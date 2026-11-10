#!/usr/bin/env bash
# Install Zashterminal inside a Distrobox container (works on any distro).
#
# Creates (or reuses) an Arch container, runs the regular install.sh inside it,
# and exports the app to the host menu. Local tabs then open the HOST shell via
# distrobox-host-exec. install.sh itself is not modified or duplicated here.
#
#   curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install-distrobox.sh | bash

set -euo pipefail

PACKAGE_NAME="zashterminal"
DESKTOP_FILE="/usr/share/applications/org.leoberbert.zashterminal.desktop"
DISTROBOX_NAME="${DISTROBOX_NAME:-${PACKAGE_NAME}}"
DISTROBOX_IMAGE="${DISTROBOX_IMAGE:-docker.io/library/archlinux:latest}"
INSTALL_SH_URL="${INSTALL_SH_URL:-https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh}"

log() { echo "[$(date +%H:%M:%S)] $*"; }
warn() { echo "[$(date +%H:%M:%S)] WARNING: $*" >&2; }
die() { echo "[$(date +%H:%M:%S)] ERROR: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: install-distrobox.sh [-h|--help]

Installs ${PACKAGE_NAME} in a Distrobox container and exports it to the host menu.

Environment:
  DISTROBOX_NAME    container name   (default: ${PACKAGE_NAME})
  DISTROBOX_IMAGE   container image  (default: ${DISTROBOX_IMAGE})
  INSTALL_SH_URL    install.sh to run inside the container
  ZASH_NO_EXPORT=1  skip distrobox-export
EOF
}

in_distrobox() {
  [ -n "${CONTAINER_ID:-}" ] && [ -e /run/.containerenv ]
}

# Directory of this script if it sits in a source checkout, else fail.
local_checkout_dir() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
  if [ -f "${dir}/install.sh" ] && [ -f "${dir}/pyproject.toml" ] && [ -d "${dir}/src/zashterminal" ]; then
    echo "${dir}"
    return 0
  fi
  return 1
}

# Runs inside the container.
inner_install() {
  # Fresh Arch images have no git; install.sh needs it to fetch the source.
  if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    log "Installing git and curl in the container..."
    sudo pacman -Syu --needed --noconfirm git curl
  fi

  local checkout
  if checkout="$(local_checkout_dir)"; then
    bash "${checkout}/install.sh"
  else
    curl -fsSL "${INSTALL_SH_URL}" | bash
  fi

  if [ -n "${ZASH_NO_EXPORT:-}" ]; then
    return 0
  fi
  if ! command -v distrobox-export >/dev/null 2>&1; then
    warn "distrobox-export not found; run manually: distrobox-export --app ${DESKTOP_FILE}"
    return 0
  fi
  # Export by full path: `--app zashterminal` is a substring match that also hits
  # the host's auto-generated "enter container" entry (shared $HOME), exporting a
  # broken launcher and overriding the app icon with the Arch logo.
  log "Exporting ${PACKAGE_NAME} to the host application menu..."
  distrobox-export --app "${DESKTOP_FILE}" || \
    warn "distrobox-export failed; run manually: distrobox-export --app ${DESKTOP_FILE}"
  if command -v distrobox-host-exec >/dev/null 2>&1; then
    log "Local tabs will open your host shell via distrobox-host-exec."
  else
    warn "distrobox-host-exec not found: local tabs will use the container shell."
  fi
}

# Runs on the host.
host_install() {
  command -v distrobox >/dev/null 2>&1 || \
    die "distrobox not found. Install it first: https://distrobox.it/#installation"

  if distrobox list 2>/dev/null \
    | awk -F '|' 'NR > 1 { gsub(/[[:space:]]/, "", $2); print $2 }' \
    | grep -qx "${DISTROBOX_NAME}"; then
    log "Reusing Distrobox '${DISTROBOX_NAME}'."
  else
    log "Creating Distrobox '${DISTROBOX_NAME}' from ${DISTROBOX_IMAGE}..."
    distrobox create --yes --name "${DISTROBOX_NAME}" --image "${DISTROBOX_IMAGE}" </dev/null
  fi

  log "Installing inside '${DISTROBOX_NAME}' (first start initializes the container)..."
  local checkout
  # </dev/null: under `curl | bash`, stdin is this script.
  if checkout="$(local_checkout_dir)"; then
    distrobox enter --name "${DISTROBOX_NAME}" -- \
      env ZASH_NO_EXPORT="${ZASH_NO_EXPORT:-}" INSTALL_SH_URL="${INSTALL_SH_URL}" \
      bash "${checkout}/install-distrobox.sh" </dev/null
  else
    distrobox enter --name "${DISTROBOX_NAME}" -- \
      env ZASH_NO_EXPORT="${ZASH_NO_EXPORT:-}" INSTALL_SH_URL="${INSTALL_SH_URL}" \
      bash -c 'curl -fsSL "$1" | bash' _ "${INSTALL_SH_URL%/install.sh}/install-distrobox.sh" </dev/null
  fi

  log "Done. Find Zashterminal in your application menu (container '${DISTROBOX_NAME}')."
}

main() {
  case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    "") ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac

  if in_distrobox; then
    inner_install
  else
    host_install
  fi
}

main "$@"
