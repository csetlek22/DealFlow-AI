"""Shared logging + progress plumbing for the discovery pipeline.

Centralises the terminal logger so every module in the pipeline emits the same
structured, timestamped, level-tagged lines, and provides a tiny ``emit``
helper for forwarding stage events to a caller-supplied reporter (e.g. the
Streamlit UI) without coupling backend modules to a specific UI framework.
"""

from __future__ import annotations

import logging
import time

_ROOT_LOGGER = "dealflow"


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under ``dealflow.<name>``.

    Configures the ``dealflow`` logger (and its single handler) exactly once,
    so modules can call this repeatedly without duplicating handlers or
    touching third-party loggers such as httpx/urllib3.
    """
    root = logging.getLogger(_ROOT_LOGGER)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
    return logging.getLogger(f"{_ROOT_LOGGER}.{name}")


def emit(progress, start_time: float, stage: str, message: str, **fields) -> None:
    """Send a progress event to ``progress`` if one was provided.

    ``progress`` is an optional ``Callable[[dict], None]``. Each event is a dict
    carrying ``stage``, ``message`` and ``elapsed`` (seconds since
    ``start_time``) plus any extra keyword fields (e.g. ``found``,
    ``processed``, ``cell_index``, ``total_cells``).
    """
    if progress is None:
        return

    event = {
        "stage": stage,
        "message": message,
        "elapsed": time.time() - start_time,
    }
    event.update(fields)
    progress(event)


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as ``45.2s`` or ``1m 23s``."""
    seconds = float(seconds or 0)
    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s"
