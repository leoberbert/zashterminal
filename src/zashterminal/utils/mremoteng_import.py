"""Parse mRemoteNG confCons.xml into SSH session dicts.

Password decryption mirrors mRemoteNG / ZashCRT (legacy Rijndael / AES-GCM).

The MD5 keying, AES-CBC, and PBKDF2-HMAC-SHA1 steps below are required to
read existing confCons.xml exports; they are import-only interoperability
decryptors, not algorithms chosen for new storage in Zashterminal.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from Cryptodome.Cipher import AES
from Cryptodome.Hash import MD5, SHA1
from Cryptodome.Protocol.KDF import PBKDF2


DEFAULT_MREMOTENG_PASSWORD = "mR3m"


@dataclass
class MRemoteImportedSession:
    name: str
    folder_path: str
    host: str
    user: str = ""
    port: int = 22
    password: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class _NodeState:
    group: str = ""
    username: str = ""
    password: str = ""
    protocol: str = ""
    port: int = 0


def _attr(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if key.lower() == name.lower():
            return value
    return ""


def _attr_bool(element: ET.Element, name: str) -> bool:
    return _attr(element, name).strip().lower() in {"1", "true", "yes"}


def decrypt_mremote_legacy_rijndael(cipher_text: str, password: str) -> str:
    """Decrypt mRemoteNG legacy (pre-AES-GCM) password fields.

    Algorithm is fixed by the mRemoteNG file format (MD5-derived key + AES-CBC).
    """
    if not cipher_text.strip():
        return ""
    encrypted = base64.b64decode(cipher_text.strip())
    block_size = 16
    if len(encrypted) <= block_size:
        raise ValueError("Invalid mRemoteNG encrypted payload.")
    # mRemoteNG derives the AES key with MD5(password); cannot be upgraded.
    key = MD5.new(password.encode("utf-8")).digest()  # NOSONAR
    iv = encrypted[:block_size]
    cipher = encrypted[block_size:]
    # CBC is the mode written by mRemoteNG's legacy Rijndael encryptor.
    aes = AES.new(key, AES.MODE_CBC, iv=iv)  # NOSONAR
    plain = aes.decrypt(cipher)
    # PKCS7-ish trailing padding from .NET — strip trailing nulls/control.
    plain = plain.rstrip(b"\x00")
    pad = plain[-1] if plain else 0
    if 1 <= pad <= block_size and plain.endswith(bytes([pad]) * pad):
        plain = plain[:-pad]
    return plain.decode("utf-8", errors="replace")


def decrypt_mremote_aes_gcm(
    cipher_text: str, password: str, iterations: int = 1000
) -> str:
    """Decrypt mRemoteNG AES-GCM password fields (PBKDF2-HMAC-SHA1 + AES-GCM).

    KDF parameters match mRemoteNG exports; SHA-1 here is format-mandated.
    """
    if not cipher_text.strip():
        return ""
    if iterations < 1000:
        iterations = 1000
    encrypted = base64.b64decode(cipher_text.strip())
    salt_size, nonce_size, tag_size, key_size = 16, 16, 16, 32
    if len(encrypted) <= salt_size + nonce_size + tag_size:
        raise ValueError("Invalid mRemoteNG encrypted payload.")
    salt = encrypted[:salt_size]
    nonce = encrypted[salt_size : salt_size + nonce_size]
    cipher_and_tag = encrypted[salt_size + nonce_size :]
    cipher = cipher_and_tag[:-tag_size]
    tag = cipher_and_tag[-tag_size:]
    # mRemoteNG AES-GCM passwords use PBKDF2-HMAC-SHA1 with the file's iteration count.
    key = PBKDF2(  # NOSONAR
        password.encode("utf-8"),
        salt,
        dkLen=key_size,
        count=iterations,
        hmac_hash_module=SHA1,  # NOSONAR
    )
    aes = AES.new(key, AES.MODE_GCM, nonce=nonce)
    # mRemoteNG authenticates the salt as AAD.
    aes.update(salt)
    plain = aes.decrypt_and_verify(cipher, tag)
    return plain.decode("utf-8", errors="replace")


def _resolve_crypto(
    engine: str, mode: str
) -> Tuple[str, Optional[str]]:
    """Return (mode_name, warning). mode_name: legacy|gcm|unsupported."""
    engine = (engine or "").strip()
    mode = (mode or "").strip()
    if not engine and not mode:
        return "legacy", None
    if engine.lower() == "aes" and mode.lower() == "gcm":
        return "gcm", None
    return (
        "unsupported",
        f"mRemoteNG encryption {engine}/{mode} is not supported; "
        "sessions will be imported without passwords.",
    )


def import_mremoteng_sessions_from_file(
    path: Path,
    *,
    master_password: Optional[str] = None,
) -> Tuple[List[MRemoteImportedSession], List[str], bool]:
    """Parse confCons.xml.

    Returns (sessions, warnings, needs_master_password).
    When needs_master_password is True, call again with master_password set.
    """
    warnings: List[str] = []
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag.split("}")[-1].lower() != "connections":
        connections = None
        for child in root.iter():
            if child.tag.split("}")[-1].lower() == "connections":
                connections = child
                break
        if connections is None:
            raise ValueError("Invalid mRemoteNG XML: missing Connections root.")
        root = connections

    if _attr_bool(root, "FullFileEncryption"):
        raise ValueError(
            "This mRemoteNG file uses full-file encryption, which is not supported. "
            "Export with visible XML and encrypted password fields only."
        )

    crypto_mode, crypto_warning = _resolve_crypto(
        _attr(root, "EncryptionEngine"), _attr(root, "BlockCipherMode")
    )
    if crypto_warning:
        warnings.append(crypto_warning)

    try:
        kdf_iterations = int(_attr(root, "KdfIterations") or "1000")
    except ValueError:
        kdf_iterations = 1000
    if kdf_iterations <= 0:
        kdf_iterations = 1000

    encryption_password = DEFAULT_MREMOTENG_PASSWORD
    password_available = False

    def decrypt_value(cipher_text: str, password: str) -> str:
        if crypto_mode == "legacy":
            return decrypt_mremote_legacy_rijndael(cipher_text, password)
        if crypto_mode == "gcm":
            return decrypt_mremote_aes_gcm(cipher_text, password, kdf_iterations)
        raise ValueError("unsupported")

    if crypto_mode != "unsupported":
        protected = _attr(root, "Protected")
        try:
            plain = decrypt_value(protected, encryption_password)
            if plain in {"ThisIsNotProtected", "ThisIsProtected"}:
                password_available = True
        except Exception:
            password_available = False

        if not password_available:
            # None → caller should prompt; "" / wrong → continue without passwords.
            if master_password is None:
                return [], warnings, True
            if master_password:
                try:
                    plain = decrypt_value(protected, master_password)
                    if plain in {"ThisIsNotProtected", "ThisIsProtected"}:
                        encryption_password = master_password
                        password_available = True
                except Exception:
                    password_available = False
            if not password_available:
                warnings.append(
                    "mRemoteNG passwords were not imported because the "
                    "master password was not provided or could not be validated."
                )

    def decrypt_password(cipher_text: str, session_name: str) -> str:
        if not cipher_text.strip():
            return ""
        if crypto_mode == "unsupported" or not password_available:
            warnings.append(
                f"Password for mRemoteNG session '{session_name}' was not imported "
                "because the encryption key is unavailable."
            )
            return ""
        try:
            return decrypt_value(cipher_text, encryption_password)
        except Exception:
            warnings.append(
                f"Password for mRemoteNG session '{session_name}' could not be decrypted."
            )
            return ""

    imported: List[MRemoteImportedSession] = []

    def walk(element: ET.Element, parent: _NodeState) -> None:
        for child in list(element):
            if child.tag.split("}")[-1].lower() != "node":
                walk(child, parent)
                continue

            node_type = _attr(child, "Type").strip()
            node_name = _attr(child, "Name").strip()

            current = _NodeState(
                group=parent.group,
                username=parent.username,
                password=parent.password,
                protocol=parent.protocol,
                port=parent.port,
            )

            if _attr_bool(child, "InheritUsername"):
                current.username = parent.username
            else:
                current.username = _attr(child, "Username").strip()

            if _attr_bool(child, "InheritProtocol"):
                current.protocol = parent.protocol
            else:
                current.protocol = _attr(child, "Protocol").strip()

            if _attr_bool(child, "InheritPort"):
                current.port = parent.port
            else:
                try:
                    current.port = int(_attr(child, "Port") or "0")
                except ValueError:
                    current.port = 0

            if _attr_bool(child, "InheritPassword"):
                current.password = parent.password
            else:
                current.password = decrypt_password(
                    _attr(child, "Password"), node_name or "unnamed"
                )

            if node_type.lower() == "container":
                if node_name:
                    current.group = (
                        f"/{node_name}"
                        if not parent.group
                        else f"{parent.group}/{node_name}"
                    )
                walk(child, current)
                continue

            if node_type.lower() != "connection":
                walk(child, current)
                continue

            protocol = (current.protocol or "").strip().lower()
            hostname = _attr(child, "Hostname").strip()
            if protocol not in {"ssh", "ssh1", "ssh2"}:
                if protocol:
                    warnings.append(
                        f"Unsupported mRemoteNG protocol '{protocol}' "
                        f"in session '{node_name}'."
                    )
                walk(child, current)
                continue
            if not hostname:
                warnings.append(
                    f"Skipped mRemoteNG session '{node_name}' (missing Hostname)."
                )
                walk(child, current)
                continue

            port = current.port if current.port > 0 else 22
            imported.append(
                MRemoteImportedSession(
                    name=node_name or "Imported mRemoteNG Session",
                    folder_path=parent.group,
                    host=hostname,
                    user=current.username,
                    port=port,
                    password=current.password,
                )
            )
            walk(child, current)

    walk(root, _NodeState())
    # Deduplicate warnings while preserving order
    seen = set()
    unique_warnings: List[str] = []
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            unique_warnings.append(warning)
    return imported, unique_warnings, False
