# shellcheck shell=bash
                                                                                          
                                                                                             
#
                                                                                                
                                                                                              
                                                             
#
                                                         
#
                                         
                                  
#
                                                                                          
                                                                                     
                                                                                      
                                                                                                        
                                                                                             
                                                                                                
                                                                                                 
                                                                                           
#
                                                                                                 
                            
#
                                                                                  
#
                                                                                                 
                                                                                                  
                                                                                              
                
                                                                                                 
                                                                                         
                                                                                            
                     
#
                                                                                          

if [ -n "${GIDEON_PY:-}" ]; then
  PY="$GIDEON_PY"
else
                                                                                             
  PY="$REPO_ROOT/.venv/bin/python"
  if [ ! -x "$PY" ]; then
                                                                                 
    _common_dir="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
    if [ -n "$_common_dir" ]; then
                                                                                                
                                                                                           
      _primary="$(cd "$REPO_ROOT" && cd "$(dirname "$_common_dir")" 2>/dev/null && pwd || true)"
      [ -n "$_primary" ] && PY="$_primary/.venv/bin/python"
    fi
    unset _common_dir _primary
  fi
fi

if [ ! -x "$PY" ]; then
  {
    echo "harness smoke: no usable Python interpreter."
    echo
    echo "  GIDEON_PY is unset (or not executable), and no .venv/bin/python was found in"
    echo "    this checkout:      $REPO_ROOT"
    echo "    the primary one:    resolved via 'git rev-parse --git-common-dir'"
    echo
    echo "  A bare 'python3' is NOT used as a fallback on purpose: it lacks this repo's dev"
    echo "  dependencies, so the exemplar would fail on a missing module instead of on this."
    echo
    echo "  In a git worktree, point GIDEON_PY at the primary checkout's venv:"
    echo "    GIDEON_PY=/path/to/primary-checkout/.venv/bin/python \\"
    echo "      bash ${BASH_SOURCE[1]:-checks/harness/exemplars/slice_N/smoke.sh}"
    echo
    echo "  Or create one here:  uv sync --locked --extra dev"
  } >&2
  exit 1
fi
