# zashterminal/data/ai_history_manager.py

"""AI chat history persistence using JSON with conversation support."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..settings.config import get_config_paths
from ..utils.logger import get_logger


class AIHistoryManager:
    """Manages AI chat history persistence using JSON format with conversation support."""

    def __init__(self):
        self.logger = get_logger("zashterminal.data.ai_history_manager")
        self._config_paths = get_config_paths()
        self._history_file = self._config_paths.CONFIG_DIR / "ai_history.json"
        self._conversations: List[Dict[str, Any]] = []
        self._current_conversation_id: Optional[str] = None
        self._max_conversations = 100  # Limit number of conversations
        self._load_history()

    def _load_history(self) -> None:
        """Load chat history from JSON file."""
        try:
            if self._history_file.exists():
                with open(self._history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                    # Support both old and new format
                    if "conversations" in data:
                        self._conversations = data.get("conversations", [])
                        self._current_conversation_id = data.get("current_conversation_id")
                    elif "history" in data:
                        # Migrate old format to new
                        old_history = data.get("history", [])
                        if old_history:
                            conv_id = str(uuid.uuid4())
                            self._conversations = [{
                                "id": conv_id,
                                "created_at": old_history[0].get("timestamp", datetime.now().isoformat()),
                                "messages": old_history
                            }]
                            self._current_conversation_id = conv_id
                        else:
                            self._conversations = []
                            self._current_conversation_id = None

                    self.logger.info(
                        f"Loaded {len(self._conversations)} AI conversations from history"
                    )
            else:
                self._conversations = []
                self._current_conversation_id = None
                self.logger.info("No AI history file found, starting fresh")
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse AI history JSON: {e}")
            self._conversations = []
            self._current_conversation_id = None
        except Exception as e:
            self.logger.error(f"Failed to load AI history: {e}")
            self._conversations = []
            self._current_conversation_id = None

    def _save_history(self) -> None:
        """Save chat history to JSON file."""
        try:
            # Ensure config directory exists
            self._config_paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            # Trim conversations if too many
            if len(self._conversations) > self._max_conversations:
                self._conversations = self._conversations[-self._max_conversations:]

            data = {
                "conversations": self._conversations,
                "current_conversation_id": self._current_conversation_id
            }

            # Write atomically using a temp file
            temp_file = self._history_file.with_suffix(".json.tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Move temp file to final location
            temp_file.replace(self._history_file)

            # Set secure permissions
            os.chmod(self._history_file, 0o600)

            self.logger.debug(f"Saved {len(self._conversations)} AI conversations to history")
        except Exception as e:
            self.logger.error(f"Failed to save AI history: {e}")

    def _get_current_conversation(self) -> Optional[Dict[str, Any]]:
        """Get the current conversation or None if no current conversation."""
        if not self._current_conversation_id:
            return None
        for conv in self._conversations:
            if conv.get("id") == self._current_conversation_id:
                return conv
        return None

    def _ensure_current_conversation(self) -> Dict[str, Any]:
        """Ensure there's a current conversation, creating one if needed."""
        conv = self._get_current_conversation()
        if conv is None:
            conv = self.new_conversation()
        return conv

    def new_conversation(self) -> Dict[str, Any]:
        """Start a new conversation."""
        conv_id = str(uuid.uuid4())
        conv = {
            "id": conv_id,
            "created_at": datetime.now().isoformat(),
            "messages": []
        }
        self._conversations.append(conv)
        self._current_conversation_id = conv_id
        self._save_history()
        self.logger.info(f"Created new conversation: {conv_id}")
        return conv

    def get_current_conversation(self) -> Optional[Dict[str, Any]]:
        """Get the current conversation."""
        return self._get_current_conversation()

    def get_all_conversations(self) -> List[Dict[str, Any]]:
        """Get all conversations, newest first."""
        return list(reversed(self._conversations))

    def load_conversation(self, conv_id: str) -> bool:
        """Load a specific conversation by ID."""
        for conv in self._conversations:
            if conv.get("id") == conv_id:
                self._current_conversation_id = conv_id
                self._save_history()
                self.logger.info(f"Loaded conversation: {conv_id}")
                return True
        return False

    def add_message(
        self, role: str, content: str, commands: Optional[List[str]] = None
    ) -> None:
        """
        Add a message to the current conversation.

        Args:
            role: Either "user" or "assistant"
            content: The message content
            commands: Optional list of command strings for assistant messages
        """
        if not content or not content.strip():
            return

        conv = self._ensure_current_conversation()

        entry = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "content": content.strip(),
        }
        if commands:
            entry["commands"] = commands

        conv["messages"].append(entry)
        self._save_history()

    def add_user_message(self, content: str) -> None:
        """Add a user message to the history."""
        self.add_message("user", content)

    def add_assistant_message(
        self, content: str, commands: Optional[List[str]] = None
    ) -> None:
        """Add an assistant message to the history."""
        self.add_message("assistant", content, commands)

    def get_history(self) -> List[Dict[str, Any]]:
        """
        Get the current conversation's messages.

        Returns:
            List of message dictionaries with timestamp, role, and content
        """
        conv = self._get_current_conversation()
        if conv:
            return conv.get("messages", []).copy()
        return []

    def get_recent_history(self, count: int = 50) -> List[Dict[str, Any]]:
        """
        Get the most recent messages from current conversation.

        Args:
            count: Number of messages to retrieve

        Returns:
            List of recent message dictionaries
        """
        history = self.get_history()
        return history[-count:] if count < len(history) else history

    def clear_history(self) -> None:
        """Clear current conversation's messages."""
        conv = self._get_current_conversation()
        if conv:
            conv["messages"] = []
            self._save_history()
            self.logger.info("Cleared current conversation history")

    def delete_conversation(self, conv_id: str) -> bool:
        """
        Delete a specific conversation by ID.

        Args:
            conv_id: The ID of the conversation to delete

        Returns:
            True if deleted, False if not found
        """
        for i, conv in enumerate(self._conversations):
            if conv.get("id") == conv_id:
                del self._conversations[i]
                # If we deleted the current conversation, clear the reference
                if self._current_conversation_id == conv_id:
                    self._current_conversation_id = None
                self._save_history()
                self.logger.info(f"Deleted conversation: {conv_id}")
                return True
        return False

    def clear_all_history(self) -> None:
        """Clear all conversations."""
        self._conversations = []
        self._current_conversation_id = None
        self._save_history()
        self.logger.info("Cleared all AI chat history")


# Global instance for convenience
_history_manager: Optional[AIHistoryManager] = None


def get_ai_history_manager() -> AIHistoryManager:
    """Get the global AI history manager instance."""
    global _history_manager
    if _history_manager is None:
        _history_manager = AIHistoryManager()
    return _history_manager
