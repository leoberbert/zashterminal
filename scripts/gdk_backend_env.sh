# Shared GDK backend policy for Distrobox / Gentoo / Nix / native installs.
# Source this before launching Zashterminal (install.sh launcher + Nix wrapper).
#
# Policy:
#   1. If the GTK4 Wayland GDK backend is missing but DISPLAY is available,
#      force GDK_BACKEND=x11 (avoids "No such backend: wayland" / NULL display).
#   2. If Wayland backend exists and GDK_BACKEND is unset, prefer wayland,x11.
#   3. Never override an explicit user-set non-wayland-primary backend (e.g. x11).

zash_apply_gdk_backend_policy() {
  # Nothing to do without any display.
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    return 0
  fi

  _zash_gdk_wayland=""
  for _zash_d in \
    /usr/lib64 \
    /usr/lib \
    /usr/lib/x86_64-linux-gnu \
    /usr/lib/aarch64-linux-gnu \
    /run/current-system/sw/lib
  do
    [ -d "${_zash_d}" ] || continue
    if find "${_zash_d}" \
      \( -name '*gdk*wayland*' -o -name '*libgdk-4*wayland*' \) \
      2>/dev/null | grep -q .
    then
      _zash_gdk_wayland=1
      break
    fi
  done

  if [ -z "${_zash_gdk_wayland}" ]; then
    case "${GDK_BACKEND:-}" in
      ""|wayland|wayland,*)
        export GDK_BACKEND=x11
        ;;
    esac
  elif [ -z "${GDK_BACKEND:-}" ]; then
    export GDK_BACKEND=wayland,x11
  fi

  unset _zash_gdk_wayland _zash_d
}

zash_apply_gdk_backend_policy
