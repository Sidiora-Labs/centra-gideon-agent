#!/bin/sh
# Gideon bootstrap installer. Run this file from the checkout, or supply
# GIDEON_PACKAGE_SOURCE with an explicit package spec, wheel URL, or local path.
# Publication destinations are configured by the operator; no release registry
# or hosted install endpoint is assumed. The installer is POSIX sh and keeps
# execution inside main so an incomplete download cannot execute a partial body.

set -eu

GIDEON_PACKAGE="${GIDEON_PACKAGE_SOURCE:-}"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

                                                                               
if [ -t 1 ]; then
    _bold=$(printf '\033[1m')
    _dim=$(printf '\033[2m')
    _reset=$(printf '\033[0m')
else
    _bold=''
    _dim=''
    _reset=''
fi
say() { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$_bold" "$_reset" "$*"; }
warn() { printf '%swarning:%s %s\n' "$_bold" "$_reset" "$*" >&2; }
die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

                                                                               
print_container() {
    cat <<'EOF'
Run Gideon with Docker Compose (self-hosters / Windows):

  cp .env.example .env        # optional: set provider keys / options
  docker compose -f infrastructure/compose/compose.yaml -f infrastructure/compose/compose.build.yaml up -d --build

The dashboard comes up on http://127.0.0.1:10000 with a persistent volume.
Details, backups, and updates: docs/guides/CONTAINERS.md in this checkout
EOF
}

                                                                               
detect_platform() {
    os=$(uname -s 2>/dev/null || echo unknown)
    arch=$(uname -m 2>/dev/null || echo unknown)
    case "$os" in
        Linux | Darwin) : ;;
        *)
            warn "unsupported OS '$os' — the uv path targets Linux and macOS."
            warn "On Windows, use the Docker Compose path:  sh install.sh --container"
            ;;
    esac
    say "${_dim}Detected platform: $os $arch${_reset}"
}

                                                                               
ensure_uv() {
    if have uv; then
        say "${_dim}uv already installed: $(uv --version 2>/dev/null || echo present)${_reset}"
        return 0
    fi
    step "Installing uv (Astral's Python package/tool manager)…"
                                                                                           
                                                                                          
                                                                                       
                                                                                        
                                                                                            
                                                                                         
                                                                                            
                                                                                               
                                                                                           
                                                                        
                                     
    if have curl; then
        curl -fsSL "$UV_INSTALLER_URL" | sh
    elif have wget; then
        wget -qO- "$UV_INSTALLER_URL" | sh
    else
        die "need curl or wget to install uv. Install one, or install uv manually: https://docs.astral.sh/uv/"
    fi
                                                                               
                                                                       
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin" "${XDG_BIN_HOME:-}"; do
        [ -n "$d" ] && [ -d "$d" ] && case ":$PATH:" in
            *":$d:"*) : ;;
            *) PATH="$d:$PATH" ;;
        esac
    done
    export PATH
    have uv || die "uv was installed but is not on PATH. Open a new shell and re-run, or add ~/.local/bin to PATH."
}

                                                                         
resolve_package_source() {
    if [ -n "$GIDEON_PACKAGE" ]; then
        return
    fi
    installer_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    checkout=$(CDPATH='' cd -- "$installer_dir/../.." && pwd)
    if [ -f "$checkout/pyproject.toml" ]; then
        GIDEON_PACKAGE="$checkout"
    else
        die "set GIDEON_PACKAGE_SOURCE to a Gideon wheel, checkout path, or versioned package specification"
    fi
}

install_gideon() {
    step "Installing Gideon from $GIDEON_PACKAGE with uv…"
    # An explicit source avoids installing an unrelated package with a similar name.
    # Versioned release specifications are supplied by the configured publisher.
    uv tool install --upgrade "$GIDEON_PACKAGE"
    have gideon || {
        warn "gideon installed but not yet on PATH."
        warn "Run 'uv tool update-shell' (or open a new shell), then 'gideon setup'."
        return 0
    }
}

                                                                               
offer_setup() {
    have gideon || return 0
    say ""
    step "Installed. Next steps:"
    say "  gideon setup      # configure your workspace directory + timezone"
    say "  gideon gateway    # start the dashboard on http://localhost:10000"
    say ""
                                                                                    
    if [ -t 0 ]; then
                                                                                           
                                                                                         
                                                                                      
                                                                              
                                                                                           
                                                                     
        # shellcheck disable=SC2016
        printf 'Run `gideon setup` now? [y/N] '
        read -r reply || reply=n
        case "$reply" in
            [Yy] | [Yy][Ee][Ss]) exec gideon setup ;;
            *) say "Skipped. Run 'gideon setup' when you're ready." ;;
        esac
    else
        say "${_dim}(non-interactive install — run 'gideon setup' yourself.)${_reset}"
    fi
}

main() {
    for arg in "$@"; do
        case "$arg" in
            --container)
                print_container
                exit 0
                ;;
            -h | --help)
                say "Usage: install.sh [--container]"
                say "  (no args)    install Gideon from this checkout or GIDEON_PACKAGE_SOURCE via uv"
                say "  --container  print the Docker Compose snippet instead"
                exit 0
                ;;
            *) die "unknown argument: $arg (try --help)" ;;
        esac
    done

    say "${_bold}Gideon installer${_reset}"
    detect_platform
    resolve_package_source
    ensure_uv
    install_gideon
    offer_setup
}

main "$@"
