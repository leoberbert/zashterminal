# zashterminal/utils/updater.py

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from ..settings.config import get_config_paths
from .logger import get_logger

GITHUB_OWNER = "leoberbert"
GITHUB_REPO = "zashterminal"
RELEASES_LATEST_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
RAW_CONFIG_MAIN_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/"
    "src/zashterminal/settings/config.py"
)


def _read_os_release() -> Dict[str, str]:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return {}
    data: Dict[str, str] = {}
    try:
        for raw in os_release.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k] = v.strip().strip('"').strip("'")
    except Exception:
        return {}
    return data


def _normalize_version(version: str) -> str:
    return version.strip().lstrip("vV")


def _version_key(version: str) -> Tuple[int, ...]:
    normalized = _normalize_version(version)
    nums = re.findall(r"\d+", normalized)
    if not nums:
        return (0,)
    return tuple(int(n) for n in nums)


def _is_remote_newer(local_version: str, remote_version: str) -> bool:
    return _version_key(remote_version) > _version_key(local_version)


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class UpdateManager:
    """Checks GitHub for new versions and builds update commands."""

    def __init__(self):
        self.logger = get_logger("zashterminal.updater")
        self.state_file = get_config_paths().CONFIG_DIR / "update_state.json"

    def _read_state(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_state(self, state: Dict[str, Any]) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            self.logger.warning(f"Failed to persist update state: {e}")

    def _fetch_remote_version(self) -> Optional[Dict[str, str]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "zashterminal-updater",
        }
        try:
            r = requests.get(RELEASES_LATEST_URL, headers=headers, timeout=8)
            if r.ok:
                payload = r.json()
                tag_name = str(payload.get("tag_name", "")).strip()
                if tag_name:
                    version = _normalize_version(tag_name)
                    return {
                        "version": version,
                        "ref": tag_name,
                        "source": "github-release",
                    }
        except Exception as e:
            self.logger.warning(f"Release API check failed: {e}")

        try:
            r = requests.get(RAW_CONFIG_MAIN_URL, timeout=8)
            if r.ok:
                match = re.search(
                    r'APP_VERSION\s*=\s*["\']([^"\']+)["\']',
                    r.text,
                )
                if match:
                    version = _normalize_version(match.group(1))
                    return {
                        "version": version,
                        "ref": "main",
                        "source": "github-main",
                    }
        except Exception as e:
            self.logger.warning(f"Raw config version check failed: {e}")

        return None

    def check_for_updates(self, local_version: str) -> Dict[str, Any]:
        if os.environ.get("ZASHTERMINAL_DISABLE_AUTO_UPDATE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return {"checked": False, "reason": "disabled-by-env"}

        now = int(time.time())
        today = _today_key()
        state = self._read_state()
        first_run = not bool(state.get("initialized"))

        # At most one remote check per calendar day.
        if str(state.get("last_check_day", "")) == today:
            cached_remote = str(state.get("last_remote_version", "")).strip()
            if not cached_remote:
                return {"checked": False, "reason": "already-checked-today"}
            return {
                "checked": False,
                "reason": "already-checked-today",
                "update_available": _is_remote_newer(local_version, cached_remote),
                "first_run": first_run,
                "local_version": _normalize_version(local_version),
                "remote_version": _normalize_version(cached_remote),
                "remote_ref": str(state.get("last_remote_ref", "main")),
                "remote_source": str(state.get("last_remote_source", "cached")),
            }

        remote = self._fetch_remote_version()
        state["initialized"] = True
        state["last_check"] = now
        state["last_check_day"] = today
        if remote:
            state["last_remote_version"] = remote["version"]
            state["last_remote_ref"] = remote["ref"]
            state["last_remote_source"] = remote["source"]
        self._write_state(state)

        if not remote:
            return {"checked": True, "update_available": False, "first_run": first_run}

        remote_version = remote["version"]
        update_available = _is_remote_newer(local_version, remote_version)
        return {
            "checked": True,
            "update_available": update_available,
            "first_run": first_run,
            "local_version": _normalize_version(local_version),
            "remote_version": remote_version,
            "remote_ref": remote["ref"],
            "remote_source": remote["source"],
        }

    def mark_update_triggered(self, remote_version: str) -> None:
        state = self._read_state()
        state["last_update_triggered"] = int(time.time())
        state["last_update_target_version"] = _normalize_version(remote_version)
        self._write_state(state)

    def has_prompted_today(self) -> bool:
        state = self._read_state()
        return str(state.get("last_prompt_day", "")) == _today_key()

    def mark_prompted_today(self, remote_version: str) -> None:
        state = self._read_state()
        state["last_prompt_day"] = _today_key()
        state["last_prompt_version"] = _normalize_version(remote_version)
        state["last_prompt_at"] = int(time.time())
        self._write_state(state)

    def _detect_distro_id(self) -> str:
        info = _read_os_release()
        return info.get("ID", "").strip().lower()

    def build_update_command(self, remote_ref: str) -> str:
        distro_id = self._detect_distro_id()
        safe_ref = remote_ref.strip() or "main"

        if distro_id == "nixos":
            return (
                'nix --extra-experimental-features "nix-command flakes" '
                f'profile add --refresh "github:{GITHUB_OWNER}/{GITHUB_REPO}/{safe_ref}#zashterminal"'
            )

        # Distros with install.sh flow.
        return (
            'TMP_DIR="$(mktemp -d)" && '
            f'(git clone --depth 1 --branch "{safe_ref}" '
            f'https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git "$TMP_DIR/{GITHUB_REPO}" '
            f'|| git clone --depth 1 https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git "$TMP_DIR/{GITHUB_REPO}") && '
            f'cd "$TMP_DIR/{GITHUB_REPO}" && INSTALL_MODE=local bash ./install.sh'
        )
