"""Background worker.

Embedded mode is started by the API process. External mode is `python -m app.worker`,
which polls the database. A Redis queue can replace the poller later without
changing process_job.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.services.pipeline import claim_next_id, process_job

log = logging.getLogger("prism.worker")


async def worker_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        job_id = await asyncio.to_thread(claim_next_id, SessionLocal)
        if job_id:
            log.info("processing review %s", job_id)
            await asyncio.to_thread(process_job, job_id, SessionLocal)
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.4)
        except TimeoutError:
            continue


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if settings.worker_mode == "disabled":
        raise SystemExit("PRISM_WORKER_MODE=disabled")
    stop = asyncio.Event()
    try:
        asyncio.run(worker_loop(stop))
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
