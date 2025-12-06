from __future__ import annotations
import logging, json, time, os
from contextlib import contextmanager

_LOG = logging.getLogger("aiinsp")


def setup_logging(level: int = logging.INFO,
                  log_dir: str = "logs",
                  log_file: str = "aiinsp.log") -> None:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, log_file)

    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    # Create handlers explicitly so we can have both console + file
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.setLevel(level)
    # Clear existing handlers to avoid duplicates if called twice
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)


def jlog(event: str, **kw):
    """JSON structured log entry."""
    _LOG.info(json.dumps({"event": event, **kw}, ensure_ascii=False))


@contextmanager
def tb(label: str, extra: dict | None = None):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        jlog("time_budget", label=label, ms=round(dt_ms, 3), **(extra or {}))
