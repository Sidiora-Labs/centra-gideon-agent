#!/bin/sh
                                                                               
#
                                                                                
                                                                             
                                                                              
                                                                                
                                                                         
#
                                                                         
                                                                           
                                                                             
                                                                    
#
                                                                      
                                                     
#
                                                                             
                                                                  
set -eu

repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"

                                                                         
                                                       
staged_py=$(git diff --cached --name-only --diff-filter=ACMR -z -- '*.py' | tr '\0' '\n')

if [ -z "$staged_py" ]; then
  exit 0
fi

echo "pre-commit: formatting + linting staged Python files"

                                                                                
                                                                                
                                                                               
                                                                                 
resolve() {
  if [ -x ".venv/bin/$1" ]; then
    echo ".venv/bin/$1"
  elif command -v "$1" >/dev/null 2>&1; then
    echo "$1"
  else
    echo "pre-commit: '$1' not found — install dev deps (pip install -e '.[dev]')" >&2
    echo "            or run 'make lint' manually, then commit." >&2
    exit 1
  fi
}

BLACK=$(resolve black)
ISORT=$(resolve isort)
FLAKE8=$(resolve flake8)

                                                                                
                                                                                
                                           
printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 "$BLACK" --quiet
printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 "$ISORT"
printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 git add

                                                                                
                                                                         
                                                      
if ! printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 "$FLAKE8"; then
  echo "" >&2
  echo "pre-commit: flake8 found issues that can't be auto-fixed (see above)." >&2
  echo "            Fix them and re-commit, or 'git commit --no-verify' to skip." >&2
  exit 1
fi

echo "pre-commit: staged Python is formatted and lint-clean."
