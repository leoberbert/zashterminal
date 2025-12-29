# zashterminal/core/tasks.py
"""
Global Task Manager for centralized background processing.

This module provides a singleton AsyncTaskManager that manages two separate
thread pools for I/O-bound and CPU-bound tasks. This prevents resource waste
from multiple ThreadPoolExecutor instances across the application and ensures
graceful shutdown.

Usage:
    # Submit I/O-bound task (file operations, network, etc.)
    future = AsyncTaskManager.get().submit_io(fetch_data, url)
    
    # Submit CPU-bound task (regex, parsing, etc.)
    future = AsyncTaskManager.get().submit_cpu(process_text, content)
    
    # Shutdown (called by app.py on exit)
    AsyncTaskManager.get().shutdown()
"""

import multiprocessing
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional, Set

from ..utils.logger import get_logger


class AsyncTaskManager:
    """
    Singleton Task Manager for centralized background task execution.
    
    Manages two thread pools:
    - IO pool: For I/O-bound tasks (file system, network, SSH connections)
    - CPU pool: For CPU-bound tasks (regex highlighting, search, parsing)
    
    Thread-safe for concurrent access from multiple parts of the application.
    """

    _instance: Optional["AsyncTaskManager"] = None
    _lock = threading.Lock()

    # Pool sizes
    IO_POOL_SIZE = 20
    CPU_POOL_SIZE = max(1, multiprocessing.cpu_count())

    def __init__(self):
        self.logger = get_logger("zashterminal.core.tasks")
        self._io_executor: Optional[ThreadPoolExecutor] = None
        self._cpu_executor: Optional[ThreadPoolExecutor] = None
        self._active_futures: Set[Future] = set()
        self._futures_lock = threading.Lock()
        self._is_shutdown = False

        self._initialize_pools()
        self.logger.info(
            f"AsyncTaskManager initialized (IO workers: {self.IO_POOL_SIZE}, "
            f"CPU workers: {self.CPU_POOL_SIZE})"
        )

    @classmethod
    def get(cls) -> "AsyncTaskManager":
        """
        Get the singleton AsyncTaskManager instance.
        
        Thread-safe lazy initialization.
        
        Returns:
            The global AsyncTaskManager instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """
        Reset the singleton instance (useful for testing).
        
        Warning: This will shutdown existing pools and disconnect all futures.
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown(wait=False)
                cls._instance = None

    def _initialize_pools(self) -> None:
        """Initialize the thread pool executors."""
        self._io_executor = ThreadPoolExecutor(
            max_workers=self.IO_POOL_SIZE,
            thread_name_prefix="zashterminal-io"
        )
        self._cpu_executor = ThreadPoolExecutor(
            max_workers=self.CPU_POOL_SIZE,
            thread_name_prefix="zashterminal-cpu"
        )

    def _track_future(self, future: Future) -> None:
        """Add a future to the tracking set."""
        with self._futures_lock:
            self._active_futures.add(future)
            future.add_done_callback(self._remove_future)

    def _remove_future(self, future: Future) -> None:
        """Remove a completed future from the tracking set."""
        with self._futures_lock:
            self._active_futures.discard(future)

    def submit_io(self, fn: Callable, *args, **kwargs) -> Optional[Future]:
        """
        Submit an I/O-bound task to the IO thread pool.
        
        Use for: File operations, network requests, SSH connections,
                database queries, subprocess calls.
        
        Args:
            fn: The function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
            
        Returns:
            A Future object, or None if the manager is shut down.
        """
        if self._is_shutdown or self._io_executor is None:
            self.logger.warning("IO task submitted after shutdown, ignoring")
            return None

        try:
            future = self._io_executor.submit(fn, *args, **kwargs)
            self._track_future(future)
            return future
        except RuntimeError as e:
            self.logger.error(f"Failed to submit IO task: {e}")
            return None

    def submit_cpu(self, fn: Callable, *args, **kwargs) -> Optional[Future]:
        """
        Submit a CPU-bound task to the CPU thread pool.
        
        Use for: Regex processing, text parsing, search operations,
                syntax highlighting, data transformation.
        
        Args:
            fn: The function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
            
        Returns:
            A Future object, or None if the manager is shut down.
        """
        if self._is_shutdown or self._cpu_executor is None:
            self.logger.warning("CPU task submitted after shutdown, ignoring")
            return None

        try:
            future = self._cpu_executor.submit(fn, *args, **kwargs)
            self._track_future(future)
            return future
        except RuntimeError as e:
            self.logger.error(f"Failed to submit CPU task: {e}")
            return None

    def shutdown(self, wait: bool = False) -> None:
        """
        Shutdown the task manager and all thread pools.
        
        Args:
            wait: If True, wait for all pending tasks to complete.
                  If False, cancel pending tasks and return immediately.
        """
        if self._is_shutdown:
            return

        self._is_shutdown = True
        self.logger.info(f"Shutting down AsyncTaskManager (wait={wait})")

        # Cancel all pending futures if not waiting
        if not wait:
            with self._futures_lock:
                cancelled_count = 0
                for future in list(self._active_futures):
                    if future.cancel():
                        cancelled_count += 1
                if cancelled_count > 0:
                    self.logger.info(f"Cancelled {cancelled_count} pending tasks")

        # Shutdown executors
        if self._io_executor is not None:
            self._io_executor.shutdown(wait=wait, cancel_futures=not wait)
            self._io_executor = None

        if self._cpu_executor is not None:
            self._cpu_executor.shutdown(wait=wait, cancel_futures=not wait)
            self._cpu_executor = None

        self.logger.info("AsyncTaskManager shutdown complete")

    @property
    def is_shutdown(self) -> bool:
        """Check if the task manager has been shut down."""
        return self._is_shutdown

    @property
    def pending_io_tasks(self) -> int:
        """Get approximate count of pending IO tasks."""
        with self._futures_lock:
            return sum(1 for f in self._active_futures
                      if not f.done() and "io" in str(getattr(f, '_thread_name_prefix', '')))

    @property
    def pending_cpu_tasks(self) -> int:
        """Get approximate count of pending CPU tasks."""
        with self._futures_lock:
            return sum(1 for f in self._active_futures
                      if not f.done() and "cpu" in str(getattr(f, '_thread_name_prefix', '')))


# Convenience functions for quick access
def submit_io(fn: Callable, *args, **kwargs) -> Optional[Future]:
    """Submit an I/O-bound task to the global task manager."""
    return AsyncTaskManager.get().submit_io(fn, *args, **kwargs)


def submit_cpu(fn: Callable, *args, **kwargs) -> Optional[Future]:
    """Submit a CPU-bound task to the global task manager."""
    return AsyncTaskManager.get().submit_cpu(fn, *args, **kwargs)
