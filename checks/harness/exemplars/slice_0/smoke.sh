#!/usr/bin/env bash
                                                                                       
                                                                        
#
                                                                                
                                                                 
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO_ROOT"

                                                                                
# shellcheck source-path=SCRIPTDIR/..
source "$REPO_ROOT/harness/exemplars/resolve_py.sh"

HOME_DIR="$(mktemp -d)"
trap 'rm -rf "$HOME_DIR"' EXIT

GIDEON_HOME="$HOME_DIR" "$PY" -m checks.harness.exemplars.slice_0.exemplar
