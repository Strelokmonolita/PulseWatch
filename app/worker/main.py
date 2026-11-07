"""Worker process entry point."""

import asyncio
from contextlib import suppress

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.worker.scheduler import MonitorWorker, run_worker


def main() -> None:
    """Run the monitoring worker until interrupted."""
    setup_logging(get_settings().log_level)
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


__all__ = ["MonitorWorker", "main", "run_worker"]


if __name__ == "__main__":
    main()
