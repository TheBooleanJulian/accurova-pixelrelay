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
from .storage import LocalDiskStorage


def main() -> None:
    settings = load_settings()
    setup_logging()
    configure(settings.max_image_megapixels)
    db.init_db(settings.db_path)
    ctx = jobs.Context(settings, LocalDiskStorage(settings.files_dir), settings.db_path)
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
