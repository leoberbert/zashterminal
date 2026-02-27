# zashterminal/utils/crypto.py

import os
from typing import Dict, Optional, Tuple

import gi

gi.require_version("Secret", "1")
from gi.repository import Gio, Secret

from .exceptions import ZashterminalError
from .logger import get_logger

# Schema to identify Zashterminal passwords in the system keyring.
SECRET_SCHEMA = Secret.Schema.new(
    "org.leoberbert.zashterminal.Password",
    Secret.SchemaFlags.NONE,
    {"session_name": Secret.SchemaAttributeType.STRING},
)
SECURECRT_V2_PREFIX = "02:"


def _get_securecrt_v2_crypto_backend() -> Tuple[Optional[object], Optional[object]]:
    """
    Lazy import for Cryptodome primitives used by SecureCRT Password V2 format.
    Returns (AES, SHA256) when available, (None, None) otherwise.
    """
    try:
        from Cryptodome.Cipher import AES
        from Cryptodome.Hash import SHA256

        return AES, SHA256
    except Exception:
        return None, None


def is_encryption_available() -> bool:
    """Checks if the libsecret library is available."""
    try:
        # A successful import is already a good indication.
        return True
    except (ImportError, ValueError):
        return False


def is_session_password_encryption_available() -> bool:
    """Checks whether SecureCRT V2-compatible encryption backend is available."""
    aes, sha256 = _get_securecrt_v2_crypto_backend()
    return aes is not None and sha256 is not None


def is_securecrt_v2_password(value: str) -> bool:
    """Returns True when value matches the `02:<hex>` SecureCRT V2 prefix."""
    return bool(value and value.startswith(SECURECRT_V2_PREFIX))


def encrypt_session_password(password: str, config_passphrase: str = "") -> str:
    """
    Encrypt plaintext password in a SecureCRT Password V2-compatible payload.
    Result format: `02:<hex>`.
    """
    aes, sha256 = _get_securecrt_v2_crypto_backend()
    if aes is None or sha256 is None:
        raise ZashterminalError(
            "SecureCRT V2 encryption backend is not available (missing pycryptodomex)."
        )

    plain_bytes = password.encode("utf-8")
    if len(plain_bytes) > 0xFFFFFFFF:
        raise ZashterminalError("Password is too long for SecureCRT V2 format.")

    payload = (
        len(plain_bytes).to_bytes(4, "little")
        + plain_bytes
        + sha256.new(plain_bytes).digest()
    )
    block_size = aes.block_size
    padding_len = block_size - (len(payload) % block_size)
    padded = payload + os.urandom(padding_len)

    key = sha256.new(config_passphrase.encode("utf-8")).digest()
    cipher = aes.new(key, aes.MODE_CBC, iv=b"\x00" * block_size)
    encrypted_hex = cipher.encrypt(padded).hex()
    return f"{SECURECRT_V2_PREFIX}{encrypted_hex}"


def decrypt_session_password(value: str, config_passphrase: str = "") -> str:
    """
    Decrypt a SecureCRT Password V2-compatible value.
    Accepts either `<hex>` or `02:<hex>`.
    """
    aes, sha256 = _get_securecrt_v2_crypto_backend()
    if aes is None or sha256 is None:
        raise ZashterminalError(
            "SecureCRT V2 decryption backend is not available (missing pycryptodomex)."
        )

    cipher_hex = value[len(SECURECRT_V2_PREFIX) :] if is_securecrt_v2_password(value) else value
    if not cipher_hex:
        return ""

    try:
        ciphered_bytes = bytes.fromhex(cipher_hex)
    except ValueError as e:
        raise ZashterminalError("Invalid encrypted password format.") from e

    block_size = aes.block_size
    if len(ciphered_bytes) == 0 or len(ciphered_bytes) % block_size != 0:
        raise ZashterminalError("Invalid encrypted password length.")

    key = sha256.new(config_passphrase.encode("utf-8")).digest()
    cipher = aes.new(key, aes.MODE_CBC, iv=b"\x00" * block_size)
    padded_plain = cipher.decrypt(ciphered_bytes)

    plain_len = int.from_bytes(padded_plain[0:4], "little")
    plain_bytes = padded_plain[4 : 4 + plain_len]
    digest = padded_plain[4 + plain_len : 4 + plain_len + sha256.digest_size]

    if len(plain_bytes) != plain_len or len(digest) != sha256.digest_size:
        raise ZashterminalError("Invalid encrypted password payload.")
    if sha256.new(plain_bytes).digest() != digest:
        raise ZashterminalError("Encrypted password integrity check failed.")

    try:
        return plain_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ZashterminalError("Invalid decrypted password encoding.") from e


def store_password(session_name: str, password: str) -> bool:
    """
    Stores a password securely in the system keyring (GNOME Keyring, KWallet).
    """
    if not is_encryption_available():
        raise ZashterminalError("Secret Service API is not available.")

    try:
        attributes = {"session_name": session_name}
        # The last argument "cancellable" is None, and the function is synchronous.
        Secret.password_store_sync(
            SECRET_SCHEMA,
            attributes,
            Secret.COLLECTION_DEFAULT,
            f"Password for Zashterminal session '{session_name}'",
            password,
            None,
        )
        get_logger().info(f"Stored password securely for session '{session_name}'.")
        return True
    except Exception as e:
        get_logger().error(
            f"Failed to store password for session '{session_name}': {e}"
        )
        raise ZashterminalError(f"Failed to store password: {e}") from e


def lookup_password(session_name: str) -> Optional[str]:
    """
    Looks up a password from the system keyring.
    """
    if not is_encryption_available():
        raise ZashterminalError("Secret Service API is not available.")

    try:
        attributes = {"session_name": session_name}
        # The synchronous function returns the password or None if not found.
        password = Secret.password_lookup_sync(SECRET_SCHEMA, attributes, None)
        if password:
            get_logger().info(
                f"Retrieved password securely for session '{session_name}'."
            )
            return password
        return None
    except Exception as e:
        get_logger().error(
            f"Failed to lookup password for session '{session_name}': {e}"
        )
        raise ZashterminalError(f"Failed to lookup password: {e}") from e


def clear_password(session_name: str) -> bool:
    """
    Removes a password from the system keyring.
    """
    if not is_encryption_available():
        return False

    try:
        attributes = {"session_name": session_name}
        # The synchronous function returns True on success.
        return Secret.password_clear_sync(SECRET_SCHEMA, attributes, None)
    except Exception as e:
        get_logger().error(
            f"Failed to clear password for session '{session_name}': {e}"
        )
        return False


def export_all_passwords(sessions_store: Gio.ListStore) -> Dict[str, str]:
    """
    Exports all passwords from the keyring for sessions that use them.

    Args:
        sessions_store: The Gio.ListStore containing all SessionItem objects.

    Returns:
        A dictionary mapping session names to their passwords.
    """
    # Import moved inside the function to break the circular dependency
    from ..sessions.models import SessionItem

    logger = get_logger("zashterminal.crypto")
    passwords = {}
    if not is_encryption_available():
        logger.warning("Cannot export passwords: Secret Service API not available.")
        return passwords

    for i in range(sessions_store.get_n_items()):
        session = sessions_store.get_item(i)
        if isinstance(session, SessionItem) and session.uses_password_auth():
            try:
                password = lookup_password(session.name)
                if password:
                    passwords[session.name] = password
            except ZashterminalError as e:
                logger.error(f"Could not look up password for '{session.name}': {e}")

    logger.info(f"Exported {len(passwords)} passwords for backup.")
    return passwords
