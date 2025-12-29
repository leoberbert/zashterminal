# zashterminal/terminal/highlighter/output.py
"""
Output syntax highlighter for terminal commands.

This module provides OutputHighlighter, which applies regex-based
highlighting rules to terminal output text using ANSI escape codes.
"""

import threading
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

# Use regex module (PCRE2 backend) for ~50% faster matching
import regex as re_engine

from ...settings.highlights import HighlightRule, get_highlight_manager
from ...utils.logger import get_logger

from .constants import ANSI_RESET, ANSI_COLOR_PATTERN
from .rules import (
    CompiledRule,
    LiteralKeywordRule,
    extract_literal_keywords,
    extract_prefilter,
)

if TYPE_CHECKING:
    from ...settings.highlights import HighlightManager


# Singleton instance
_output_highlighter: Optional["OutputHighlighter"] = None


class OutputHighlighter:
    """
    Applies syntax highlighting to terminal output using ANSI escape codes.

    Supports:
    - Multi-group regex: colors list maps to capture groups
    - Theme-aware colors: resolves logical names via HighlightManager
    - Context-aware highlighting based on foreground process

    This is a singleton that supports multiple terminal proxies, each with
    their own context tracking.

    Performance Architecture:
    - Per-rule iteration with PCRE2 backend
    - Fast pre-filtering skips rules that cannot match
    - Tuples instead of lists for faster iteration
    - Early termination on "stop" action
    - Early return for ignored commands (native coloring tools)
    """

    def __init__(self):
        self.logger = get_logger("zashterminal.terminal.highlighter")
        self._manager: "HighlightManager" = get_highlight_manager()
        self._lock = threading.Lock()

        # Cache for compiled rules per context
        # Key: context_name, Value: Tuple of CompiledRule or LiteralKeywordRule
        self._context_rules_cache: Dict[
            str, Tuple[Union[CompiledRule, LiteralKeywordRule], ...]
        ] = {}

        # Global compiled rules (tuple for faster iteration)
        self._global_rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...] = ()

        # Per-proxy context tracking: proxy_id -> context_name
        self._proxy_contexts: Dict[int, str] = {}

        # Per-proxy full command tracking: proxy_id -> full command line
        # Used for Pygments highlighting to extract filenames from cat commands
        self._full_commands: Dict[int, str] = {}

        # Per-proxy flag to skip first output after context is set
        # This prevents the echoed command line from being highlighted
        # Key: proxy_id, Value: True if should skip first output
        self._skip_first_output: Dict[int, bool] = {}

        # Cached set of ignored commands (tools with native coloring)
        self._ignored_commands: frozenset = frozenset()
        self._refresh_ignored_commands()

        self.logger.info("Using regex module (PCRE2) for high-performance highlighting")

        # Reusable buffer for collecting matches (avoids allocation per line)
        self._match_buffer: List[Tuple[int, int, str]] = []

        self._refresh_rules()
        self._manager.connect("rules-changed", self._on_rules_changed)

    def _refresh_ignored_commands(self) -> None:
        """Refresh the cached set of ignored commands from settings."""
        try:
            from ...settings.manager import get_settings_manager

            settings = get_settings_manager()
            ignored_list = settings.get("ignored_highlight_commands", [])
            self._ignored_commands = frozenset(cmd.lower() for cmd in ignored_list)
            self.logger.debug(
                f"Refreshed ignored commands: {len(self._ignored_commands)} commands"
            )
        except Exception as e:
            self.logger.warning(f"Failed to refresh ignored commands: {e}")
            self._ignored_commands = frozenset()

    def refresh_ignored_commands(self) -> None:
        """Public method to refresh ignored commands (called when settings change)."""
        with self._lock:
            self._refresh_ignored_commands()

    def register_proxy(self, proxy_id: int) -> None:
        """Register a proxy with the highlighter."""
        with self._lock:
            self._proxy_contexts[proxy_id] = ""
            self.logger.debug(f"Registered proxy {proxy_id}")

    def unregister_proxy(self, proxy_id: int) -> None:
        """Unregister a proxy from the highlighter."""
        with self._lock:
            if proxy_id in self._proxy_contexts:
                del self._proxy_contexts[proxy_id]
            if proxy_id in self._full_commands:
                del self._full_commands[proxy_id]
            if proxy_id in self._skip_first_output:
                del self._skip_first_output[proxy_id]
            self.logger.debug(f"Unregistered proxy {proxy_id}")

    def _on_rules_changed(self, manager) -> None:
        self._refresh_rules()
        # Clear context cache when rules change
        with self._lock:
            self._context_rules_cache.clear()

    def _compile_rule(
        self, rule: HighlightRule
    ) -> Optional[Union[CompiledRule, LiteralKeywordRule]]:
        """
        Compile a single highlight rule for fast matching.

        For simple keyword patterns like \\b(word1|word2)\\b, returns a
        LiteralKeywordRule which is ~10-50x faster than regex.

        For complex patterns, returns CompiledRule with:
        - Compiled regex pattern (PCRE2)
        - ANSI color tuple
        - Pre-filter function for fast skipping
        """
        if not rule.enabled or not rule.pattern:
            return None

        # Get action (default: "next")
        action = getattr(rule, "action", "next")
        if action not in ("next", "stop"):
            action = "next"

        # Check if this is a simple keyword pattern that can use optimized matching
        literal_keywords = extract_literal_keywords(rule.pattern)
        if literal_keywords:
            # Use optimized literal keyword matching (no regex!)
            # Resolve first color only (keyword rules use single color)
            if rule.colors:
                ansi_color = self._manager.resolve_color_to_ansi(rule.colors[0])
            else:
                ansi_color = ""

            if not ansi_color:
                return None

            return LiteralKeywordRule(
                keywords=frozenset(literal_keywords),
                keyword_tuple=literal_keywords,
                ansi_color=ansi_color,
                action=action,
            )

        # Fall back to regex for complex patterns
        try:
            # Compile with regex engine (PCRE2) - use faster VERSION1 mode
            flags = re_engine.IGNORECASE | re_engine.VERSION1
            pattern = re_engine.compile(rule.pattern, flags)
            num_groups = pattern.groups

            # Resolve colors to ANSI sequences (tuple for faster iteration)
            ansi_colors = (
                tuple(
                    self._manager.resolve_color_to_ansi(c) if c else ""
                    for c in rule.colors
                )
                if rule.colors
                else ("",)
            )

            if not any(ansi_colors):
                return None

            # Create pre-filter for fast skipping
            prefilter = extract_prefilter(rule.pattern, rule.name)

            return CompiledRule(
                pattern=pattern,
                ansi_colors=ansi_colors,
                action=action,
                num_groups=num_groups,
                prefilter=prefilter,
            )

        except Exception as e:
            self.logger.warning(f"Invalid regex pattern in rule '{rule.name}': {e}")
            return None

    def _refresh_rules(self) -> None:
        """Refresh compiled rules from the manager."""
        with self._lock:
            rules_list = self._manager.rules
            self.logger.debug(f"Refreshing global rules: {len(rules_list)} total")

            compiled = []
            literal_count = 0
            regex_count = 0

            for rule in rules_list:
                cr = self._compile_rule(rule)
                if cr:
                    compiled.append(cr)
                    if isinstance(cr, LiteralKeywordRule):
                        literal_count += 1
                    else:
                        regex_count += 1

            # Convert to tuple for faster iteration
            self._global_rules = tuple(compiled)

            self.logger.debug(
                f"Compiled {len(self._global_rules)} global rules "
                f"({literal_count} literal, {regex_count} regex)"
            )

    def set_context(
        self, command_name: str, proxy_id: int = 0, full_command: str = ""
    ) -> bool:
        """
        Set the active context for highlighting for a specific proxy.

        This switches the highlighter to use command-specific rules in
        addition to global rules when the given command is detected.

        The command name is resolved via HighlightManager's trigger map,
        which maps aliases like "python3" -> "python" based on the
        "triggers" arrays defined in the JSON highlight files.

        Args:
            command_name: The command name (e.g., "ping", "docker", "python3").
                          Empty string or None resets to global rules only.
            proxy_id: The ID of the proxy to set context for.
            full_command: The full command line including arguments (for Pygments file highlighting).

        Returns:
            True if context changed, False if it was already set.
        """
        with self._lock:
            # Normalize empty/None to empty string
            if not command_name:
                resolved_context = ""
            else:
                # Check if command is in ignored list FIRST
                # If so, store the command name directly so it can be checked later
                if command_name.lower() in self._ignored_commands:
                    resolved_context = command_name.lower()
                else:
                    # Resolve command to canonical context name using trigger map
                    # This handles aliases like python3 -> python, pip3 -> pip, etc.
                    resolved_context = self._manager.get_context_for_command(
                        command_name
                    )
                    if not resolved_context:
                        # Command not in any context's triggers - use command name as-is
                        # This allows the ignored command check to work
                        resolved_context = command_name.lower()

            # Get current context for this proxy
            current_context = self._proxy_contexts.get(proxy_id, "")
            current_full_command = self._full_commands.get(proxy_id, "")

            # Always update full command if provided (needed for cat to get correct filename)
            if full_command:
                self._full_commands[proxy_id] = full_command
            elif proxy_id in self._full_commands:
                del self._full_commands[proxy_id]

            # Check if context actually changed
            if current_context == resolved_context:
                # Even if context didn't change, we may have updated full_command
                if full_command and full_command != current_full_command:
                    self.logger.debug(
                        f"Full command updated for proxy {proxy_id}: '{full_command[:50]}...'"
                    )
                    # Set skip flag since this is a new command execution
                    self._skip_first_output[proxy_id] = True
                return False

            self._proxy_contexts[proxy_id] = resolved_context

            # Set the skip flag to prevent highlighting the echoed command line
            # This flag will be consumed by the first data processing after Enter
            self._skip_first_output[proxy_id] = True

            if resolved_context:
                self.logger.debug(
                    f"Context changed for proxy {proxy_id}: '{current_context}' -> '{resolved_context}' (from '{command_name}')"
                )
            else:
                self.logger.debug(
                    f"Context cleared for proxy {proxy_id} (command '{command_name}' has no context)"
                )

            return True

    def get_full_command(self, proxy_id: int = 0) -> str:
        """Get the full command line for a specific proxy (for Pygments file highlighting)."""
        with self._lock:
            return self._full_commands.get(proxy_id, "")

    def get_context(self, proxy_id: int = 0) -> str:
        """Get the current context name for a specific proxy."""
        with self._lock:
            return self._proxy_contexts.get(proxy_id, "")

    def should_skip_first_output(self, proxy_id: int = 0) -> bool:
        """
        Check if first output should be skipped for highlighting.

        This is called when processing output to check if the output
        corresponds to the echoed command line that shouldn't be highlighted.
        The flag is consumed (cleared) after being checked.

        Args:
            proxy_id: The ID of the proxy to check.

        Returns:
            True if this is the first output after Enter and should be skipped.
        """
        with self._lock:
            if self._skip_first_output.get(proxy_id, False):
                self._skip_first_output[proxy_id] = False
                return True
            return False

    def clear_context(self, proxy_id: int = 0) -> None:
        """
        Clear the context for a specific proxy.

        This is used after a command completes to prevent re-processing
        on subsequent Enter key presses.
        """
        with self._lock:
            if proxy_id in self._proxy_contexts:
                old_context = self._proxy_contexts[proxy_id]
                del self._proxy_contexts[proxy_id]
                self.logger.debug(
                    f"Cleared context for proxy {proxy_id} (was: {old_context})"
                )
            if proxy_id in self._full_commands:
                del self._full_commands[proxy_id]

    def _compile_rules_for_context(
        self, context_name: str
    ) -> Tuple[Union[CompiledRule, LiteralKeywordRule], ...]:
        """
        Compile rules for a specific context.

        This merges global rules with context-specific rules.
        """
        rules = self._manager.get_rules_for_context(context_name)
        self.logger.debug(f"Compiling {len(rules)} rules for context '{context_name}'")

        compiled = []
        for rule in rules:
            cr = self._compile_rule(rule)
            if cr:
                compiled.append(cr)

        return tuple(compiled)

    def _get_active_rules(
        self, context: str = ""
    ) -> Tuple[Union[CompiledRule, LiteralKeywordRule], ...]:
        """
        Get the active compiled rules based on given context.

        Uses caching to avoid recompiling rules repeatedly.

        Args:
            context: The context name to get rules for.

        Returns:
            Tuple of CompiledRule or LiteralKeywordRule objects
        """
        # If no context or context-aware disabled, use global rules
        if not context or not self._manager.context_aware_enabled:
            return self._global_rules

        # Check cache
        if context in self._context_rules_cache:
            return self._context_rules_cache[context]

        # Compile and cache rules for this context
        context_rules = self._compile_rules_for_context(context)
        self._context_rules_cache[context] = context_rules

        return context_rules

    def highlight_text(self, text: str, proxy_id: int = 0) -> str:
        """
        Apply highlighting to text for a specific proxy.

        Uses optimized per-rule iteration with pre-filtering.
        Processes text line-by-line for streaming compatibility.

        Args:
            text: The text to highlight.
            proxy_id: The ID of the proxy to get context from.
        """
        if not text:
            return text

        # Fast path: get context and rules with minimal locking
        with self._lock:
            context = self.get_context(proxy_id)

            # Early return for ignored commands (tools with native coloring)
            # This preserves their ANSI colors and saves CPU
            if context and context.lower() in self._ignored_commands:
                return text

            rules = self._get_active_rules(context)

        if not rules:
            return text

        # Process outside the lock for better concurrency
        return self._apply_highlighting(text, rules)

    def highlight_line(self, line: str, proxy_id: int = 0) -> str:
        """
        Apply highlighting to a single line (streaming API).

        This is the preferred method for streaming data - call once per line
        as data arrives instead of buffering.

        Args:
            line: Single line of text to highlight.
            proxy_id: The ID of the proxy to get context from.
        """
        if not line:
            return line

        with self._lock:
            context = self.get_context(proxy_id)

            # Early return for ignored commands (tools with native coloring)
            # This preserves their ANSI colors and saves CPU
            if context and context.lower() in self._ignored_commands:
                return line

            rules = self._get_active_rules(context)

        if not rules:
            return line

        return self._apply_highlighting_to_line(line, rules)

    def _apply_highlighting(
        self,
        text: str,
        rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...],
    ) -> str:
        """
        Apply highlighting using optimized per-rule iteration.

        Args:
            text: The text to highlight
            rules: Tuple of CompiledRule or LiteralKeywordRule objects

        Returns:
            Text with ANSI color codes applied
        """
        # Split text into lines for streaming-friendly processing
        lines = text.split("\n")
        result_lines = []

        for line in lines:
            highlighted_line = self._apply_highlighting_to_line(line, rules)
            result_lines.append(highlighted_line)

        return "\n".join(result_lines)

    def _apply_highlighting_to_line(
        self,
        line: str,
        rules: Tuple[Union[CompiledRule, LiteralKeywordRule], ...],
    ) -> str:
        """
        Apply highlighting to a single line using per-rule iteration.

        Optimizations:
        - LiteralKeywordRule: O(1) set lookup + string.find() (no regex!)
        - CompiledRule: Pre-filtering skips regex when line cannot match
        - Tuple iteration is faster than list
        - Early termination on "stop" action
        - PCRE2 backend for regex rules

        Args:
            line: The line to highlight
            rules: Tuple of CompiledRule or LiteralKeywordRule objects

        Returns:
            Line with ANSI color codes applied
        """
        if not line:
            return line

        # Skip lines that already contain ANSI color codes to prevent double-highlighting
        # This handles cases where the shell or another tool has already colorized the output
        # Uses pre-compiled pattern for efficiency
        if "\x1b[" in line and ANSI_COLOR_PATTERN.search(line):
            return line

        # Pre-compute lowercase line for matching (O(n) once)
        line_lower = line.lower()

        # Reuse match buffer to avoid allocation per line
        matches = self._match_buffer
        matches.clear()
        should_stop = False

        for rule in rules:
            if should_stop:
                break

            # Handle LiteralKeywordRule (optimized path - no regex!)
            if isinstance(rule, LiteralKeywordRule):
                # Quick check: any keyword might be in line?
                # This is O(k) where k is number of keywords, but very fast
                has_potential = False
                for kw in rule.keyword_tuple:
                    if kw in line_lower:
                        has_potential = True
                        break

                if not has_potential:
                    continue

                # Find all keyword matches with word boundaries
                rule_matches = rule.find_matches(line, line_lower)
                rule_matched = bool(rule_matches)
                matches.extend(rule_matches)

                if rule_matched and rule.action == "stop":
                    should_stop = True
                continue

            # Handle CompiledRule (regex path)
            # Pre-filter: fast check if line might match
            if rule.prefilter is not None:
                if not rule.prefilter(line_lower):
                    continue  # Skip this rule - pre-filter failed

            try:
                rule_matched = False
                for match in rule.pattern.finditer(line):
                    rule_matched = True

                    if rule.num_groups > 0:
                        # Multi-group pattern: color each group separately
                        for group_idx in range(1, rule.num_groups + 1):
                            group_start = match.start(group_idx)
                            group_end = match.end(group_idx)

                            # Skip if group didn't match
                            if group_start == -1 or group_end == -1:
                                continue

                            # Get color for this group (fallback to first color)
                            color_idx = group_idx - 1
                            if color_idx < len(rule.ansi_colors):
                                ansi_color = rule.ansi_colors[color_idx]
                            else:
                                ansi_color = rule.ansi_colors[0]

                            # Skip if color is empty (intentionally no coloring)
                            if not ansi_color:
                                continue

                            matches.append((group_start, group_end, ansi_color))
                    else:
                        # No capture groups: color entire match
                        start, end = match.start(), match.end()
                        if rule.ansi_colors and rule.ansi_colors[0]:
                            matches.append((start, end, rule.ansi_colors[0]))

                # Check if we should stop processing after this rule
                if rule_matched and rule.action == "stop":
                    should_stop = True

            except Exception as e:
                # Log at debug level to help diagnose pattern issues without flooding logs
                if hasattr(self, "logger"):
                    self.logger.debug(f"Rule pattern matching failed: {e}")
                continue

        if not matches:
            return line

        # Sort by start position, then by length (longer first)
        matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))

        result = []
        last_end = 0
        covered_until = 0

        for start, end, color in matches:
            # Skip if already covered by previous match
            if start < covered_until:
                continue

            # Add text before this match
            if start > last_end:
                result.append(line[last_end:start])

            # Add colored match
            result.append(color)
            result.append(line[start:end])
            result.append(ANSI_RESET)

            last_end = end
            covered_until = end

        # Add remaining text
        if last_end < len(line):
            result.append(line[last_end:])

        return "".join(result)

    def is_enabled_for_type(self, terminal_type: str) -> bool:
        """Check if highlighting is enabled for the given terminal type."""
        if terminal_type == "local":
            return self._manager.enabled_for_local
        elif terminal_type in ("ssh", "sftp"):
            return self._manager.enabled_for_ssh
        return False


def get_output_highlighter() -> OutputHighlighter:
    """Get or create the singleton OutputHighlighter instance."""
    global _output_highlighter
    if _output_highlighter is None:
        _output_highlighter = OutputHighlighter()
    return _output_highlighter


__all__ = ["OutputHighlighter", "get_output_highlighter"]
