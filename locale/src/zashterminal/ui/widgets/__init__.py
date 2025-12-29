# zashterminal/ui/widgets/__init__.py

"""Custom UI widgets for Zashterminal."""

from .ai_chat_panel import AIChatPanel
from .base_syntax_text_view import BaseSyntaxTextView
from .bash_text_view import BashTextView
from .conversation_history import ConversationHistoryPanel
from .form_widget_builder import (
    FieldConfig,
    FormWidgetBuilder,
    create_field_from_dict,
    create_field_from_form_field,
)
from .inline_context_menu import InlineContextMenu
from .regex_text_view import RegexTextView
from .ssh_error_banner import (
    BannerAction,
    BannerConfig,
    SSHErrorBanner,
    SSHErrorBannerManager,
    get_ssh_error_banner_manager,
)

__all__ = [
    "AIChatPanel",
    "BannerAction",
    "BannerConfig",
    "BaseSyntaxTextView",
    "BashTextView",
    "ConversationHistoryPanel",
    "FieldConfig",
    "FormWidgetBuilder",
    "InlineContextMenu",
    "RegexTextView",
    "SSHErrorBanner",
    "SSHErrorBannerManager",
    "create_field_from_dict",
    "create_field_from_form_field",
    "get_ssh_error_banner_manager",
]
