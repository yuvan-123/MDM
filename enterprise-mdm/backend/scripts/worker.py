"""CLI entrypoint for the Phase 7 background-job worker loop.

Mirrors scripts/seed_admin.py's bootstrap pattern: adds the backend root to
sys.path so it can be invoked directly, then drives the real
AsyncSessionLocal against app/workers/loop.py::run_worker_loop.

Phase 15: wires SIGTERM/SIGINT to the `stop_event` `run_worker_loop`
already accepted as a parameter since Phase 7 but that nothing here ever
set — without this, `docker stop` / a Kubernetes pod eviction / any
orchestrator's graceful-shutdown signal hard-kills the process wherever
it happens to be (mid-`await`, between claiming a job and marking it
processed), rather than letting the current `run_worker_once` cycle finish
and exit cleanly at its own next poll boundary. This does not change
`run_worker_loop`/`run_worker_once` at all — both already only ever
process one job to completion per cycle before checking the loop
condition again, which is exactly what makes cooperative shutdown here
safe: at most one job's completion is deferred by a few seconds, never
half a job left claimed-but-unprocessed.

Usage:
    python scripts/worker.py
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from app.core.logging import configure_logging, get_logger
from app.database.session import AsyncSessionLocal
from app.workers.loop import run_worker_loop

configure_logging()
logger = get_logger(__name__)


async def _run() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(sig_name: str) -> None:
        logger.info("worker_shutdown_requested", signal=sig_name)
        stop_event.set()

    # SIGTERM is what `docker stop`/Kubernetes send by default; SIGINT
    # covers Ctrl+C during local/manual runs. Windows only supports
    # SIGINT/SIGBREAK on asyncio's loop — add_signal_handler for SIGTERM
    # raises NotImplementedError there, so this degrades to "Ctrl+C still
    # works, SIGTERM falls back to the OS default" on that platform rather
    # than crashing the worker at startup.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            logger.warning("worker_signal_handler_unavailable", signal=sig.name)

    logger.info("worker_starting")
    await run_worker_loop(AsyncSessionLocal, stop_event=stop_event)
    logger.info("worker_stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
