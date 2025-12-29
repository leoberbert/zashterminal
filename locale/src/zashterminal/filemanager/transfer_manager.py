# zashterminal/filemanager/transfer_manager.py
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ..utils.icons import icon_button
from ..utils.logger import get_logger
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _


class TransferType(Enum):
    DOWNLOAD = "download"
    UPLOAD = "upload"


class TransferStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferItem:
    id: str
    filename: str
    local_path: str
    remote_path: str
    file_size: int
    transfer_type: TransferType
    status: TransferStatus
    is_directory: bool = False
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    progress: float = 0.0
    error_message: Optional[str] = None
    is_cancellable: bool = False
    cancellation_event: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    # Warmup tracking to avoid initial spurious progress from rsync
    first_stable_progress: float = -1.0  # First monotonically increasing progress value
    warmup_end_time: Optional[float] = None  # When warmup period ends

    def get_duration(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    def is_warmed_up(self) -> bool:
        """Returns True if the transfer has passed the warmup period."""
        if self.warmup_end_time is None:
            return False
        return time.time() >= self.warmup_end_time

    def get_stable_progress(self) -> float:
        """Returns progress adjusted for initial warmup period."""
        if not self.is_warmed_up():
            return 0.0
        if self.first_stable_progress < 0:
            return 0.0
        # Return actual progress minus the baseline
        adjusted = self.progress - self.first_stable_progress
        return max(0.0, min(100.0, adjusted))


class TransferManager(GObject.Object):
    __gsignals__ = {
        "transfer-started": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "transfer-progress": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),
        "transfer-completed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "transfer-failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "transfer-cancelled": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config_dir: str, file_operations=None):
        super().__init__()
        self.logger = get_logger(__name__)
        self.config_dir = config_dir
        self.history_file = os.path.join(config_dir, "transfer_history.json")
        self.file_operations = file_operations
        self.active_transfers: Dict[str, TransferItem] = {}
        self.history: List[TransferItem] = []

        # Thread safety for active_transfers access
        self._transfer_lock = threading.Lock()

        # Throttle progress updates to avoid UI flooding
        self._last_progress_update = 0.0
        self._progress_update_interval = 0.1  # 100ms minimum between UI updates

        self.progress_revealer: Optional[Gtk.Revealer] = None
        self.progress_row: Optional[Adw.ActionRow] = None  # Reference to the ActionRow
        self.progress_bar: Optional[Gtk.ProgressBar] = None
        self.cancel_button: Optional[Gtk.Button] = None

        # self.progress_revealer use red background

        self._load_history()

    def _load_history(self):
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item_data in data:
                        # Re-hydrate enums
                        item_data["transfer_type"] = TransferType(
                            item_data["transfer_type"]
                        )
                        item_data["status"] = TransferStatus(item_data["status"])
                        # These are not saved, so they are not in item_data
                        item_data.pop("cancellation_event", None)
                        item_data.pop("is_cancellable", None)
                        # For backward compatibility with old history files
                        if "is_directory" not in item_data:
                            item_data["is_directory"] = False
                        self.history.append(TransferItem(**item_data))
            # Keep history trimmed
            self.history = self.history[:50]
        except Exception as e:
            self.logger.error(f"Failed to load transfer history: {e}")

    def _save_history(self):
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            data_to_save = []
            # Persist only the last 50 items
            for item in self.history[:50]:
                # Create a serializable dictionary, excluding non-JSON types
                serializable_item = {
                    "id": item.id,
                    "filename": item.filename,
                    "local_path": item.local_path,
                    "remote_path": item.remote_path,
                    "file_size": item.file_size,
                    "transfer_type": item.transfer_type.value,
                    "status": item.status.value,
                    "is_directory": item.is_directory,
                    "start_time": item.start_time,
                    "end_time": item.end_time,
                    "progress": item.progress,
                    "error_message": item.error_message,
                }
                data_to_save.append(serializable_item)

            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save transfer history: {e}")

    def add_transfer(
        self,
        filename: str,
        local_path: str,
        remote_path: str,
        file_size: int,
        transfer_type: TransferType,
        is_cancellable: bool = False,
        is_directory: bool = False,
    ) -> str:
        transfer_id = str(uuid.uuid4())
        transfer_item = TransferItem(
            id=transfer_id,
            filename=filename,
            local_path=local_path,
            remote_path=remote_path,
            file_size=file_size,
            transfer_type=transfer_type,
            status=TransferStatus.PENDING,
            is_cancellable=is_cancellable,
            is_directory=is_directory,
        )
        with self._transfer_lock:
            self.active_transfers[transfer_id] = transfer_item
        return transfer_id

    def start_transfer(self, transfer_id: str):
        with self._transfer_lock:
            if transfer_id in self.active_transfers:
                transfer = self.active_transfers[transfer_id]
                transfer.status = TransferStatus.IN_PROGRESS
                transfer.start_time = time.time()
        self.emit("transfer-started", transfer_id)
        # Force immediate UI update when transfer starts
        self._last_progress_update = 0  # Reset throttle
        self._update_progress_display()

    def update_progress(self, transfer_id: str, progress: float):
        with self._transfer_lock:
            if transfer_id in self.active_transfers:
                transfer = self.active_transfers[transfer_id]
                current_time = time.time()

                # Warmup logic: wait 3 seconds before showing real progress
                # This avoids initial spurious values from rsync per-file progress
                if transfer.warmup_end_time is None:
                    transfer.warmup_end_time = current_time + 3.0

                # Track the first stable progress value after warmup
                if transfer.is_warmed_up() and transfer.first_stable_progress < 0:
                    transfer.first_stable_progress = progress

                transfer.progress = progress

        # Throttle progress updates to prevent UI flooding
        current_time = time.time()
        # Allow more frequent updates (50ms) for responsive UI
        if current_time - self._last_progress_update >= 0.05:
            self._last_progress_update = current_time
            self.emit("transfer-progress", transfer_id, progress)
            self._update_progress_display()

    def complete_transfer(self, transfer_id: str):
        transfer = None
        with self._transfer_lock:
            if transfer_id in self.active_transfers:
                transfer = self.active_transfers.pop(transfer_id)
                transfer.status = TransferStatus.COMPLETED
                transfer.end_time = time.time()
                transfer.progress = 100.0
                self.history.insert(0, transfer)

        if transfer:
            self.emit("transfer-completed", transfer_id)
            self._save_history()
            self._update_progress_display()

    def fail_transfer(self, transfer_id: str, error_message: str):
        transfer = None
        with self._transfer_lock:
            if transfer_id in self.active_transfers:
                transfer = self.active_transfers.pop(transfer_id)
                if "cancel" in error_message.lower():
                    transfer.status = TransferStatus.CANCELLED
                else:
                    transfer.status = TransferStatus.FAILED
                transfer.end_time = time.time()
                transfer.error_message = error_message
                self.history.insert(0, transfer)

        if transfer:
            if transfer.status == TransferStatus.CANCELLED:
                self.emit("transfer-cancelled", transfer_id)
            else:
                self.emit("transfer-failed", transfer_id, error_message)
            self._save_history()
            self._update_progress_display()

    def cancel_transfer(self, transfer_id: str):
        with self._transfer_lock:
            if transfer_id in self.active_transfers:
                transfer = self.active_transfers[transfer_id]
                if transfer.is_cancellable:
                    transfer.cancellation_event.set()
                    self.logger.info(
                        f"Cancellation requested for transfer {transfer_id}"
                    )

    def get_cancellation_event(self, transfer_id: str) -> Optional[threading.Event]:
        with self._transfer_lock:
            if transfer_id in self.active_transfers:
                return self.active_transfers[transfer_id].cancellation_event
        return None

    def get_transfer(self, transfer_id: str) -> Optional[TransferItem]:
        with self._transfer_lock:
            return self.active_transfers.get(transfer_id)

    def _update_progress_display(self):
        if self.progress_revealer:
            GLib.idle_add(self._do_update_progress_display)

    def _do_update_progress_display(self):
        # Take a snapshot of active transfers with lock
        with self._transfer_lock:
            active_count = len(self.active_transfers)
            if active_count == 0:
                self.progress_revealer.set_reveal_child(False)
                return False

            # Copy data needed for display to avoid holding lock during UI updates
            transfers_snapshot = list(self.active_transfers.values())

        self.progress_revealer.set_reveal_child(True)

        if active_count == 1:
            transfer = transfers_snapshot[0]
            self.progress_row.set_title(
                _("Transferring {filename}").format(filename=transfer.filename)
            )

            # Use stable progress to avoid initial spurious values
            display_progress = transfer.get_stable_progress()
            is_warmed = transfer.is_warmed_up()

            if not is_warmed:
                # Still in warmup period - show preparing message
                subtitle_parts = [_("Starting...")]
            else:
                subtitle_parts = [f"{display_progress:.1f}%"]

                # Calculate speed and ETA only after warmup
                if transfer.start_time and transfer.file_size > 0:
                    # Use time since warmup ended for more accurate speed
                    elapsed_since_warmup = time.time() - (
                        transfer.warmup_end_time or transfer.start_time
                    )
                    if elapsed_since_warmup > 0.5:
                        bytes_transferred = (
                            display_progress / 100.0
                        ) * transfer.file_size
                        speed = (
                            bytes_transferred / elapsed_since_warmup
                            if elapsed_since_warmup > 0
                            else 0
                        )
                        if speed > 0:
                            subtitle_parts.append(self._format_speed(speed))
                            remaining_bytes = transfer.file_size - bytes_transferred
                            if remaining_bytes > 0:
                                eta = remaining_bytes / speed
                                subtitle_parts.append(
                                    _("{time} left").format(
                                        time=self._format_duration(eta)
                                    )
                                )
                elif transfer.start_time and transfer.is_directory:
                    # For directories without known size, just show elapsed time
                    elapsed = time.time() - transfer.start_time
                    if elapsed > 1:
                        subtitle_parts.append(
                            _("{time} elapsed").format(
                                time=self._format_duration(elapsed)
                            )
                        )

            self.progress_row.set_subtitle(" • ".join(subtitle_parts))
            self.progress_bar.set_fraction(display_progress / 100.0)
        else:
            # Calculate aggregated statistics for multiple transfers
            total_bytes = 0
            total_bytes_transferred = 0
            total_stable_progress = 0.0
            earliest_warmup_end = None
            all_warmed = True

            for transfer in transfers_snapshot:
                display_progress = transfer.get_stable_progress()
                total_bytes += transfer.file_size
                bytes_done = (display_progress / 100.0) * transfer.file_size
                total_bytes_transferred += bytes_done
                total_stable_progress += display_progress

                if not transfer.is_warmed_up():
                    all_warmed = False

                if transfer.warmup_end_time:
                    if (
                        earliest_warmup_end is None
                        or transfer.warmup_end_time < earliest_warmup_end
                    ):
                        earliest_warmup_end = transfer.warmup_end_time

            # Calculate overall progress - use average if we don't have sizes
            if total_bytes > 0:
                overall_progress = total_bytes_transferred / total_bytes * 100.0
            else:
                # Fallback: average progress across all transfers
                overall_progress = (
                    total_stable_progress / active_count if active_count > 0 else 0
                )

            self.progress_row.set_title(
                _("Transferring {count} files").format(count=active_count)
            )

            if not all_warmed:
                # Still in warmup period
                subtitle_parts = [_("Starting...")]
            else:
                subtitle_parts = [f"{overall_progress:.1f}%"]

                # Calculate aggregate speed and ETA only after warmup
                if earliest_warmup_end is not None and total_bytes > 0:
                    elapsed_since_warmup = time.time() - earliest_warmup_end
                    if elapsed_since_warmup > 0.5 and total_bytes_transferred > 0:
                        speed = total_bytes_transferred / elapsed_since_warmup
                        subtitle_parts.append(self._format_speed(speed))

                        remaining_bytes = total_bytes - total_bytes_transferred
                        if remaining_bytes > 0 and speed > 0:
                            eta = remaining_bytes / speed
                            subtitle_parts.append(
                                _("{time} left").format(time=self._format_duration(eta))
                            )
                elif earliest_warmup_end is not None:
                    # When we don't have file sizes, show elapsed time
                    elapsed_since_warmup = time.time() - earliest_warmup_end
                    if elapsed_since_warmup > 1:
                        subtitle_parts.append(
                            _("{time} elapsed").format(
                                time=self._format_duration(elapsed_since_warmup)
                            )
                        )

            self.progress_row.set_subtitle(" • ".join(subtitle_parts))
            self.progress_bar.set_fraction(overall_progress / 100.0)

        return False

    def _on_cancel_all_clicked(self, button):
        with self._transfer_lock:
            transfer_ids = list(self.active_transfers.keys())
        for transfer_id in transfer_ids:
            self.cancel_transfer(transfer_id)

    def create_progress_widget(self) -> Gtk.Widget:
        self.progress_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
        )
        self.progress_revealer.add_css_class("background")

        # Create the ActionRow and store a reference to it
        self.progress_row = Adw.ActionRow()

        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        self.progress_row.add_prefix(self.progress_bar)

        self.cancel_button = icon_button("process-stop-symbolic")
        get_tooltip_helper().add_tooltip(self.cancel_button, _("Cancel All Transfers"))
        self.cancel_button.set_valign(Gtk.Align.CENTER)
        self.cancel_button.add_css_class("flat")
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.connect("clicked", self._on_cancel_all_clicked)
        self.progress_row.add_suffix(self.cancel_button)

        self.progress_revealer.set_child(self.progress_row)
        return self.progress_revealer

    def _format_file_size(self, size_bytes: int) -> str:
        if not isinstance(size_bytes, (int, float)) or size_bytes < 0:
            return "0 B"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024**2:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024**3:
            return f"{size_bytes / 1024**2:.1f} MB"
        return f"{size_bytes / 1024**3:.1f} GB"

    def _format_speed(self, bytes_per_second: float) -> str:
        if not isinstance(bytes_per_second, (int, float)) or bytes_per_second <= 0:
            return "0 B/s"
        if bytes_per_second < 1024:
            return f"{bytes_per_second:.1f} B/s"
        if bytes_per_second < 1024**2:
            return f"{bytes_per_second / 1024:.1f} KB/s"
        if bytes_per_second < 1024**3:
            return f"{bytes_per_second / 1024**2:.1f} MB/s"
        return f"{bytes_per_second / 1024**3:.1f} GB/s"

    def _format_duration(self, seconds: float) -> str:
        if not isinstance(seconds, (int, float)) or seconds < 0:
            return "0s"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

