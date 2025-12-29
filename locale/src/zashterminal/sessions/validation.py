# zashterminal/sessions/validation.py

from gi.repository import Gio

from ..utils.translation_utils import _
from .models import SessionFolder, SessionItem
from .results import OperationResult


def validate_session_for_add(
    session: SessionItem, session_store: Gio.ListStore, folder_store: Gio.ListStore
) -> OperationResult:
    """Validates a new session before it is added."""
    # Basic model validation
    if not session.validate():
        errors = session.get_validation_errors()
        return OperationResult(
            False, _("Session validation failed: {}").format(", ".join(errors))
        )

    # Check for duplicate names within the same folder
    for i in range(session_store.get_n_items()):
        existing_session = session_store.get_item(i)
        if (
            existing_session.name == session.name
            and existing_session.folder_path == session.folder_path
        ):
            return OperationResult(
                False,
                _(
                    "A session with the name '{name}' already exists in this folder."
                ).format(name=session.name),
            )

    # Check if the target folder exists
    if session.folder_path:
        folder_exists = False
        for i in range(folder_store.get_n_items()):
            if folder_store.get_item(i).path == session.folder_path:
                folder_exists = True
                break
        if not folder_exists:
            return OperationResult(
                False,
                _("The target folder '{folder}' does not exist.").format(
                    folder=session.folder_path
                ),
            )

    return OperationResult(True, "Validation successful.")


def validate_folder_for_add(
    folder: SessionFolder, folder_store: Gio.ListStore
) -> OperationResult:
    """Validates a new folder before it is added."""
    # Basic model validation
    if not folder.validate():
        errors = folder.get_validation_errors()
        return OperationResult(
            False, _("Folder validation failed: {}").format(", ".join(errors))
        )

    # Check for duplicate paths
    for i in range(folder_store.get_n_items()):
        if folder_store.get_item(i).path == folder.path:
            return OperationResult(
                False,
                _("A folder with the path '{path}' already exists.").format(
                    path=folder.path
                ),
            )

    # Check if parent folder exists
    if folder.parent_path:
        parent_exists = False
        for i in range(folder_store.get_n_items()):
            if folder_store.get_item(i).path == folder.parent_path:
                parent_exists = True
                break
        if not parent_exists:
            return OperationResult(
                False,
                _("The parent folder '{folder}' does not exist.").format(
                    folder=folder.parent_path
                ),
            )

    return OperationResult(True, "Validation successful.")
