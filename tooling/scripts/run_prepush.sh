#!/bin/sh
                                                                          
                      
#
                                                                         
                                                                              
                                                                                
                                                                              
                                                                            
                                                                                          
#
                                                                                
                                                                                  
                                                                               
                                                                               
#
                                                                                
                                                                                 
                        
#
                                                                               
                                                                                 
                                                                                 
                                                                       
                                                                               
                                                                                  
                                                                             
                                                                                  
                                                                               
                                                                               
                                                                                   
                                                                                    
                                                                                
                                                                
#
                                                                         
                                                                                  
set -eu

repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"

FRONTEND_PATHS="apps/console package.json package-lock.json tooling/scripts/render_smoke.mjs tooling/scripts/run_prepush.sh"
PYTHON_PATHS="runtime/gideon checks/runtime checks/harness pyproject.toml"
ZERO=0000000000000000000000000000000000000000

needs_gate=0
needs_lint=0
if [ -t 0 ]; then
                                                                             
                                                                                
                                                                                
                                                                                
  needs_gate=1
  needs_lint=1
fi
head_commit=$(git rev-parse --verify HEAD 2>/dev/null || echo unknown)
while [ ! -t 0 ] && read -r local_ref local_sha _remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue                                      
                                                                                 
                                                                                  
                                                                                  
  pushed_commit=$(git rev-parse --verify --quiet "$local_sha^{commit}" || echo "$local_sha")
  if [ "$pushed_commit" != "$head_commit" ]; then
    echo "" >&2
    echo "pre-push: refusing to gate a tree that is not what you are pushing." >&2
    echo "          $local_ref resolves to $pushed_commit" >&2
    echo "          this worktree's HEAD is $head_commit" >&2
    echo "          Both halves of this gate check the working tree, not the pushed" >&2
    echo "          commits, so running them here would prove nothing about what ships." >&2
    echo "          Fix: push from the worktree that has that ref checked out — see" >&2
    echo "          'git worktree list' — and push one ref per worktree rather than" >&2
    echo "          batching several refs into one push." >&2
    exit 1
  fi
                                                                                      
                                                                                        
                                                                                       
                                                                                      
                                                                                       
                                                                                        
                                                                                      
                                                                                      
                                        
  #
                                                                                        
                                                                                          
                                                                                           
                                       
  if base=$(git merge-base "$local_sha" origin/main 2>/dev/null); then
    range="$base..$local_sha"
  elif [ "$remote_sha" != "$ZERO" ]; then
                                                                                     
                                                                                    
                                                                                 
    range="$remote_sha..$local_sha"
  else
                                                                                          
    needs_gate=1
    needs_lint=1
    continue
  fi
  # shellcheck disable=SC2086 — FRONTEND_PATHS is a deliberate word list
  if [ -n "$(git diff --name-only "$range" -- $FRONTEND_PATHS 2>/dev/null || echo changed)" ]; then
    needs_gate=1
  fi
  # shellcheck disable=SC2086 — PYTHON_PATHS is a deliberate word list
  if [ -n "$(git diff --name-only "$range" -- $PYTHON_PATHS 2>/dev/null || echo changed)" ]; then
    needs_lint=1
  fi
done

                                                                                
                                                                             
                                                                                 
                       
if [ "$needs_lint" -eq 1 ]; then
  if [ -x .venv/bin/black ]; then
    PY_BIN=".venv/bin/"
  elif command -v black >/dev/null 2>&1 && command -v isort >/dev/null 2>&1 \
    && command -v flake8 >/dev/null 2>&1; then
    PY_BIN=""
  else
    PY_BIN="MISSING"
  fi

  if [ "$PY_BIN" = "MISSING" ]; then
    echo "pre-push: python changes outgoing but dev tools not found — skipping lint."
    echo "          Install them with: pip install -e '.[dev]'   (CI still checks.)"
  else
    echo "pre-push: python changes outgoing — checking lint (black, isort, flake8)."
    if ! "${PY_BIN}black" --check --quiet runtime/gideon checks/runtime checks/harness \
      || ! "${PY_BIN}isort" --check-only --quiet runtime/gideon checks/runtime checks/harness \
      || ! "${PY_BIN}flake8" runtime/gideon checks/runtime checks/harness; then
      echo "" >&2
      echo "pre-push: lint is red — run 'make format' then 'make lint', and commit the" >&2
      echo "          result before pushing. (CI's lint job checks the same thing.)" >&2
      exit 1
    fi
    echo "pre-push: python lint green."
  fi
fi

if [ "$needs_gate" -eq 0 ]; then
  echo "pre-push: no frontend changes outgoing — render-smoke gate skipped."
  exit 0
fi

echo "pre-push: frontend changes outgoing — running the render-smoke gate"
echo "          (clean npm ci -> typecheck -> vitest -> build -> headless render)."

npm ci
npm run typecheck:web
npm run test:web
npm run build
npx playwright install chromium
npm run smoke:render

echo "pre-push: render-smoke gate green."
