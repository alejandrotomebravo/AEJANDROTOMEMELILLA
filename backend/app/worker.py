"""Trabajador independiente: ``python -m app.worker``.

Úselo con INLINE_WORKER=false para separar la API del procesamiento (por
ejemplo, la API en un servidor pequeño y el trabajador en una máquina con GPU
que comparta el volumen de datos).
"""
from __future__ import annotations

import logging
import signal
import threading

from . import db
from .config import settings
from .jobs import queue, tasks  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    settings.ensure_dirs()
    db.conn()
    w = queue.Worker(settings.worker_concurrency)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    w.start()
    logging.info("Trabajador iniciado (%s hilos)", settings.worker_concurrency)
    stop.wait()
    w.stop()


if __name__ == "__main__":
    main()
