# Wallpaper colors

Open **Highlight Colors → Terminal Colors → Wallpaper colors**.

1. Select **Detect wallpaper**, or **Choose image…** for a local file.
2. When multiple images are found, no image is preselected: choose the source
   explicitly. KDE packages expose all resolutions and both light/dark variants,
   which may have completely different colors. Hover over the selector after
   choosing to see the full image path. Moving the
   terminal between monitors does not change its colors.
3. Select Dark or Light, inspect the terminal preview, and click **Apply**.
4. **Restore manual theme** restores the saved manual scheme and interface
   preference. Selecting a manual scheme also exits wallpaper mode.
5. **Save as theme** creates an independent editable copy without switching the
   active theme. Reopening the dialog alone never changes the active palette.

Updates are deliberately manual in this first version. Detect/choose again to
refresh after changing the wallpaper. No periodic task, watcher, external theme
generator, shell hook, terminal reset, or image decoding runs at startup. The
applied palette is saved in settings and survives a missing source image.

For translated source runs, compile the checkout catalogs once after changing
translations, then restart the application:

```sh
python3 scripts/compile_locales.py pt
PYTHONPATH=src .venv/bin/python -m zashterminal
```

Omit `pt` to compile all existing language catalogs. This requires GNU gettext's
`msgfmt` and writes only ignored `.mo` files under the repository's `locale/`
directory. Source runs prefer these catalogs and retain installed translations
as fallback. The `pt` catalog also serves `pt_BR`; no system installation or
administrator privileges are required.

## Generation dependencies and local execution

The feature uses **Pywal16 3.8.x**, the maintained fork providing the `pywal`
Python module, plus **ImageMagick**. It is tested with Pywal16 3.8.15.
For a local checkout with GTK bindings supplied by the distro:

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install 'pywal16>=3.8,<4'
PYTHONPATH=src .venv/bin/python -m zashterminal
```

ImageMagick must provide `magick` or `convert` on PATH. `install.sh` installs both
ImageMagick and Pywal16 in its application environment. Pywal16 is a required
Python dependency, installed by a normal `pip install .`; no extra is needed.
ImageMagick is a system dependency and must be installed separately for pip-only
installations. Arch packaging requires `python-pywal16` and `imagemagick`, and the
Nix expression includes both dependencies.

Existing applied snapshots remain unchanged on upgrade. Select Detect wallpaper
or Choose image again and Apply to replace an old snapshot with the Pywal result.
The first generation can take seconds; repeated unchanged requests use the cache.

## Linux support

The feature does not depend on a distribution name, package manager, X11 or
Wayland. All supported Zashterminal installations can use the file picker,
subject to Pywal16, ImageMagick, GdkPixbuf raster loaders and file permissions.
Missing generation dependencies keep the current palette and show an actionable
message; they do not fall back to a different extraction algorithm.

| Desktop | Discovery method | Scope |
| --- | --- | --- |
| KDE Plasma | Read the user's `plasma-org.kde.plasma.desktop-appletsrc` | Static `org.kde.image` containments, including image packages and light/dark variants |
| GNOME, Unity, Budgie | Read available GNOME GSettings keys | `picture-uri` and `picture-uri-dark`, when present |
| Cinnamon | Read Cinnamon GSettings | Static `picture-uri` |
| MATE | Read MATE GSettings | Static `picture-filename` |
| Xfce | Read live `xfconf-query -c xfce4-desktop -l -v` | `last-image` / `image-path`, when the desktop's tool is installed |
| Other desktops/compositors | Choose image | No assumptions about wallpaper daemons or shell commands |

Schema existence is checked before creating GSettings objects. Missing tools,
unavailable schemas, sandbox restrictions, animated wallpapers, slideshows and
unsupported sources fall back to choosing an image. KDE detection reports saved
configuration; it does not claim to identify the currently displayed frame of a
dynamic wallpaper or the active activity. Detection never changes the desktop.

## Performance and colors

- Extraction runs in a background thread only after an explicit action. Controls
  prevent overlapping extraction in the same dialog; closing it discards results.
- Input is limited to regular local raster files up to 32 MiB / 40 megapixels.
  Supported types are PNG, JPEG, WebP and BMP when their loaders are available.
- Pywal16's `wal` backend processes the original image, with its own ImageMagick
  resize/quantization, matching `wal -i IMAGE --backend wal --cols16` (plus `-l`
  for light mode). This replaces the former single-hue thumbnail algorithm.
- A separate Python process calls only the backend and palette serialization.
  It never calls the wal CLI, exports templates, sends terminal sequences,
  reloads desktop components, or changes the wallpaper. Temporary configuration
  and cache paths are isolated. A 30-second timeout kills the process group,
  including ImageMagick. The GTK thread only applies completed colors.
- A bounded four-entry cache stores palettes, not images. Source size, modification
  time and light/dark mode distinguish entries. The persisted snapshot is separate
  from the manual scheme and does not rearrange built-in scheme indexes.
- The VTE update changes only colors, cursor and selection. Font, scrollback, PTY,
  processes and session state are not reapplied by the wallpaper event.
- Background, foreground, cursor and all 16 ANSI entries are preserved exactly
  as Pywal produces them. ANSI positions may no longer have their conventional
  red/green meanings. No extra saturation or contrast correction changes the
  terminal palette. The header uses Pywal's background; interface accent/selection
  is the highest-contrast chromatic entry, and its text is black or white.
- The existing terminal-style interface path updates the application surfaces;
  custom RGB output and independently themed program content remain their own.
  Turning off the interface switch uses the previous interface preference; if that
  preference already follows terminal colors, it continues to do so.

## Validation

Run the non-window tests:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Run the explicit integration test in a graphical session:

```sh
PYTHONPATH=src .venv/bin/python scripts/test_wallpaper_ui.py
```

Add `--desktop` to also read the current desktop's wallpaper candidates and use
the first one in the isolated preview test. This does not change the wallpaper.

Both use temporary configuration when importing application settings. The GTK
test creates unpresented widgets, without launching a shell, and covers both color
dialogs, apply/restore, dark/light mode, terminal content preservation, saving,
missing files and modern/legacy CSS parsing. It does not certify every distro or
measure interactive terminal throughput under load. The host image loader may
require access to the session D-Bus even for these local tests.

Before release, visually exercise the feature with multiple windows, panes,
SSH sessions, a large stream of output, transparency and open auxiliary dialogs.
Repeat desktop detection on the supported environments in the table. Check
typing latency and CPU/memory during extraction, application and idle separately.

### Seleção automática da variante

No KDE, a detecção consulta a cor de fundo do esquema do desktop em
`kdeglobals` para inferir a aparência clara/escura, independentemente do tema do
terminal. Em pacotes com resoluções no nome do arquivo, prioriza a proporção e
resolução do monitor da janela. No GNOME, consulta `color-scheme` para escolher
entre `picture-uri` e `picture-uri-dark`. A seleção gera somente a prévia;
é necessário clicar em Aplicar para mudar as cores.

Essa seleção é uma aproximação para pacotes do KDE, não uma consulta ao arquivo
renderizado pelo compositor. Se houver vários monitores/atividades com fontes
diferentes ou informação insuficiente, as alternativas continuam disponíveis
para escolha manual. Wallpapers dinâmicos e plugins externos podem exigir
Escolher imagem.
