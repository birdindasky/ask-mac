"""Self-update endpoints: check GitHub for a newer release, and perform an
in-place swap of the running .app."""
from __future__ import annotations

import asyncio
import json as _json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .. import updater

router = APIRouter(prefix="/api/update", tags=["update"])


@router.get("/check")
async def get_check():
    # Network call — run off the event loop.
    return await asyncio.to_thread(updater.check_for_update)


@router.post("/perform")
async def post_perform(body: dict):
    """SSE stream of update progress. The final `relaunching` event is
    followed by the app quitting (the detached swap script relaunches it)."""
    dmg_url = (body or {}).get("dmg_url")

    async def _gen():
        # Drive the blocking generator on a worker thread, forwarding events.
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def _run():
            try:
                for ev in updater.perform_update(dmg_url):
                    loop.call_soon_threadsafe(q.put_nowait, ev)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, DONE)

        loop.run_in_executor(None, _run)
        while True:
            ev = await q.get()
            if ev is DONE:
                break
            yield f"event: {ev.get('stage','progress')}\ndata: {_json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(_gen(), media_type="text/event-stream")
