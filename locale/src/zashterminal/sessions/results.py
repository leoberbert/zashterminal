# zashterminal/sessions/results.py

from typing import List, Optional, Union

from .models import SessionFolder, SessionItem


class OperationResult:
    """Represents the result of a session or folder operation."""

    def __init__(
        self,
        success: bool,
        message: str = "",
        item: Optional[Union[SessionItem, SessionFolder]] = None,
        warnings: Optional[List[str]] = None,
    ):
        self.success = success
        self.message = message
        self.item = item
        self.warnings = warnings or []
