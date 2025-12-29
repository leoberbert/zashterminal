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
  PKG_INSTALL=""

  case "${ID_LIKE:-$ID}" in
    *debian*|*ubuntu*) PKG_INSTALL="sudo apt update && sudo apt install -y podman distrobox" ;;
    *fedora*|*rhel*|*centos*) PKG_INSTALL="sudo dnf install -y podman distrobox" ;;
    *suse*) PKG_INSTALL="sudo zypper install -y podman distrobox" ;;
    *arch*) PKG_INSTALL="sudo pacman -Syu --needed --noconfirm podman distrobox" ;;
    *)
      echo "Could not detect the distro to install podman/distrobox."
      exit 1
      ;;
  esac

  log "Installing podman and distrobox..."
  eval "$PKG_INSTALL"

  if ! command -v distrobox >/dev/null 2>&1; then
    log "distrobox not found in repo. Installing from upstream script..."
    curl -fsSL https://raw.githubusercontent.com/89luca89/distrobox/main/install | sudo sh
  fi
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
  # Tenta deletar usando o ID completo
  distrobox-export --app "$DESKTOP_ID" --delete 2>/dev/null || true
  distrobox rm --force --name "$CONTAINER_NAME" 2>/dev/null || true
}

install_in_container() {
  log "Installing $PACKAGE_NAME from AUR..."
  HOST_LANG="${LANG:-en_US.UTF-8}"
  
  # Passamos as variáveis para dentro do heredoc
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
# O segredo está em usar o nome exato do .desktop sem a extensão
distrobox-export --app "$DESKTOP_ID"
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
    log "Attempt $attempt failed; retrying..."
    cleanup_container
  fi
done

if [ "$success" -eq 1 ]; then
  log "Success! Update your host desktop database..."
  update-desktop-database ~/.local/share/applications 2>/dev/null || true
  log "Done."
else
  log "Installation failed."
  cleanup_container
  exit 1
fi