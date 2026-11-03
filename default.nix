{
  python3Packages,
  vte-gtk4,
  gtk4,
  libadwaita,
  rsync,
  sshpass,
  pkg-config,
  libsecret,
  wrapGAppsHook4,
  gobject-introspection,
  pywal16,
  imagemagick,
}:

python3Packages.buildPythonApplication {
  pname = "zashterminal";
  version = "0.8.22";

  src = ./.;

  pyproject = true;

  build-system = with python3Packages; [ uv-build ];
  dependencies = with python3Packages; [
    pygobject3
    pycairo
    setproctitle
    requests
    psutil
    pycryptodomex
    py7zr
    (toPythonModule pywal16)
  ];

  nativeBuildInputs = [
    pkg-config
    wrapGAppsHook4
    gobject-introspection
  ];
  buildInputs = [
    vte-gtk4
    gtk4
    libadwaita
    rsync
    sshpass
    libsecret
  ];

  # NixOS/Wayland stacks can hit EGL/ZINK crashes with some drivers.
  # GDK backend policy is shared with install.sh via scripts/gdk_backend_env.sh
  # (prefer wayland,x11 when the backend exists; force x11 when it does not).
  makeWrapperArgs = [
    "--prefix PATH : ${imagemagick}/bin"
    "--set-default GSK_RENDERER cairo"
    "--run source ${./scripts/gdk_backend_env.sh}"
  ];

  postInstall = ''
    cp $src/usr/share $out/share -r
  '';
}
