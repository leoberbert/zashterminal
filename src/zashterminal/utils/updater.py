# zashterminal/utils/updater.py

import json
import os
import re
import shlex
import shutil
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
TAGS_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/tags?per_page=15"
)
RAW_CONFIG_MAIN_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/"
    "src/zashterminal/settings/config.py"
)
# Re-check the remote periodically so a same-day version bump is not missed.
CHECK_INTERVAL_SECONDS = 4 * 60 * 60

_ARCH_FAMILY_IDS = frozenset(
    {
        "arch",
        "archarm",
        "manjaro",
        "endeavouros",
        "cachyos",
        "garuda",
        "artix",
    }
)
_AUR_HELPERS = ("yay", "paru")
_SEMVER_TAG = re.compile(r"^v?\d+(?:\.\d+)*", re.IGNORECASE)
_TRUTHY = frozenset({"1", "true", "yes", "on"})


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


def is_arch_family(os_release: Optional[Dict[str, str]] = None) -> bool:
    """Return True for Arch and Arch-based distros (Manjaro, CachyOS, …)."""
    info = os_release if os_release is not None else _read_os_release()
    distro_id = info.get("ID", "").strip().lower()
    if distro_id in _ARCH_FAMILY_IDS:
        return True
    like = info.get("ID_LIKE", "").strip().lower().split()
    return "arch" in like


def is_auto_update_disabled_by_env(
    environ: Optional[Dict[str, str]] = None,
) -> bool:
    env = environ if environ is not None else os.environ
    return env.get("ZASHTERMINAL_DISABLE_AUTO_UPDATE", "").strip().lower() in _TRUTHY


def allow_main_version_fallback(
    environ: Optional[Dict[str, str]] = None,
) -> bool:
    """Whether update checks may use raw ``main`` APP_VERSION as last resort.

    Default is False so installed builds only prompt on Releases/tags and avoid
    surprise bumps from an unreleased ``main``. Opt in with
    ``ZASHTERMINAL_UPDATE_ALLOW_MAIN=1`` (useful for developers tracking tip).
    """
    env = environ if environ is not None else os.environ
    return env.get("ZASHTERMINAL_UPDATE_ALLOW_MAIN", "").strip().lower() in _TRUTHY


class UpdateManager:
    """Checks GitHub for new versions and builds update commands."""

    def __init__(self, state_file: Optional[Path] = None):
        self.logger = get_logger("zashterminal.updater")
        self.state_file = state_file or (
            get_config_paths().CONFIG_DIR / "update_state.json"
        )

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

    def _parse_release_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
        tag_name = str(payload.get("tag_name", "")).strip()
        if not tag_name:
            return None
        return {
            "version": _normalize_version(tag_name),
            "ref": tag_name,
            "source": "github-release",
        }

    def _parse_tags_payload(self, payload: Any) -> Optional[Dict[str, str]]:
        if not isinstance(payload, list):
            return None
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name or not _SEMVER_TAG.match(name):
                continue
            return {
                "version": _normalize_version(name),
                "ref": name,
                "source": "github-tag",
            }
        return None

    def _parse_raw_config_version(self, text: str) -> Optional[Dict[str, str]]:
        match = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', text)
        if not match:
            return None
        return {
            "version": _normalize_version(match.group(1)),
            "ref": "main",
            "source": "github-main",
        }

    def _fetch_remote_version(
        self, *, allow_main_fallback: Optional[bool] = None
    ) -> Optional[Dict[str, str]]:
        """Resolve remote version: Releases → Tags → (optional) raw main."""
        if allow_main_fallback is None:
            allow_main_fallback = allow_main_version_fallback()

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "zashterminal-updater",
        }

        try:
            r = requests.get(RELEASES_LATEST_URL, headers=headers, timeout=8)
            if r.ok:
                parsed = self._parse_release_payload(r.json())
                if parsed:
                    return parsed
            elif r.status_code == 404:
                self.logger.info(
                    "No GitHub Releases found; trying repository tags next."
                )
            else:
                self.logger.warning(
                    f"Release API returned HTTP {r.status_code}; trying tags."
                )
        except Exception as e:
            self.logger.warning(f"Release API check failed: {e}")

        try:
            r = requests.get(TAGS_URL, headers=headers, timeout=8)
            if r.ok:
                parsed = self._parse_tags_payload(r.json())
                if parsed:
                    return parsed
                if allow_main_fallback:
                    self.logger.info(
                        "No semver tags found; falling back to main config."
                    )
                else:
                    self.logger.info(
                        "No semver tags found; skipping main fallback "
                        "(set ZASHTERMINAL_UPDATE_ALLOW_MAIN=1 to enable)."
                    )
            else:
                self.logger.warning(
                    f"Tags API returned HTTP {r.status_code}"
                    + (
                        "; falling back to main."
                        if allow_main_fallback
                        else "; not using main fallback."
                    )
                )
        except Exception as e:
            self.logger.warning(f"Tags API check failed: {e}")

        if not allow_main_fallback:
            return None

        try:
            r = requests.get(RAW_CONFIG_MAIN_URL, timeout=8)
            if r.ok:
                return self._parse_raw_config_version(r.text)
        except Exception as e:
            self.logger.warning(f"Raw config version check failed: {e}")

        return None

    def check_for_updates(
        self,
        local_version: str,
        *,
        enabled: bool = True,
        allow_main_fallback: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if is_auto_update_disabled_by_env():
            return {"checked": False, "reason": "disabled-by-env"}
        if not enabled:
            return {"checked": False, "reason": "disabled-by-setting"}

        now = int(time.time())
        today = _today_key()
        state = self._read_state()
        first_run = not bool(state.get("initialized"))
        local_norm = _normalize_version(local_version)
        cached_remote = str(state.get("last_remote_version", "")).strip()
        last_check = int(state.get("last_check") or 0)

        within_interval = (
            last_check > 0 and (now - last_check) < CHECK_INTERVAL_SECONDS
        )
        if within_interval and cached_remote:
            return {
                "checked": False,
                "reason": "recently-checked",
                "update_available": _is_remote_newer(local_norm, cached_remote),
                "first_run": first_run,
                "local_version": local_norm,
                "remote_version": _normalize_version(cached_remote),
                "remote_ref": str(state.get("last_remote_ref", "main")),
                "remote_source": str(state.get("last_remote_source", "cached")),
            }

        remote = self._fetch_remote_version(
            allow_main_fallback=allow_main_fallback
        )
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
        update_available = _is_remote_newer(local_norm, remote_version)
        return {
            "checked": True,
            "update_available": update_available,
            "first_run": first_run,
            "local_version": local_norm,
            "remote_version": remote_version,
            "remote_ref": remote["ref"],
            "remote_source": remote["source"],
        }

    def mark_update_triggered(self, remote_version: str) -> None:
        state = self._read_state()
        state["last_update_triggered"] = int(time.time())
        state["last_update_target_version"] = _normalize_version(remote_version)
        self._write_state(state)

    def has_prompted_today(self, remote_version: Optional[str] = None) -> bool:
        """Return True if we already prompted today for this remote version (or newer)."""
        state = self._read_state()
        if str(state.get("last_prompt_day", "")) != _today_key():
            return False
        if not remote_version:
            return True
        last_prompted = str(state.get("last_prompt_version", "")).strip()
        if not last_prompted:
            return True
        return not _is_remote_newer(last_prompted, remote_version)

    def mark_prompted_today(self, remote_version: str) -> None:
        state = self._read_state()
        state["last_prompt_day"] = _today_key()
        state["last_prompt_version"] = _normalize_version(remote_version)
        state["last_prompt_at"] = int(time.time())
        self._write_state(state)

    def _detect_distro_id(self) -> str:
        info = _read_os_release()
        return info.get("ID", "").strip().lower()

    def build_update_command(
        self,
        remote_ref: str,
        *,
        os_release: Optional[Dict[str, str]] = None,
        which=shutil.which,
    ) -> str:
        info = os_release if os_release is not None else _read_os_release()
        distro_id = info.get("ID", "").strip().lower()
        safe_ref = remote_ref.strip() or "main"

        if distro_id == "nixos":
            return (
                'nix --extra-experimental-features "nix-command flakes" '
                f'profile add --refresh "github:{GITHUB_OWNER}/{GITHUB_REPO}/{safe_ref}#zashterminal"'
            )

        if is_arch_family(info):
            for helper in _AUR_HELPERS:
                helper_path = which(helper)
                if helper_path:
                    # AUR helpers pull the packaged release; avoid silent prompts in VTE.
                    return (
                        f"{shlex.quote(helper_path)} -S --needed --noconfirm zashterminal"
                    )
            self.logger.info(
                "Arch-based distro without yay/paru; falling back to install.sh."
            )

        return (
            'TMP_DIR="$(mktemp -d)" && '
            f'(git clone --depth 1 --branch "{safe_ref}" '
            f'https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git "$TMP_DIR/{GITHUB_REPO}" '
            f'|| git clone --depth 1 https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git "$TMP_DIR/{GITHUB_REPO}") && '
            f'cd "$TMP_DIR/{GITHUB_REPO}" && INSTALL_MODE=local bash ./install.sh'
        )
