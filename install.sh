#!/bin/bash

set -euo pipefail

CONTAINER_NAME="arch-zashterminal"
PACKAGE_NAME="zashterminal"
DESKTOP_ID="org.leoberbert.zashterminal"
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

  . /etc/os-release
  case "${ID_LIKE:-$ID}" in
    *debian*|*ubuntu*) PKG_INSTALL="sudo apt update && sudo apt install -y podman distrobox" ;;
    *fedora*|*rhel*|*centos*) PKG_INSTALL="sudo dnf install -y podman distrobox" ;;
    *suse*) PKG_INSTALL="sudo zypper install -y podman distrobox" ;;
    *arch*) PKG_INSTALL="sudo pacman -Syu --needed --noconfirm podman distrobox" ;;
    *) echo "Distro not supported for auto-install."; exit 1 ;;
  esac

  log "Installing tools..."
  eval "$PKG_INSTALL"
}

create_container() {
  if distrobox ls | grep -q "$CONTAINER_NAME"; then
    log "Container $CONTAINER_NAME already exists."
    return
  fi
  log "Creating container $CONTAINER_NAME..."
  distrobox create --name "$CONTAINER_NAME" --image "$ARCH_IMAGE" --yes
}

cleanup_container() {
  log "Cleaning up..."
  distrobox enter "$CONTAINER_NAME" -- distrobox-export --app "$DESKTOP_ID" --delete >/dev/null 2>&1 || true
  distrobox rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

install_in_container() {
  log "Installing $PACKAGE_NAME from AUR..."
  HOST_LANG="${LANG:-en_US.UTF-8}"
  distrobox enter "$CONTAINER_NAME" -- env LANG_HOST="$HOST_LANG" DESKTOP_ID="$DESKTOP_ID" bash <<'INBOX'
set -euo pipefail

sudo pacman -Syu --needed --noconfirm glibc base-devel git

# Locales
sudo sed -i "s/^#\(${LANG_HOST} UTF-8\)/\1/" /etc/locale.gen || true
sudo sed -i "s/^#\(en_US.UTF-8 UTF-8\)/\1/" /etc/locale.gen || true
sudo locale-gen || true

if ! command -v yay >/dev/null 2>&1; then
  rm -rf /tmp/yay-bin
  git clone https://aur.archlinux.org/yay-bin.git /tmp/yay-bin
  cd /tmp/yay-bin && makepkg -si --noconfirm
fi

yay -S --noconfirm zashterminal

echo "Exporting application..."
# Exporta usando o ID completo do desktop
distrobox-export --app "$DESKTOP_ID"
INBOX
}

log "Starting automation for $PACKAGE_NAME"
abort_if_live
ensure_tools

success=0
for attempt in 1 2 3; do
  log "Attempt $attempt/3"
  
  if distrobox ls | grep -q "$CONTAINER_NAME"; then
      cleanup_container
  fi

  create_container
  if install_in_container; then
    success=1
    break
  else
    log "Attempt $attempt failed."
    cleanup_container
  fi
done

if [ "$success" -eq 1 ]; then
  log "Success! Refreshing desktop database..."
  update-desktop-database ~/.local/share/applications 2>/dev/null || true
  log "Done. You can now open Zash Terminal from your menu."
else
  log "Installation failed after 3 attempts."
  exit 1
fi