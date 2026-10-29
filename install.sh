#!/usr/bin/env bash

set -euo pipefail

PACKAGE_NAME="zashterminal"
DESKTOP_ID="org.leoberbert.zashterminal"
REPO_URL="https://github.com/leoberbert/zashterminal.git"

# Overridable for local/container testing (e.g. INSTALL_ROOT=/tmp/zash-test).
INSTALL_ROOT="${INSTALL_ROOT:-/opt/${PACKAGE_NAME}}"
VENV_DIR="${VENV_DIR:-${INSTALL_ROOT}/venv}"
BIN_PATH="${BIN_PATH:-/usr/local/bin/${PACKAGE_NAME}}"
APP_DIR="${APP_DIR:-/usr/share/applications}"
ICON_DIR="${ICON_DIR:-/usr/share/icons/hicolor/scalable/apps}"
PIXMAP_DIR="${PIXMAP_DIR:-/usr/share/pixmaps}"
LOCALE_BASE_DIR="${LOCALE_BASE_DIR:-/usr/share/locale}"
# Gentoo: set GENTOO_USE_BINHOST=0 to force source builds.
GENTOO_USE_BINHOST="${GENTOO_USE_BINHOST:-1}"
GENTOO_BINHOST_DEFAULT="https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64/"

DISTRO_FAMILY=""
PKG_MANAGER=""
INSTALL_MODE="${INSTALL_MODE:-auto}"   # auto | local | aur
ARCH_AUR_HELPER="${ARCH_AUR_HELPER:-}" # yay | paru
NIX_FEATURES="nix-command flakes"

log() { echo "[$(date +%H:%M:%S)] $*"; }
warn() { echo "[$(date +%H:%M:%S)] WARNING: $*" >&2; }
die() { echo "[$(date +%H:%M:%S)] ERROR: $*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

detect_system() {
  [ -r /etc/os-release ] || die "/etc/os-release not found; unsupported Linux distribution."
  # shellcheck disable=SC1091
  . /etc/os-release

  local key="${ID_LIKE:-} ${ID:-}"
  case " ${key} " in
    *" arch "*|*" manjaro "*)
      DISTRO_FAMILY="arch"
      PKG_MANAGER="pacman"
      ;;
    *" ubuntu "*|*" debian "*|*" linuxmint "*|*" pop "*)
      DISTRO_FAMILY="debian"
      PKG_MANAGER="apt"
      ;;
    *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" alma "*)
      DISTRO_FAMILY="fedora"
      PKG_MANAGER="dnf"
      ;;
    *" opensuse "*|*" suse "*)
      DISTRO_FAMILY="suse"
      PKG_MANAGER="zypper"
      ;;
    *" nixos "*)
      DISTRO_FAMILY="nixos"
      PKG_MANAGER="nix"
      ;;
    *" gentoo "*|*" calculate "*|*" funtoo "*)
      DISTRO_FAMILY="gentoo"
      PKG_MANAGER="emerge"
      ;;
    *)
      die "Unsupported distro (${ID:-unknown}). Add package mapping in install.sh."
      ;;
  esac

  log "Detected distro: ${PRETTY_NAME:-${ID:-unknown}} (${DISTRO_FAMILY}, ${PKG_MANAGER})"
}

pkg_update_once() {
  case "$PKG_MANAGER" in
    apt) sudo apt update ;;
    pacman) sudo pacman -Syu --noconfirm ;;
    dnf|zypper|emerge) : ;;
    *) die "Unsupported package manager: $PKG_MANAGER" ;;
  esac
}

ensure_gentoo_package_use() {
  # USE flags required for the GTK4 / VTE / libsecret dependency tree.
  local use_file="/etc/portage/package.use/zashterminal"
  log "Writing Gentoo package.use: ${use_file}"
  sudo mkdir -p /etc/portage/package.use
  sudo tee "${use_file}" >/dev/null <<'EOF'
# Managed by zashterminal install.sh — GTK4 / VTE / libsecret stack
media-libs/freetype harfbuzz
x11-libs/cairo X
media-libs/libglvnd X
app-crypt/gcr:0 gtk
gui-libs/gtk X
gui-libs/vte introspection
app-crypt/libsecret introspection
dev-libs/gobject-introspection introspection
EOF
}

configure_gentoo_binhost() {
  [ "${GENTOO_USE_BINHOST}" = "1" ] || return 0
  case "$(uname -m)" in
    x86_64|amd64) ;;
    *)
      warn "Gentoo binhost helper is only auto-configured for amd64; emerging from source."
      return 0
      ;;
  esac
  if [ -z "${PORTAGE_BINHOST:-}" ]; then
    export PORTAGE_BINHOST="${GENTOO_BINHOST_DEFAULT}"
    log "Using Gentoo binhost: ${PORTAGE_BINHOST}"
  else
    log "Using existing PORTAGE_BINHOST: ${PORTAGE_BINHOST}"
  fi
  # Prefer binary packages when the binhost has a matching build.
  export FEATURES="${FEATURES:-} getbinpkg binpkg-multi-instance"
}

emerge_install_packages() {
  local packages=("$@")
  local emerge_opts=(--ask=n --quiet-build=y --noreplace --jobs="$(nproc)")
  if [ "${GENTOO_USE_BINHOST}" = "1" ] && [ -n "${PORTAGE_BINHOST:-}" ]; then
    emerge_opts+=(--getbinpkg)
  fi
  sudo emerge "${emerge_opts[@]}" "${packages[@]}"
}

install_pkg_group() {
  local group_name="$1"
  local fail_on_error="$2"
  shift 2
  local packages=("$@")
  local failed=()

  [ "${#packages[@]}" -gt 0 ] || return 0
  log "Installing ${group_name} packages (${#packages[@]})..."

  if [ "$fail_on_error" != "true" ]; then
    for pkg in "${packages[@]}"; do
      case "$PKG_MANAGER" in
        apt) sudo apt install -y "$pkg" >/dev/null 2>&1 || failed+=("$pkg") ;;
        pacman) sudo pacman -S --needed --noconfirm "$pkg" >/dev/null 2>&1 || failed+=("$pkg") ;;
        dnf) sudo dnf install -y "$pkg" >/dev/null 2>&1 || failed+=("$pkg") ;;
        zypper) sudo zypper --non-interactive install "$pkg" >/dev/null 2>&1 || failed+=("$pkg") ;;
        emerge) emerge_install_packages "$pkg" >/dev/null 2>&1 || failed+=("$pkg") ;;
        *) die "Unsupported package manager: $PKG_MANAGER" ;;
      esac
    done
    if [ "${#failed[@]}" -gt 0 ]; then
      warn "Optional packages not installed for ${DISTRO_FAMILY}: ${failed[*]}"
    fi
    return 0
  fi

  case "$PKG_MANAGER" in
    apt) sudo apt install -y "${packages[@]}" || failed=("${packages[@]}") ;;
    pacman) sudo pacman -S --needed --noconfirm "${packages[@]}" || failed=("${packages[@]}") ;;
    dnf) sudo dnf install -y "${packages[@]}" || failed=("${packages[@]}") ;;
    zypper) sudo zypper --non-interactive install "${packages[@]}" || failed=("${packages[@]}") ;;
    emerge) emerge_install_packages "${packages[@]}" || failed=("${packages[@]}") ;;
    *) die "Unsupported package manager: $PKG_MANAGER" ;;
  esac

  if [ "${#failed[@]}" -gt 0 ]; then
    die "Failed to install ${group_name} packages. Review package names for ${DISTRO_FAMILY}."
  fi
}

install_system_dependencies() {
  local base_packages=()
  local python_runtime_packages=()
  local optional_python_packages=()

  case "$DISTRO_FAMILY" in
    arch)
      base_packages=(
        python python-pip git rsync sshpass gettext imagemagick
        gtk4 libadwaita vte4 libsecret
        gobject-introspection python-gobject python-cairo
      )
      python_runtime_packages=(
        python-requests python-psutil python-regex python-pygments
        python-py7zr python-setproctitle python-pycryptodomex
      )
      ;;
    debian)
      base_packages=(
        python3 python3-venv python3-pip git rsync sshpass gettext imagemagick
        libgtk-4-1 libadwaita-1-0 libvte-2.91-gtk4-0 libsecret-1-0
        gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 gir1.2-secret-1
        python3-gi python3-gi-cairo python3-cairo
      )
      python_runtime_packages=(
        python3-requests python3-psutil python3-regex python3-pygments
      )
      optional_python_packages=(
        python3-py7zr python3-setproctitle
      )
      ;;
    fedora)
      base_packages=(
        python3 python3-pip git rsync sshpass gettext ImageMagick
        gtk4 libadwaita vte291-gtk4 libsecret gobject-introspection
        python3-gobject python3-cairo
      )
      # python3-pycryptodomex is required (SecureCRT V2 / session crypto).
      # python3-py7zr is not packaged on Fedora; install via pip in the venv.
      python_runtime_packages=(
        python3-requests python3-psutil python3-pygments python3-pycryptodomex
      )
      optional_python_packages=(
        python3-regex python3-setproctitle
      )
      ;;
    suse)
      base_packages=(
        python3 python3-pip git rsync sshpass gettext-tools ImageMagick
        gtk4 libadwaita-1-0 libvte-2_91-0 libsecret-1-0
        typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 typelib-1_0-Vte-3_91 typelib-1_0-Secret-1
        python3-gobject python3-cairo
      )
      python_runtime_packages=(
        python3-requests python3-psutil python3-regex python3-Pygments
      )
      optional_python_packages=(
        python3-py7zr python3-setproctitle
      )
      ;;
    gentoo)
      # gui-libs/vte is the GTK4 slot (2.91-gtk4). x11-libs/vte is GTK3 only.
      # pycryptodomex is not packaged on Gentoo (only pycryptodome/Crypto);
      # install.sh installs Cryptodome via pip into the venv.
      # gsettings-desktop-schemas is required by libadwaita/GTK (wm.preferences).
      base_packages=(
        dev-lang/python
        dev-vcs/git
        net-misc/rsync
        net-misc/sshpass
        sys-devel/gettext
        media-gfx/imagemagick
        gui-libs/gtk
        gui-libs/libadwaita
        gui-libs/vte
        app-crypt/libsecret
        gnome-base/gsettings-desktop-schemas
        dev-libs/gobject-introspection
        dev-python/pygobject
        dev-python/pycairo
      )
      python_runtime_packages=(
        dev-python/requests
        dev-python/psutil
        dev-python/pygments
      )
      optional_python_packages=(
        dev-python/regex
        dev-python/setproctitle
      )
      ;;
    *)
      die "No dependency mapping for distro family: $DISTRO_FAMILY"
      ;;
  esac

  pkg_update_once
  if [ "${DISTRO_FAMILY}" = "gentoo" ]; then
    ensure_gentoo_package_use
    configure_gentoo_binhost
  fi
  install_pkg_group "required" "true" "${base_packages[@]}"
  install_pkg_group "python-runtime" "true" "${python_runtime_packages[@]}"
  install_pkg_group "python-optional" "false" "${optional_python_packages[@]}"
}

ensure_runtime_prereqs() {
  require_cmd sudo
  if command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    return 0
  fi
  die "Python is not available after dependency installation."
}

python_cmd() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
  else
    echo "python"
  fi
}

choose_arch_aur_helper() {
  if [ -n "${ARCH_AUR_HELPER}" ]; then
    command -v "${ARCH_AUR_HELPER}" >/dev/null 2>&1 || \
      die "ARCH_AUR_HELPER=${ARCH_AUR_HELPER} not found in PATH."
    echo "${ARCH_AUR_HELPER}"
    return 0
  fi

  if command -v paru >/dev/null 2>&1; then
    echo "paru"
    return 0
  fi
  if command -v yay >/dev/null 2>&1; then
    echo "yay"
    return 0
  fi
  return 1
}

resolve_install_mode() {
  case "${INSTALL_MODE}" in
    auto|local|aur) ;;
    *)
      die "Invalid INSTALL_MODE='${INSTALL_MODE}'. Use: auto | local | aur"
      ;;
  esac

  if [ "${DISTRO_FAMILY}" = "nixos" ]; then
    log "Install mode: nix-profile (flake)"
    return 0
  fi

  if [ "${DISTRO_FAMILY}" != "arch" ]; then
    log "Install mode: local (AUR mode only applies to Arch/Manjaro)"
    return 0
  fi

  if [ "${INSTALL_MODE}" = "aur" ]; then
    local helper
    helper="$(choose_arch_aur_helper)" || die "INSTALL_MODE=aur requires yay or paru."
    log "Install mode: aur (${helper})"
    return 0
  fi

  if [ "${INSTALL_MODE}" = "local" ]; then
    log "Install mode: local"
    return 0
  fi

  if helper="$(choose_arch_aur_helper)"; then
    log "Install mode: aur (${helper}) [auto]"
  else
    log "Install mode: local [auto] (yay/paru not found)"
  fi
}

install_arch_via_aur() {
  local helper
  helper="$(choose_arch_aur_helper)" || die "AUR helper not found (expected yay or paru)."
  log "Installing ${PACKAGE_NAME} via AUR using ${helper}..."
  "${helper}" -S --noconfirm "${PACKAGE_NAME}"
}

nixos_features_configured() {
  if [ -r /etc/nix/nix.conf ] && \
    grep -Eq '^[[:space:]]*experimental-features[[:space:]]*=' /etc/nix/nix.conf && \
    grep -Eq '^[[:space:]]*experimental-features[[:space:]]*=.*nix-command' /etc/nix/nix.conf && \
    grep -Eq '^[[:space:]]*experimental-features[[:space:]]*=.*flakes' /etc/nix/nix.conf; then
    return 0
  fi

  if [ -r /etc/nixos/configuration.nix ] && \
    grep -Eq 'nix\.settings\.experimental-features[[:space:]]*=' /etc/nixos/configuration.nix && \
    grep -Eq 'nix\.settings\.experimental-features[[:space:]]*=.*"nix-command"' /etc/nixos/configuration.nix && \
    grep -Eq 'nix\.settings\.experimental-features[[:space:]]*=.*"flakes"' /etc/nixos/configuration.nix; then
    return 0
  fi

  return 1
}

warn_if_nixos_features_not_configured() {
  [ "${DISTRO_FAMILY}" = "nixos" ] || return 0
  if nixos_features_configured; then
    return 0
  fi

  warn "NixOS experimental features not found in system config."
  warn "Recommended permanent setting in /etc/nixos/configuration.nix:"
  warn '  nix.settings.experimental-features = [ "nix-command" "flakes" ];'
  warn "Then apply with: sudo nixos-rebuild switch"
  warn "Continuing using temporary flags: --extra-experimental-features \"${NIX_FEATURES}\""
}

prepare_source() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if [ -f "${script_dir}/pyproject.toml" ] && [ -d "${script_dir}/src/zashterminal" ]; then
    echo "$script_dir"
    return 0
  fi

  require_cmd git
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  log "Cloning ${PACKAGE_NAME} source into temporary directory..." >&2
  git clone --depth 1 "$REPO_URL" "${tmp_dir}/${PACKAGE_NAME}" >/dev/null
  echo "${tmp_dir}/${PACKAGE_NAME}"
}

install_nixos_menu_entries() {
  local profile_dir="${HOME}/.nix-profile"
  local profile_desktop="${profile_dir}/share/applications/${DESKTOP_ID}.desktop"
  local profile_icon="${profile_dir}/share/icons/hicolor/scalable/apps/${PACKAGE_NAME}.svg"
  local user_app_dir="${HOME}/.local/share/applications"
  local user_icon_dir="${HOME}/.local/share/icons/hicolor/scalable/apps"
  local user_pixmap_dir="${HOME}/.local/share/pixmaps"

  mkdir -p "${user_app_dir}" "${user_icon_dir}" "${user_pixmap_dir}"

  if [ -f "${profile_desktop}" ]; then
    install -m 644 "${profile_desktop}" "${user_app_dir}/${DESKTOP_ID}.desktop"
  fi

  if [ -f "${profile_icon}" ]; then
    install -m 644 "${profile_icon}" "${user_icon_dir}/${PACKAGE_NAME}.svg"
    install -m 644 "${profile_icon}" "${user_pixmap_dir}/${PACKAGE_NAME}.svg"
  fi

  command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "${user_app_dir}" >/dev/null 2>&1 || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true
}

install_nixos_via_flake() {
  local src_dir="$1"
  require_cmd nix

  warn_if_nixos_features_not_configured

  log "Building ${PACKAGE_NAME} via flake..."
  nix --extra-experimental-features "${NIX_FEATURES}" \
    build "${src_dir}#zashterminal"

  log "Installing ${PACKAGE_NAME} to user profile via nix profile..."
  nix --extra-experimental-features "${NIX_FEATURES}" \
    profile add "${src_dir}#zashterminal"

  log "Installing desktop/menu integration for current user..."
  install_nixos_menu_entries

  log "NixOS installation complete (user profile)."
  log "Run: ${PACKAGE_NAME}"
}

compile_locales_if_possible() {
  local src_dir="$1"
  [ -d "${src_dir}/locale" ] || return 0
  command -v msgfmt >/dev/null 2>&1 || return 0

  log "Compiling translation files (.po -> .mo)..."
  find "${src_dir}/locale" -name '*.po' -print0 | while IFS= read -r -d '' po; do
    local lang out
    lang="$(basename "${po%.po}")"
    out="${LOCALE_BASE_DIR}/${lang}/LC_MESSAGES/${PACKAGE_NAME}.mo"
    sudo mkdir -p "$(dirname "$out")"
    sudo msgfmt -o "$out" "$po" || warn "Failed to compile ${po}"
  done
}

install_python_app() {
  local src_dir="$1"
  local pybin
  pybin="$(python_cmd)"

  log "Installing application into system venv: ${VENV_DIR}"
  sudo mkdir -p "${INSTALL_ROOT}"
  sudo rm -rf "${VENV_DIR}"
  sudo "${pybin}" -m venv --system-site-packages "${VENV_DIR}"

  sudo "${VENV_DIR}/bin/python" -m pip install --upgrade pip >/dev/null

  # Install pure-Python deps BEFORE the app package. The venv uses
  # --system-site-packages for PyGObject/pycairo, so the project is installed
  # with --no-deps; installing deps first avoids pip's conflict ERROR about
  # missing pycryptodomex right after a --no-deps install.
  #
  # Required (fail the install if these cannot be satisfied):
  #   pywal16, requests, psutil, Pygments, pycryptodomex, setproctitle
  # Best-effort extras (may already come from distro packages):
  #   regex, py7zr
  log "Installing required Python packages into the venv..."
  sudo "${VENV_DIR}/bin/python" -m pip install \
    'pywal16>=3.8,<4' requests psutil Pygments pycryptodomex setproctitle \
    || die "Failed to install required Python packages into ${VENV_DIR}."

  sudo "${VENV_DIR}/bin/python" -m pip install regex py7zr >/dev/null 2>&1 || \
    warn "Optional Python packages (regex/py7zr) could not be installed in the venv (continuing)."

  log "Installing ${PACKAGE_NAME} package (no PyPI deps; using system GI + venv packages)..."
  sudo "${VENV_DIR}/bin/python" -m pip install --no-deps "${src_dir}" \
    || die "Failed to install ${PACKAGE_NAME} into ${VENV_DIR}."

  # Sanity check: crypto backend must import (system site or venv).
  if ! sudo "${VENV_DIR}/bin/python" -c "from Cryptodome.Cipher import AES" >/dev/null 2>&1; then
    die "pycryptodomex is not importable in ${VENV_DIR} (Cryptodome.Cipher.AES)."
  fi
}

install_launcher() {
  local src_dir="$1"
  log "Installing launcher to ${BIN_PATH}..."
  # Shared Distrobox/Gentoo/Nix policy lives in scripts/gdk_backend_env.sh —
  # embed a copy so the installed launcher stays self-contained.
  local probe_src="${src_dir}/scripts/gdk_backend_env.sh"
  [[ -f "${probe_src}" ]] || die "Missing GDK probe script: ${probe_src}"

  {
    echo '#!/bin/sh'
    echo '# Generated by zashterminal install.sh — GDK policy from scripts/gdk_backend_env.sh'
    # Drop the trailing auto-invoke; call explicitly after embedding.
    sed '/^zash_apply_gdk_backend_policy$/d' "${probe_src}"
    echo 'zash_apply_gdk_backend_policy'
    echo "exec \"${VENV_DIR}/bin/${PACKAGE_NAME}\" \"\$@\""
  } | sudo tee "${BIN_PATH}" >/dev/null
  sudo chmod +x "${BIN_PATH}"
}

install_desktop_files() {
  local src_dir="$1"
  sudo mkdir -p "${APP_DIR}" "${ICON_DIR}" "${PIXMAP_DIR}"

  if [ -f "${src_dir}/usr/share/applications/${DESKTOP_ID}.desktop" ]; then
    sudo install -Dm644 \
      "${src_dir}/usr/share/applications/${DESKTOP_ID}.desktop" \
      "${APP_DIR}/${DESKTOP_ID}.desktop"
  fi

  if [ -f "${src_dir}/usr/share/icons/hicolor/scalable/apps/${PACKAGE_NAME}.svg" ]; then
    sudo install -Dm644 \
      "${src_dir}/usr/share/icons/hicolor/scalable/apps/${PACKAGE_NAME}.svg" \
      "${ICON_DIR}/${PACKAGE_NAME}.svg"
    sudo install -Dm644 \
      "${src_dir}/usr/share/icons/hicolor/scalable/apps/${PACKAGE_NAME}.svg" \
      "${PIXMAP_DIR}/${PACKAGE_NAME}.svg"
  fi

  sudo update-desktop-database "${APP_DIR}" >/dev/null 2>&1 || true
  sudo gtk-update-icon-cache /usr/share/icons/hicolor >/dev/null 2>&1 || true
}

post_install_notes() {
  log "Installation complete (system-wide with venv)."
  log "  Venv: ${VENV_DIR}"
  log "  Launcher: ${BIN_PATH}"
  log "  Desktop: ${APP_DIR}/${DESKTOP_ID}.desktop"
  log "Run: ${PACKAGE_NAME}"
}

main() {
  log "Starting system-wide installation for ${PACKAGE_NAME}"
  detect_system
  resolve_install_mode

  local src_dir
  src_dir="$(prepare_source)"

  if [ "${DISTRO_FAMILY}" = "nixos" ]; then
    install_nixos_via_flake "${src_dir}"
    exit 0
  fi

  if [ "${DISTRO_FAMILY}" = "arch" ]; then
    if helper="$(choose_arch_aur_helper 2>/dev/null)" && [ "${INSTALL_MODE}" != "local" ]; then
      install_arch_via_aur
      log "Installed via AUR (${helper})."
      exit 0
    fi
  fi

  install_system_dependencies
  ensure_runtime_prereqs

  install_python_app "${src_dir}"
  install_launcher "${src_dir}"
  install_desktop_files "${src_dir}"
  compile_locales_if_possible "${src_dir}"
  post_install_notes
}

main "$@"
