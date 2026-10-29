# Zashterminal

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/zashterminal.svg" alt="Zashterminal" width="96" height="96">
</p>

<p align="center">
  <b>A modern GTK4 terminal for developers, infrastructure, and system administration</b>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://www.gtk.org/"><img src="https://img.shields.io/badge/GTK-4.0+-orange.svg" alt="GTK"></a>
  <a href="https://gnome.pages.gitlab.gnome.org/libadwaita/"><img src="https://img.shields.io/badge/libadwaita-1.0+-purple.svg" alt="libadwaita"></a>
  <a href="https://ko-fi.com/leoberbert"><img src="https://img.shields.io/badge/Support-Ko--fi-ff5f5f.svg" alt="Ko-fi"></a>
</p>

Zashterminal combines a VTE terminal with session management, an integrated file manager, optional AI assistance, and workflow tools — built on GTK4 and libadwaita for a native Linux desktop experience.

**Supported installs:** Arch/Manjaro (AUR), Debian/Ubuntu, Fedora, openSUSE, Gentoo, NixOS. WSL (Ubuntu) is experimental.

---

## Table of contents

- [Gallery](#gallery)
- [Features](#features)
- [SecureCRT & PAM](#securecrt--pam)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License & support](#license--support)

---

## Gallery

### Terminal + file manager

Split view with a live shell above and a browsable file list below.

<p align="center">
  <img src="https://github.com/user-attachments/assets/4c264548-909e-4edb-95be-a5dc6a6756bb" alt="Terminal with integrated file manager" width="920">
</p>

### Multi-pane layouts

Tile terminals for monitoring, status, and interactive shells in one window.

<p align="center">
  <img src="https://github.com/user-attachments/assets/6aba3c63-a181-4e3c-8870-d58ceae11daa" alt="Four-pane terminal layout" width="920">
</p>

### Color schemes

Built-in palettes with live previews; edit or create custom schemes from Preferences.

<p align="center">
  <img src="https://github.com/user-attachments/assets/46e41739-7c28-47d7-b4ba-26e9320b0061" alt="Terminal color scheme picker" width="920">
</p>

### AI assistant — configuration

Optional AI with Groq, Gemini, OpenRouter, or local models (Ollama / LM Studio). Only text you explicitly send is shared.

<p align="center">
  <img src="https://github.com/user-attachments/assets/762fa599-a266-41c3-83c2-f28fe825f0f6" alt="AI assistant configuration dialog" width="920">
</p>

### AI assistant — chat panel

Side or docked panel with explanations, suggested commands, and one-click run / copy.

<p align="center">
  <img src="https://github.com/user-attachments/assets/4dd9482b-420d-4170-878d-e9a652493ec9" alt="AI assistant chat with suggested commands" width="920">
</p>

### AI assistant — quick prompts

Starter prompts for common shell tasks when you open the panel.

<p align="center">
  <img src="https://github.com/user-attachments/assets/97aae8ed-6466-46b9-b7e4-ca1256f425ff" alt="AI quick prompts under the terminal" width="920">
</p>

### File manager

Browse local directories with size, permissions, and ownership columns — without leaving the terminal.

<p align="center">
  <img src="https://github.com/user-attachments/assets/a40bd623-eb31-4a8b-9fe2-e327d8b7de0c" alt="Integrated file manager listing" width="920">
</p>

### Session context menu

Connect with SFTP, edit, duplicate, rename, or delete saved sessions from the sidebar.

<p align="center">
  <img src="https://github.com/user-attachments/assets/f340ac07-3408-488c-a4a8-d26ac1b7cdab" alt="Session context menu for SSH/SFTP" width="920">
</p>

---

## Features

| Area | What you get |
|------|----------------|
| **Sessions** | Local, SSH, and SFTP sessions with folders, restore, and layouts |
| **Layouts** | Horizontal/vertical splits; save and restore multi-pane setups |
| **File manager** | Local and remote browsing, drag-and-drop transfer, remote edit + upload on save |
| **Highlighting** | Output rules for 50+ commands; optional Pygments for `cat` / input |
| **AI (optional)** | Multi-provider chat, quick prompts, click-to-run snippets — selection only |
| **Broadcast** | Type once; run across selected tabs/panes |
| **Appearance** | 30 built-in schemes (default: GitHub Dark), transparency, shortcuts |

### Color schemes

Default: **GitHub Dark**. Also included: Ayu Mirage, Catppuccin (Mocha / Latte), Dracula, Everforest, Gruvbox, Horizon, Kanagawa, Material Ocean, Monokai, Moonfly, Night Owl, Nord, One Dark Pro, Oxocarbon, Palenight, Rosé Pine, Solarized, Tokyo Night, VS Code Dark+, System Default, and light variants.

Switch or customize from **Preferences → Terminal colors**.

---

## SecureCRT & PAM

Migrate from SecureCRT without leaving sessions behind:

- Import sessions from the main menu (**Import SecureCRT Sessions**), including full directory trees
- **Password V2** support (`02:<hex>`) mapped into Zashterminal’s session format
- Keyboard-interactive flows used with **Balabit / One Identity** privileged session gateways

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
