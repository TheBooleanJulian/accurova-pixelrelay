"""Run the background worker as its own process (use with WORKER_MODE=off on the web process).

python -m pixelrelay.worker
"""

from __future__ import annotations

import logging
import signal

from . import db, jobs
from .config import load_settings
from .images import configure
from .logging_setup import setup_logging
from .storage import from_settings as storage_from_settings


def main() -> None:
    settings = load_settings()
    setup_logging()
    configure(settings.max_image_megapixels)
    db.init_db(settings.db_target, settings.db_schema)
    ctx = jobs.Context(settings, storage_from_settings(settings))
    worker = jobs.Worker(ctx)
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    logging.getLogger("pixelrelay").info("worker started")
    worker.start()
    try:
        while worker.is_alive():
            worker.join(1.0)
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
