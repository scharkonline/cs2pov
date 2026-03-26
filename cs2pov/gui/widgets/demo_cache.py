"""Shared demo parse cache - parses each demo file once and shares results."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..workers import ParseWorker


class DemoCache(QObject):
    """Cache parsed demo results so the same file isn't parsed twice.

    All cache mutations happen on the main thread via signal handlers,
    so no explicit locking is needed.
    """

    parse_complete = Signal(str, object)  # (abs_path, DemoInfo)
    parse_error = Signal(str, str)  # (abs_path, error_message)
    parse_started = Signal(str)  # (abs_path,)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict[str, object] = {}  # abs_path -> DemoInfo
        self._in_flight: dict[str, ParseWorker] = {}  # abs_path -> active worker

    def request_parse(self, path: str):
        """Request a demo parse. Emits parse_complete when done.

        If already cached, emits immediately. If already in-flight,
        the caller will get the signal when the existing parse finishes.
        """
        resolved = str(Path(path).resolve())

        if resolved in self._cache:
            self.parse_complete.emit(resolved, self._cache[resolved])
            return

        if resolved in self._in_flight:
            return  # Already parsing, signal will fire when done

        self.parse_started.emit(resolved)

        worker = ParseWorker(Path(resolved))
        worker.finished.connect(lambda result, p=resolved: self._on_finished(p, result))
        worker.error.connect(lambda msg, p=resolved: self._on_error(p, msg))
        self._in_flight[resolved] = worker
        worker.start()

    def _on_finished(self, path: str, result):
        demo_info, _ = result  # We only cache DemoInfo, not timeline
        self._cache[path] = demo_info
        self._in_flight.pop(path, None)
        self.parse_complete.emit(path, demo_info)

    def _on_error(self, path: str, msg: str):
        self._in_flight.pop(path, None)
        self.parse_error.emit(path, msg)

    def get_cached(self, path: str):
        """Return cached DemoInfo or None."""
        resolved = str(Path(path).resolve())
        return self._cache.get(resolved)
