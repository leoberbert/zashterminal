#!/bin/bash

set -euo pipefail

PACKAGE_NAME="zashterminal"
DESKTOP_ID="org.leoberbert.zashterminal"
REPO_URL="https://github.com/leoberbert/zashterminal.git"

INSTALL_ROOT="/opt/${PACKAGE_NAME}"
VENV_DIR="${INSTALL_ROOT}/venv"
BIN_PATH="/usr/local/bin/${PACKAGE_NAME}"
APP_DIR="/usr/share/applications"
ICON_DIR="/usr/share/icons/hicolor/scalable/apps"
PIXMAP_DIR="/usr/share/pixmaps"
LOCALE_BASE_DIR="/usr/share/locale"

DISTRO_FAMILY=""
PKG_MANAGER=""
INSTALL_MODE="${INSTALL_MODE:-auto}"   # auto | local | aur
ARCH_AUR_HELPER="${ARCH_AUR_HELPER:-}" # yay | paru

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
    dnf|zypper) : ;;
    *) die "Unsupported package manager: $PKG_MANAGER" ;;
  esac
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
        python python-pip git rsync sshpass gettext
        gtk4 libadwaita vte4 libsecret
        gobject-introspection python-gobject python-cairo
      )
      python_runtime_packages=(
        python-requests python-psutil python-regex python-pygments
        python-py7zr python-setproctitle python-cryptography
      )
      ;;
    debian)
      base_packages=(
        python3 python3-venv python3-pip git rsync sshpass gettext
        libgtk-4-1 libadwaita-1-0 libvte-2.91-gtk4-0 libsecret-1-0
        gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 gir1.2-secret-1
        python3-gi python3-gi-cairo python3-cairo
      )
      python_runtime_packages=(
        python3-requests python3-psutil python3-regex python3-pygments
      )
      optional_python_packages=(
        python3-py7zr python3-setproctitle python3-cryptography
      )
      ;;
    fedora)
      base_packages=(
        python3 python3-pip git rsync sshpass gettext
        gtk4 libadwaita vte291-gtk4 libsecret gobject-introspection
        python3-gobject python3-cairo
      )
      python_runtime_packages=(
        python3-requests python3-psutil python3-regex python3-pygments
        python3-cryptography
      )
      optional_python_packages=(
        python3-py7zr python3-setproctitle
      )
      ;;
    suse)
      base_packages=(
        python3 python3-pip git rsync sshpass gettext-tools
        gtk4 libadwaita-1-0 libvte-2_91-0 libsecret-1-0
        typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 typelib-1_0-Vte-3_91 typelib-1_0-Secret-1
        python3-gobject python3-cairo
      )
      python_runtime_packages=(
        python3-requests python3-psutil python3-regex python3-Pygments
      )
      optional_python_packages=(
        python3-py7zr python3-setproctitle python3-cryptography
      )
      ;;
    *)
      die "No dependency mapping for distro family: $DISTRO_FAMILY"
      ;;
  esac

  pkg_update_once
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
  sudo "${VENV_DIR}/bin/python" -m pip install --no-deps "${src_dir}" >/dev/null

  # Default extras in the venv (py7zr + setproctitle requested as default).
  # They may already be available via system packages on some distros.
  sudo "${VENV_DIR}/bin/python" -m pip install \
    requests psutil regex Pygments cryptography py7zr setproctitle >/dev/null || \
    warn "Some Python packages could not be installed in the venv (continuing)."
}

install_launcher() {
  log "Installing launcher to ${BIN_PATH}..."
  sudo tee "${BIN_PATH}" >/dev/null <<EOF
#!/bin/sh
exec "${VENV_DIR}/bin/${PACKAGE_NAME}" "\$@"
EOF
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
  log "Starting system-wide installation for ${PACKAGE_NAME} (no distrobox, with venv)"
  detect_system
  resolve_install_mode

  if [ "${DISTRO_FAMILY}" = "arch" ]; then
    if helper="$(choose_arch_aur_helper 2>/dev/null)" && [ "${INSTALL_MODE}" != "local" ]; then
      install_arch_via_aur
      log "Installed via AUR (${helper})."
      exit 0
    fi
  fi

  install_system_dependencies
  ensure_runtime_prereqs

  local src_dir
  src_dir="$(prepare_source)"

  install_python_app "${src_dir}"
  install_launcher
  install_desktop_files "${src_dir}"
  compile_locales_if_possible "${src_dir}"
  post_install_notes
}

main "$@"
