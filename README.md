# Zashterminal

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/zashterminal.svg" alt="Zashterminal" width="96" height="96">
</p>

<p align="center">
  <b>The GTK4 terminal for people who live in SSH: sessions, files, search and AI in one window.</b>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://www.gtk.org/"><img src="https://img.shields.io/badge/GTK-4.0+-orange.svg" alt="GTK"></a>
  <a href="https://gnome.pages.gitlab.gnome.org/libadwaita/"><img src="https://img.shields.io/badge/libadwaita-1.0+-purple.svg" alt="libadwaita"></a>
  <a href="https://ko-fi.com/leoberbert"><img src="https://img.shields.io/badge/Support-Ko--fi-ff5f5f.svg" alt="Ko-fi"></a>
</p>

<p align="center">
  <img src="docs/gallery/01-main-terminal.png" alt="Zashterminal main window" width="920">
</p>

Zashterminal pairs a VTE terminal with session management, an integrated file manager, optional AI assistance and workflow tools. It is built on GTK4 and libadwaita for a native Linux desktop.

## Why Zashterminal?

| | |
|---|---|
| 🔌 **Sessions that stay organized** | Local, SSH and SFTP sessions in folders, with favorites, connection status and history. |
| 📊 **Know your host at a glance** | Status bar and Monitor panel with CPU, memory, disks, network, processes, latency — local or over SSH. |
| ⌨️ **Command palette** | `Ctrl+Shift+P` finds sessions, layouts, commands, actions and preferences. |
| 🪟 **Real multitasking** | Split panes, saved layouts, search across all tabs, broadcast to many panes. |
| 📁 **Files next to your shell** | Local and remote browser with drag-and-drop and edit-and-upload. |
| 🎨 **Readable output** | Highlight rules for 50+ commands and 30 color schemes; the UI follows the terminal theme. |
| 🤖 **AI, only when you ask** | OpenAI, Anthropic, Gemini, xAI, Mistral, DeepSeek, Groq and more, or your own endpoint; only text you send leaves your machine. |
| 📥 **Easy migration** | Import SecureCRT, PuTTY, mRemoteNG and OpenSSH configs. |

**Installs:** Arch/Manjaro (AUR), Debian/Ubuntu, Fedora, openSUSE, Gentoo, NixOS. WSL (Ubuntu) is experimental.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh | bash
zashterminal
```

Arch users: `yay -S zashterminal`. Other platforms: see [Installation](#installation).

## Table of contents

- [Tour](#tour)
- [Features](#features)
- [Imports & PAM](#imports--pam)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License & support](#license--support)

---

## Tour

Screenshots live in [`docs/gallery/`](docs/gallery/). Regenerate them with `PYTHONPATH=src python3 scripts/capture_gallery.py`.

### Activity rail and sessions panel

The rail on the left switches the side panel: **Sessions**, **Files**, **Commands**, **History**, **Monitor** and **AI**. Sessions are cards with `user@host` and a connection dot, grouped in folders (single-click the arrow to expand, drag several at once to move them). It auto-hides after you open a session (configurable in Preferences). Tabs are named after the session; extra copies become `name(1)`, `name(2)`…

<p align="center">
  <img src="docs/gallery/13-sessions-sidebar.png" alt="Sessions panel" width="880">
</p>

### Status bar and Monitor

The status bar shows the connection, OS, CPU, memory, disk (of the shell's current directory) and latency of the active tab; click a metric to open the Monitor. Local hosts are read from `/proc`; SSH hosts through the tab's existing connection (no new login, no prompts). Data is kept per tab, so switching tabs is instant.

<p align="center">
  <img src="docs/gallery/15-monitor-panel.png" alt="Monitor side panel" width="880">
</p>

Expand the Monitor into its own window for load, swap, per-core CPU, all disks and the top processes by CPU and memory (as `top` shows them). GPU and temperature appear when the host has them.

<p align="center">
  <img src="docs/gallery/16-monitor-window.png" alt="Expanded monitor window" width="760">
</p>

### Connection history

Recently used sessions with search, newest first; click to reconnect.

<p align="center">
  <img src="docs/gallery/17-history.png" alt="Connection history panel" width="880">
</p>

### Command palette

`Ctrl+Shift+P`: one search box for sessions, SFTP, layouts, commands, actions and preferences. Type `@` for sessions and layouts only, `>` for actions only. `Esc` closes it (and every other dialog).

<p align="center">
  <img src="docs/gallery/14-command-palette.png" alt="Command palette" width="880">
</p>

### Multi-pane layouts

Split horizontally and vertically to watch logs, metrics and a shell in one tab. Each pane has a "⋯" menu to move, split or close it.

<p align="center">
  <img src="docs/gallery/02-multipane.png" alt="Multi-pane terminal layout" width="880">
</p>

### Integrated file manager

Browse local and remote (SFTP) directories under the terminal: size, permissions, owner, recursive filter.

<p align="center">
  <img src="docs/gallery/03-file-manager.png" alt="Terminal with integrated file manager" width="880">
</p>

### Search across all tabs

Find bar with **Case sensitive**, **Regex** and **All tabs**. Next/Previous jumps between matching panes.

<p align="center">
  <img src="docs/gallery/04-search-all-tabs.png" alt="Global search across all tabs" width="880">
</p>

### Broadcast a command

Type once and send to selected tabs or panes. Target panes get an orange outline and the bar lists who will receive the command.

<p align="center">
  <img src="docs/gallery/05-broadcast.png" alt="Broadcast command bar" width="880">
</p>

### Preferences

Appearance, terminal behavior, profiles/backups and advanced VTE options, with search. See [Background blur](#background-blur).

<p align="center">
  <img src="docs/gallery/07-preferences.png" alt="Preferences appearance page" width="760">
</p>

### Color schemes

Thirty built-in schemes plus Pywal wallpaper detection, with preview before applying.

<p align="center">
  <img src="docs/gallery/06-color-schemes.png" alt="Color scheme picker" width="620">
</p>

### Output highlighting

Terminal colors, output rules and command-specific highlighting.

<p align="center">
  <img src="docs/gallery/09-highlight.png" alt="Highlight and terminal colors dialog" width="760">
</p>

### Session editor

SSH/local sessions with folders, auth, post-login commands, forwards, favorite flag and per-session highlight overrides.

<p align="center">
  <img src="docs/gallery/10-session-edit.png" alt="Add or edit SSH session" width="620">
</p>

### AI assistant

Optional AI chat with **OpenAI, Anthropic (Claude), Google Gemini, xAI (Grok), Mistral, DeepSeek, Groq, Perplexity, Together AI, OpenRouter, local Ollama / LM Studio**, or **Other (custom)**: any OpenAI- or Anthropic-compatible endpoint with your base URL, API key and model. Each provider keeps its own key in the system keyring; "Browse models" lists what the provider offers. Only text you explicitly send is shared.

<p align="center">
  <img src="docs/gallery/08-ai-config.png" alt="AI assistant configuration" width="620">
</p>

### Command manager

Reusable command snippets with icons and forms: insert, run, or open as a dialog.

<p align="center">
  <img src="docs/gallery/12-command-manager.png" alt="Command manager" width="880">
</p>

### Keyboard shortcuts

Browse, search and customize accelerators; conflicts are flagged.

<p align="center">
  <img src="docs/gallery/11-shortcuts.png" alt="Keyboard shortcuts dialog" width="620">
</p>

---

## Features

| Area | What you get |
|------|----------------|
| **Sessions** | Local, SSH and SFTP sessions with folders, favorites, history, restore and layouts |
| **Monitor** | Status bar + Monitor panel/window: CPU, memory, swap, disks, network, processes, GPU, latency (local and SSH) |
| **Command palette** | `Ctrl+Shift+P` over sessions, layouts, commands, actions and preferences |
| **Imports** | SecureCRT directory trees, PuTTY `.reg`/`.ini`, mRemoteNG `confCons.xml`, OpenSSH `~/.ssh/config` |
| **Layouts** | Horizontal/vertical splits; save and restore multi-pane setups |
| **File manager** | Local and remote browsing, drag-and-drop transfer, remote edit + upload on save |
| **Search** | In-terminal find with case/regex; **All tabs** scope jumps across panes |
| **Highlighting** | Output rules for 50+ commands; optional Pygments for `cat` / input |
| **AI (optional)** | 12 providers + custom OpenAI/Anthropic-compatible endpoint, per-provider keys, quick prompts, click-to-run snippets |
| **Broadcast** | Type once; run across selected tabs/panes |
| **Appearance** | 30 built-in schemes (default: GitHub Dark), transparency, blur, shortcuts |

### Color schemes

Default: **GitHub Dark**. Also included: Ayu Mirage, Catppuccin (Mocha / Latte), Dracula, Everforest, Gruvbox, Horizon, Kanagawa, Material Ocean, Monokai, Moonfly, Night Owl, Nord, One Dark Pro, Oxocarbon, Palenight, Rosé Pine, Solarized, Tokyo Night, VS Code Dark+, System Default, and light variants.

Switch or customize from **Main menu → Colors & highlighting**.

### Background blur

Background blur is rendered live by the compositor: it follows window movement, resizing and changes behind the terminal without screenshots, hidden windows or capture permissions. Enable **Background Blur** and raise **Terminal Transparency** to see the effect. Transparency and blur strength are separate controls.

Toggling blur takes effect immediately, including while Preferences is focused and the terminal is idle; changing transparency or moving the window is not required.

On KDE Wayland, the optional **Zashterminal Per-Window Blur** native effect enables **Blur Strength** levels **1–15** in Preferences. It changes the GPU sampling kernel for the requested terminal surface only; it does not change the desktop's blur strength or blur other applications. Requests are identified by the caller's authenticated PID and native surface ID, not the window title.

Strength changes affect the window whose Preferences you opened; other open terminal windows keep their own values. The last selected strength is saved as the default for newly created windows.

The native effect currently targets **Fedora 44 x86_64, KWin 6.8.2 and Qt 6.11.2**. Build it with rootless Podman and network access; no compiler or development packages are installed on the host:

```bash
bash native/kwin-blur/build.sh --stage
bash native/kwin-blur/build.sh --help
```

`--stage` builds into the user cache without changing the running desktop. The helper's `--help` shows how to expose the staged plugin to a **new nested KWin** through `QT_PLUGIN_PATH` for isolated validation. After validation, install it into the existing system plugin directory with administrator authentication:

```bash
bash native/kwin-blur/build.sh --install-system
```

Installation copies only the plugin; it does not restart KWin or change its global settings. Restart the terminal application to discover and load the effect. KWin's native API is private: rebuild and revalidate the effect after KWin/Qt upgrades; the helper rejects mismatched versions.

Without the extension, standard Wayland background effects, older KDE Wayland blur, and the X11 KDE blur hint remain available when advertised by the compositor. Their strength belongs to the desktop, so the independent-strength slider is disabled with installation guidance. On unsupported compositors the blur switch is disabled too. Existing nonzero screenshot-blur amounts migrate to enabled; transparency is preserved.

---

## Imports & PAM

Bring sessions in from the main menu:

- **SecureCRT** — Sessions folder trees + **Password V2** (`02:<hex>`)
- **PuTTY** — exported `.reg` or portable `.ini` (SSH hosts, keys, local forwards; optional `/`-as-folders)
- **mRemoteNG** — `confCons.xml` SSH nodes (legacy / AES-GCM passwords; optional master password)
- **OpenSSH** — `~/.ssh/config` hosts synced on startup

Keyboard-interactive flows used with **Balabit / One Identity** privileged session gateways are supported.

One Identity (formerly Balabit) PAM docs: [Privileged Access Management](https://www.oneidentity.com/br-pt/privileged-access-management/)

---

## Requirements

| Component | Notes |
|-----------|--------|
| Python | 3.10+ |
| GTK / Adwaita | GTK4, libadwaita 1.0+ |
| VTE | VTE for GTK4 (`vte4` ≥ 0.76 recommended) |
| PyGObject, pycairo | GTK bindings |
| pycryptodomex | SecureCRT-compatible crypto |
| requests | AI API calls |
| pygments | Optional syntax highlighting |
| psutil, regex | Optional |

---

## Installation

### Arch / Manjaro

```bash
yay -S zashterminal   # or: paru -S zashterminal
```

Or system-wide via installer:

```bash
curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh | bash
```

### Debian / Ubuntu / Fedora / openSUSE

`install.sh` detects the distro, installs system packages, and places a venv under `/opt/zashterminal/venv`.

```bash
curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh | bash
```

### Gentoo

Same installer (`emerge`). It writes `package.use`, prefers the amd64 binhost when available (`GENTOO_USE_BINHOST=0` for source builds), installs `gsettings-desktop-schemas`, and puts `pycryptodomex` in the venv (Gentoo’s `pycryptodome` exposes `Crypto`, not `Cryptodome`).

```bash
curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh | bash
```

The launcher sets `GDK_BACKEND=x11` when GTK was built without Wayland but Distrobox still exports `WAYLAND_DISPLAY` / `GDK_BACKEND=wayland`.

### Immutable / atomic distributions (Distrobox)

Use the dedicated `install-distrobox.sh` (works on any distro with Distrobox installed; the regular `install.sh` is unchanged):

```bash
curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install-distrobox.sh | bash
```

It creates (or reuses) a container named `zashterminal` from `docker.io/library/archlinux:latest`, runs `install.sh` inside it, and runs `distrobox-export --app zashterminal` so the app appears in the host menu. Override with `DISTROBOX_NAME` or `DISTROBOX_IMAGE`, or skip the export with `ZASH_NO_EXPORT=1`. Running it by hand inside an existing container works too.

When Zashterminal detects it is running in a Distrobox (and `distrobox-host-exec` is available), local tabs open your **host** shell, not the container's: the host login shell is used, the working directory and OSC 7 tracking are preserved, and `/tmp` must be shared with the host (the Distrobox default; checked automatically). SSH/SFTP sessions still run the container's `ssh`/`sshpass`. Set `ZASHTERMINAL_SHELL_IN_CONTAINER=1` to keep the container shell. Limitation: SSH-in-tab detection (process tree) does not see host children.

### NixOS

Uses the project flake into the user profile. Enable flakes and ensure `git` is installed, then:

```bash
sudo nixos-rebuild switch
curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh | bash
```

```nix
nix.settings.experimental-features = [ "nix-command" "flakes" ];
environment.systemPackages = with pkgs; [ git ];
```

### WSL (experimental)

Tested on **Ubuntu 24.04 + WSLg**. Same `install.sh` flow as Debian/Ubuntu. For non-English locales, set `LANG` / `LC_ALL` and, if needed, `setxkbmap` in `~/.bashrc`, then restart WSL.

---

## Usage

```bash
zashterminal [options] [directory]
```

| Option | Description |
|--------|-------------|
| `-w, --working-directory DIR` | Initial working directory |
| `-e, -x, --execute COMMAND` | Run command on startup (remaining args included); **closes when the command exits** by default |
| `--close-after-execute` | Close after execute (default with `--execute`) |
| `--no-close-after-execute` | Keep the shell open after `--execute` |
| `--ssh [USER@]HOST` | Open an SSH session |
| `--new-window` | Force a new window |

```bash
zashterminal ~/projects
zashterminal -e htop
zashterminal --ssh user@server.example.com
zashterminal --no-close-after-execute -e "ls -la"
```

---

## Configuration

Files under `~/.config/zashterminal/`:

| Path | Purpose |
|------|---------|
| `settings.json` | Preferences, appearance, shortcuts, AI |
| `sessions.json` | Saved SSH/SFTP sessions and folders |
| `session_state.json` | Window / restore state |
| `layouts/` | Saved split layouts |
| `logs/` | Optional file logs |
| `backups/` | Encrypted backup archives |

Highlight rules ship in `data/highlights/` (docker, git, systemctl, kubectl, and more).

---

## Contributing

1. Fork the repository  
2. Create a branch (`git checkout -b feature/your-change`)  
3. Commit and push  
4. Open a pull request  

CI runs unit tests on Python 3.10–3.13 with coverage. Pull requests also trigger a [SonarQube Cloud](https://sonarcloud.io) scan when the repository secret `SONAR_TOKEN` is set (optional `SONAR_ORGANIZATION` / `SONAR_PROJECT_KEY`). See `sonar-project.properties`.

Blur protocol regressions run headlessly over private Wayland socketpairs and an isolated D-Bus daemon. They verify compositor-visible pending/committed state, independent surface strengths, cleanup and protocol failures without connecting to the desktop session or requiring the optional KWin effect. Install `libwayland-client` and `libwayland-server` runtime libraries plus `dbus-daemon` for these tests; both CI workflows install them explicitly.

**Important:** in SonarQube Cloud → project → **Administration → Analysis Method**, disable **Automatic Analysis**. CI analysis and Automatic Analysis cannot run together.

Local checks:

```bash
python3 -m pip install -e ".[dev]"
PYTHONPATH=src python3 -m coverage run -m unittest discover -s tests -v
PYTHONPATH=src python3 -m coverage report
```

---

## License & support

Licensed under **GNU GPL v3 or later** — see [LICENSE](LICENSE).

If Zashterminal helps your workflow, support development on [Ko-fi](https://ko-fi.com/leoberbert).

**Contact:** [leo4berbert@gmail.com](mailto:leo4berbert@gmail.com) · [LinkedIn](https://linkedin.com/in/leoberbert)

**Thanks** to the GNOME, GTK, VTE, and Pygments communities.

---

<p align="center">
  <sub>Leonardo Berbert</sub>
</p>
