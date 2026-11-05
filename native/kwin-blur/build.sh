#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

usage() {
    cat <<'HELP'
Build the optional Zashterminal native KWin effect, without changing the desktop.

Usage:
  bash native/kwin-blur/build.sh --stage [OUTPUT_DIR]
  bash native/kwin-blur/build.sh --env [OUTPUT_DIR]
  bash native/kwin-blur/build.sh --install-system [OUTPUT_DIR]
  bash native/kwin-blur/build.sh --help

Prerequisites: Fedora 44 x86_64, installed kwin/kwin-libs 6.8.2-3.fc44,
Qt base 6.11.2-3.fc44 and Qt declarative 6.11.2-2.fc44, working rootless Podman,
and network access to the Fedora image registry and Fedora 44 RPM repositories.
No host compiler, SDK, root access, or running compositor is needed to stage.
The pinned KWin/Qt SDK packages must remain available in those repositories.

--stage builds a rootless Fedora 44 SDK image, compiles, and installs ONLY into
OUTPUT_DIR/stage. The source is mounted read-only; no session bus, Wayland socket,
or GPU is exposed to the container. SDK dependencies are installed only inside
that image. All build artifacts default to:
  ${XDG_CACHE_HOME:-$HOME/.cache}/zashterminal/kwin-blur/6.8.2-qt6.11.2
An explicit OUTPUT_DIR must also be outside the source checkout.

--env prints a shell-quoted export of QT_PLUGIN_PATH for the staged plugin root.
Use it BEFORE starting a nested compositor in a private D-Bus session, e.g.:
  eval "$(bash native/kwin-blur/build.sh --env)"
  XDG_CONFIG_HOME="${XDG_CACHE_HOME:-$HOME/.cache}/zashterminal/kwin-blur/nested-config" \
  dbus-run-session -- kwin_wayland --wayland-display "$WAYLAND_DISPLAY" \
    --socket zashterminal-blur-test --width 1280 --height 720 \
    --no-lockscreen --no-global-shortcuts --no-kactivities
This helper does not start it or change KWin settings. QT_PLUGIN_PATH affects
newly started processes only: ~/.local is NOT automatically discoverable by a
running KWin.
KWin discovers the effect under kwin/effects/plugins/zashterminalblur.so.

--install-system is an explicit opt-in ONLY AFTER successful nested validation.
It checks the installed ABI again, then uses sudo (authentication may be needed)
to copy just the staged .so into Fedora's existing system Qt6 plugin directory.
It never loads/reloads an effect, changes global blur, or restarts KWin. A later
explicit org.kde.kwin.Effects.loadEffect("zashterminalblur") on org.kde.KWin can
rescan that existing plugin directory; loading unproven code into the desktop
can crash the session. Keep the stage for inspection before choosing to install.

KWin's native API is private: rebuild/revalidate after KWin or Qt upgrades. The
CMake configuration additionally requires KWin 6.8.2 and Qt 6.11.2 exactly.
HELP
}

fail() {
    printf 'zashterminalblur: %s\n' "$*" >&2
    exit 1
}

check_host() {
    command -v rpm >/dev/null || fail 'RPM is required to check the host ABI.'
    [[ $(rpm -E '%{fedora}') == 44 && $(rpm -E '%{_arch}') == x86_64 ]] ||
        fail 'This SDK is pinned to Fedora 44 x86_64.'
    local package expected actual
    for package in kwin kwin-libs qt6-qtbase qt6-qtdeclarative; do
        case "$package" in
            kwin|kwin-libs) expected=6.8.2-3.fc44.x86_64 ;;
            qt6-qtbase) expected=6.11.2-3.fc44.x86_64 ;;
            qt6-qtdeclarative) expected=6.11.2-2.fc44.x86_64 ;;
        esac
        actual=$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}' "$package") ||
            fail "Required runtime package is missing: $package"
        [[ $actual == "$expected" ]] ||
            fail "$package ABI mismatch: expected $expected, found $actual"
    done
}

command=${1:---help}
case "$command" in
    --help|-h) usage; exit 0 ;;
    --stage|--env|--install-system) ;;
    *) usage >&2; exit 2 ;;
esac
(( $# <= 2 )) || fail 'Too many arguments.'
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
checkout=$(realpath -- "$source_dir/../..")
output=$(realpath -m -- "${2:-${XDG_CACHE_HOME:-$HOME/.cache}/zashterminal/kwin-blur/6.8.2-qt6.11.2}")
case "$output/" in
    "$checkout/"*) fail 'OUTPUT_DIR must be outside the source checkout.' ;;
esac
stage="$output/stage"
plugin_root="$stage/lib/qt6/plugins"
plugin="$plugin_root/kwin/effects/plugins/zashterminalblur.so"

if [[ $command == --env ]]; then
    [[ -f $plugin ]] || fail "No staged plugin at $plugin; run --stage first."
    printf 'export QT_PLUGIN_PATH=%q\n' "$plugin_root${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
    exit 0
fi

check_host
if [[ $command == --install-system ]]; then
    [[ -f $plugin && -f $output/runtime-abi ]] || fail 'Run --stage first.'
    [[ $(<"$output/runtime-abi") == 'kwin=6.8.2-3.fc44.x86_64 qt=6.11.2-3.fc44.x86_64 quick=6.11.2-2.fc44.x86_64' ]] ||
        fail 'Staging ABI does not match this helper; rebuild first.'
    destination="$(rpm -E '%{_libdir}')/qt6/plugins/kwin/effects/plugins/zashterminalblur.so"
    printf 'Installing staged native code into %s (no live loading).\n' "$destination"
    sudo install -D -m 0755 -- "$plugin" "$destination"
    exit 0
fi

[[ $EUID != 0 ]] || fail 'Build as your normal user with rootless Podman, not sudo.'
command -v podman >/dev/null || fail 'Install Podman before using --stage.'
[[ $(podman info --format '{{.Host.Security.Rootless}}') == true ]] ||
    fail 'Rootless Podman is required.'
mkdir -p -- "$output/build" "$stage" "$output/image-context"
image=localhost/zashterminal-kwin-blur-sdk:6.8.2-3-qt6.11.2-v1
# Immutable Fedora 44 x86_64 base. RPM ABI packages are pinned as well.
podman build --pull=missing --tag "$image" --file - "$output/image-context" <<'CONTAINERFILE'
FROM registry.fedoraproject.org/fedora@sha256:b6e6d5bda753913fb912953e45408d93739b031645d2fafed6e5f516fe54294f
RUN dnf -y --setopt=install_weak_deps=False install \
        gcc-c++ cmake ninja-build extra-cmake-modules \
        kwin-devel-6.8.2-3.fc44.x86_64 \
        qt6-qtbase-devel-6.11.2-3.fc44.x86_64 \
        qt6-qtdeclarative-devel-6.11.2-2.fc44.x86_64 \
        kdecoration-devel-6.8.2-1.fc44.x86_64 \
        libepoxy-devel wayland-devel libdrm-devel vulkan-loader-devel \
        libxcb-devel libxkbcommon-devel \
    && dnf clean all
CONTAINERFILE

# label=disable avoids relabeling the user's checkout on SELinux systems.
# The build has no network, capabilities, desktop sockets, or writable source.
podman run --rm --network=none --userns=keep-id \
    --cap-drop=ALL --security-opt=no-new-privileges --security-opt=label=disable \
    --volume "$source_dir:/source:ro" \
    --volume "$output:/output:rw" \
    "$image" bash -euc '
        umask 022
        cmake -S /source -B /output/build -G Ninja \
            -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_INSTALL_PREFIX=/output/stage \
            -DKDE_INSTALL_PLUGINDIR=lib/qt6/plugins \
            -DBUILD_TESTING=OFF
        cmake --build /output/build --parallel 2
        cmake --install /output/build
        rpm -qa --qf "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n" | sort > /output/sdk-packages.txt
    '
[[ -f $plugin ]] || fail "Build did not stage the expected plugin: $plugin"
printf '%s\n' 'kwin=6.8.2-3.fc44.x86_64 qt=6.11.2-3.fc44.x86_64 quick=6.11.2-2.fc44.x86_64' > "$output/runtime-abi"
printf 'Staged: %s\n' "$plugin"
printf 'For a NEW nested compositor: export QT_PLUGIN_PATH=%q\n' "$plugin_root${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
