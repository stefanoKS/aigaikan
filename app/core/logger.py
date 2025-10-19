from __future__ import annotations
import logging, json, time
from contextlib import contextmanager

_LOG = logging.getLogger("aiinsp")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def jlog(event: str, **kw):
    _LOG.info(json.dumps({"event": event, **kw}))


@contextmanager
def tb(label: str, extra: dict | None = None):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        jlog("time_budget", label=label, ms=round(dt_ms, 3), **(extra or {}))