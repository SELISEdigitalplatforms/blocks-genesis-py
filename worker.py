import asyncio
import logging
import sys

from blocks_genesis.core.worker import WorkerConsoleApp


logger = logging.getLogger(__name__)


def main():
    app = WorkerConsoleApp("blocks_ai_worker")

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.exception("\n👋 Graceful shutdown by user")
    except Exception as e:
        logger.exception(f"💥 Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
