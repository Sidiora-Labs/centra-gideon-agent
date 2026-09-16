#!/bin/sh
                                                                                     
                                        
                           
# The local core.hooksPath wins over any
                                                       
#
                                                                            
                                                                                
                                                                                   
                                                                                
                           
set -eu

                                                     
if [ -n "${CI:-}" ]; then
  exit 0
fi

command -v git >/dev/null 2>&1 || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

repository_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repository_root" || exit 0

                                                                 
[ -d tooling/hooks ] || exit 0

git config --local core.hooksPath tooling/hooks

if [ "$(git config --local --get core.hooksPath)" != "tooling/hooks" ]; then
  echo "Failed to configure the repository-owned Git hooks." >&2
  exit 1
fi

echo "Installed repository Git hooks from tooling/hooks (pre-commit lint, DCO sign-off, pre-push)."
