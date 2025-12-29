# zashterminal/utils/crypto.py

from typing import Dict, Optional

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


def is_encryption_available() -> bool:
    """Checks if the libsecret library is available."""
    try:
        # A successful import is already a good indication.
        return True
    except (ImportError, ValueError):
        return False


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
