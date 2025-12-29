#!/bin/bash

set -euo pipefail

CONTAINER_NAME="arch-box-zash"
PACKAGE_NAME="zashterminal"
ARCH_IMAGE="${ARCH_IMAGE:-docker.io/library/archlinux:latest}"

log() { echo -e "[$(date +%H:%M:%S)] $*"; }

abort_if_live() {
  if grep -qi "live" /etc/hostname 2>/dev/null || grep -qi "live" /proc/cmdline 2>/dev/null; then
    echo "Live media detected. Please install on a persistent system and try again."
    exit 1
  fi
}

ensure_tools() {
  if command -v distrobox >/dev/null 2>&1 && command -v podman >/dev/null 2>&1; then
    return
  fi

  # Detect distro (best-effort)
  . /etc/os-release
  PKG_INSTALL=""

  case "${ID_LIKE:-$ID}" in
    *debian*|*ubuntu*)
      PKG_INSTALL="sudo apt update && sudo apt install -y podman distrobox"
      ;;
    *fedora*|*rhel*|*centos*)
      PKG_INSTALL="sudo dnf install -y podman distrobox"
      ;;
    *suse*)
      PKG_INSTALL="sudo zypper install -y podman distrobox"
      ;;
    *arch*)
      PKG_INSTALL="sudo pacman -Syu --needed --noconfirm podman distrobox"
      ;;
    *)
      echo "Could not detect the distro to install podman/distrobox."
      echo "Please install podman and distrobox manually and run this script again."
      exit 1
      ;;
  esac

  log "Installing podman and distrobox..."
  eval "$PKG_INSTALL"

  # Fallback: if distrobox is still missing (e.g., Alma/RHEL repos), install from upstream script
  if ! command -v distrobox >/dev/null 2>&1; then
    log "distrobox not found in repo. Installing from upstream script..."
    curl -fsSL https://raw.githubusercontent.com/89luca89/distrobox/main/install | sudo sh
  fi

  if ! command -v distrobox >/dev/null 2>&1 || ! command -v podman >/dev/null 2>&1; then
    echo "Failed to install podman/distrobox automatically. Please install them manually and rerun."
    exit 1
  fi
}

create_container() {
  if distrobox ls | grep -q "$CONTAINER_NAME"; then
    log "Container $CONTAINER_NAME already exists."
    return
  fi
  log "Creating container $CONTAINER_NAME based on $ARCH_IMAGE..."
  distrobox create --name "$CONTAINER_NAME" --image "$ARCH_IMAGE" --yes
}

cleanup_container() {
  log "Cleaning up container and exported app..."
  distrobox-export --app "$PACKAGE_NAME" --delete 2>/dev/null || true
  distrobox rm --force --name "$CONTAINER_NAME" 2>/dev/null || true
}

install_in_container() {
  log "Setting up Arch environment and installing $PACKAGE_NAME from AUR..."
  HOST_LANG="${LANG:-en_US.UTF-8}"
  distrobox enter "$CONTAINER_NAME" -- env LANG_HOST="$HOST_LANG" bash <<'INBOX'
set -euo pipefail

# Ensure locales to avoid warnings when host uses pt_BR or other UTF-8 locales
sudo pacman -Syu --needed --noconfirm glibc base-devel git
if [ -n "${LANG_HOST:-}" ]; then
  sudo sed -i "s/^#\\(${LANG_HOST} UTF-8\\)/\\1/" /etc/locale.gen || true
fi
sudo sed -i "s/^#\\(en_US.UTF-8 UTF-8\\)/\\1/" /etc/locale.gen || true
sudo sed -i "s/^#\\(pt_BR.UTF-8 UTF-8\\)/\\1/" /etc/locale.gen || true
sudo locale-gen || true
echo "LANG=${LANG_HOST:-en_US.UTF-8}" | sudo tee /etc/locale.conf >/dev/null
export LANG=${LANG_HOST:-en_US.UTF-8}
export LC_ALL=${LANG_HOST:-en_US.UTF-8}

if ! command -v yay >/dev/null 2>&1; then
  echo "Installing yay-bin..."
  rm -rf /tmp/yay-bin
  git clone https://aur.archlinux.org/yay-bin.git /tmp/yay-bin
  cd /tmp/yay-bin && makepkg -si --noconfirm
fi

echo "Installing zashterminal..."
yay -S --noconfirm zashterminal

echo "Exporting application to the host..."
distrobox-export --app zashterminal
INBOX
}

log "Starting automation for $PACKAGE_NAME"
abort_if_live
ensure_tools

success=0
for attempt in 1 2 3; do
  log "Attempt $attempt/3"
  create_container
  if install_in_container; then
    success=1
    break
  else
    log "Attempt $attempt failed; cleaning up and retrying..."
    cleanup_container
  fi
done

if [ "$success" -eq 1 ]; then
  log "Done. You can run '$PACKAGE_NAME' directly on the host."
else
  log "Installation failed after 3 attempts. Rolling back."
  cleanup_container
  exit 1
fi