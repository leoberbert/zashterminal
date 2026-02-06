# Zashterminal

<p align="center">
  <img src="https://github.com/leoberbert/zashterminal/blob/main/usr/share/icons/hicolor/scalable/apps/zashterminal.svg" alt="Logo Zashterminal" width="128" height="128">
</p>

<p align="center">
  <strong>A modern terminal for developers, infrastructure, and system administration</strong>
</p>
<p align="center">
  <a href="https://github.com/leoberbert/zashterminal/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0-green.svg" alt="Licença"/></a>
  <a href="https://www.gtk.org/"><img src="https://img.shields.io/badge/GTK-4.0+-orange.svg" alt="Versão GTK"/></a>
  <a href="https://gnome.pages.gitlab.gnome.org/libadwaita/"><img src="https://img.shields.io/badge/libadwaita-1.0+-purple.svg" alt="Versão libadwaita"/></a>
  <a href="https://ko-fi.com/leoberbert"><img src="https://img.shields.io/badge/Support-Ko--fi-ff5f5f.svg" alt="Support on Ko-fi"/></a>
</p>

**Zashterminal** is a modern, intuitive, and innovative terminal built with GTK4 and Adwaita. It blends powerful features for developers and system administrators with a welcoming experience for newcomers. Simplified session management, an integrated file manager, automatic color highlighting, and workflow-focused tools make the command line more productive on any Linux distribution.

## Why Zashterminal

- **Focused on real workflows**: Manage SSH/SFTP sessions, panes, and layouts without leaving the terminal.
- **Accessible by design**: Clear UI, smart defaults, and discoverable actions help beginners get started faster.
- **Optional AI assistance**: Only the text you explicitly select is sent, keeping control and privacy in your hands.
- **Built on modern Linux UI**: GTK4 + libadwaita for a native, responsive desktop experience.

## Screenshots

<img width="1457" height="699" alt="image" src="https://github.com/user-attachments/assets/4c264548-909e-4edb-95be-a5dc6a6756bb" />

<img width="1457" height="699" alt="image" src="https://github.com/user-attachments/assets/6aba3c63-a181-4e3c-8870-d58ceae11daa" />

<img width="1457" height="699" alt="image" src="https://github.com/user-attachments/assets/46e41739-7c28-47d7-b4ba-26e9320b0061" />


## Key Features

### 🤖 AI Assistant Integration

<img width="1457" height="699" alt="image" src="https://github.com/user-attachments/assets/762fa599-a266-41c3-83c2-f28fe825f0f6" />

<img width="1457" height="699" alt="image" src="https://github.com/user-attachments/assets/4dd9482b-420d-4170-878d-e9a652493ec9" />


Zashterminal creates a bridge between your shell and Large Language Models (LLMs), offering an **optional** and fully **non-intrusive** AI experience. The assistant only processes the content that **you explicitly select and choose to send**, ensuring full control over your privacy.
* **Multi-Provider Support**: Native integration with **Groq**, **Google Gemini**, **OpenRouter**, and **Local LLMs** (Ollama/LM Studio).
* **Context Aware**: The AI understands your OS and distribution context to provide accurate and relevant commands.
* **Chat Panel**: A dedicated side panel for persistent conversations, command suggestions, and "Click-to-Run" code snippets.
* **Smart Suggestions**: Ask how to perform tasks and receive ready-to-execute commands directly in the UI.


### 📂 Advanced File Manager & Remote Editing

<img width="1457" height="699" alt="image" src="https://github.com/user-attachments/assets/a40bd623-eb31-4a8b-9fe2-e327d8b7de0c" />


-   **Integrated Side Panel**: Browse local and remote file systems without leaving the terminal.
-   **Remote Editing**: Click to edit remote files (SSH/SFTP) in your favorite local editor. Zashterm watches the file and automatically uploads changes on save.
-   **Drag & Drop Transfer**: Upload files to remote servers simply by dragging them into the terminal window over (SFTP/Rsync)
-   **Transfer Manager**: Track uploads and downloads with a detailed progress manager and history.
<img width="1355" height="675" alt="image" src="https://github.com/user-attachments/assets/f340ac07-3408-488c-a4a8-d26ac1b7cdab" />



### ⚡ Productivity Tools

<img width="1457" height="699" alt="image" src="https://github.com/user-attachments/assets/97aae8ed-6466-46b9-b7e4-ca1256f425ff" />


-   **Input Broadcasting**: Type commands in one terminal and execute them simultaneously across multiple selected tabs/panes.
-   **Quick Prompts**: One-click AI prompts for common tasks (e.g., "Explain this error", "Optimize this command").


### 🖥️ Core Terminal Functionality
-   **Session Management**: Save, organize (with folders), and launch Local, SSH, and SFTP sessions.
-   **Flexible Layouts**: Split panes horizontally and vertically; save and restore complex window layouts.
-   **Directory Tracking**: Updates tab titles automatically based on the current working directory (OSC7 support).
-   **Deep Customization**: Visual theme editor, font sizing, transparency (window and headerbar), and extensive keyboard shortcuts.


## Dependencies
To build and run Zashterminal, you will need:

-   **Python 3.9+**
-   **GTK4** and **Adwaita 1.0+** (`libadwaita`)
-   **VTE for GTK4** (`vte4` >= 0.76 recommended)
-   **Python Libraries**:
    -   `PyGObject` (GTK bindings)
    -   `cryptography` (Secure password storage)
    -   `requests` (For AI API connectivity)
    -   `pygments` (For syntax highlighting)
    -   `psutil` (Optional, for advanced process tracking)
    -   `regex` (Optional, for high-performance highlighting patterns)

## Installation (works on any distro)

Arch/Manjaro (AUR):
```bash
yay -S zashterminal        # ou
paru -S zashterminal
```

Debian/Ubuntu/Fedora/openSUSE/etc. via Distrobox installer:
```bash
# Quick install (no clone required)
curl -fsSL https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh | bash

# Alternatively, download and run
curl -fsSLO https://raw.githubusercontent.com/leoberbert/zashterminal/refs/heads/main/install.sh
bash install.sh
```

## Usage

```bash
zashterminal [options] [directory]
```

#### Arguments

| Option | Description |
|--------|-------------|
| `-w, --working-directory DIR` | Set initial working directory |
| `-e, -x, --execute COMMAND` | Execute command on startup (all remaining args are included) |
| `--close-after-execute` | Close the terminal tab after the command finishes |
| `--ssh [USER@]HOST` | Immediately connect to an SSH host |
| `--new-window` | Force opening a new window instead of a tab |

#### Examples

```bash
# Open terminal in a specific directory
zashterminal ~/projects

# Execute a command
zashterminal -e htop

# SSH connection
zashterminal --ssh user@server.example.com

# Execute command and close after completion
zashterminal --close-after-execute -e "ls -la"
```

## Configuration

Configuration files are stored in `~/.config/zashterminal/`:

| File/Directory | Description |
|----------------|-------------|
| `settings.json` | General preferences, appearance, terminal behavior, shortcuts, and AI configuration |
| `sessions.json` | Saved SSH/SFTP connections and session folders |
| `session_state.json` | Window state and session restore data |
| `layouts/` | Saved window layouts (split panes configuration) |
| `logs/` | Application logs (when logging to file is enabled) |
| `backups/` | Manual encrypted backup archives |

**Note**: Syntax highlighting rules are bundled with the application in `data/highlights/` and include rules for 50+ commands (docker, git, systemctl, kubectl, and more).

## Contributing

Contributions are welcome\!

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/amazing-feature`).
3.  Commit your changes.
4.  Push to the branch.
5.  Open a Pull Request.

## License

This project is licensed under the GNU GPL v3 (or later) - see the [LICENSE](LICENSE) file for details.

## Support the Project

If you enjoy Zashterminal and it improves your workflow, a contribution helps cover development time and ongoing costs. Any support is appreciated and goes directly into making the project better:

- Ko-fi: https://ko-fi.com/leoberbert

## Contact

- Email: leo4berbert@gmail.com
- LinkedIn: https://linkedin.com/in/leoberbert

## Acknowledgments

  - Developers of **GNOME**, **GTK**, **VTE**, and **Pygments**.

---

Made with code, coffee, and curiosity through long nights of lines of code, by Leonardo Berbert.
