"""Headway -- real-time transit reliability monitor.

FastAPI app: one ingestion task feeds a WebSocket fan-out plus a small REST
surface for anything that does not need to stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import config, ingest
from .state import Subscriber, hub, store

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("headway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(ingest.run_ingestion(stop))
    log.info("ingesting %s every %ss", config.FEED_URL, config.POLL_INTERVAL_SEC)
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Headway",
    description="Real-time transit reliability monitor over GTFS-Realtime.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # read-only public data; no credentials are accepted
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {
        "ok": store.feed_status["connected"],
        "subscribers": hub.subscriber_count,
        "filtered_subscribers": hub.filtered_subscribers,
        "vehicles": len(store.vehicles),
        "ticks_buffered": len(store.history),
        "learned_routes": store.baselines.learned_routes(),
        "status": store.feed_status,
    }


@app.get("/api/snapshot")
async def snapshot():
    """The same payload a WebSocket client receives on connect."""
    return store.replay()


@app.get("/api/events")
async def events(limit: int = 50):
    return {
        "events": list(store.events)[: max(1, min(limit, config.EVENT_LOG_SIZE))],
        "alerts": list(store.alerts),
    }


@app.get("/api/baselines")
async def baselines():
    """What each route-direction has learned its own normal spacing to be.

    Exposed because an adaptive threshold that cannot be inspected is worse
    than a fixed one -- a planner disputing a flagged pair needs to see the
    number it was judged against and how many samples produced it.
    """
    rows = [
        {"route_id": route_id, "direction_id": direction_id, **store.baselines.explain((route_id, direction_id))}
        for (route_id, direction_id) in store.baselines.keys()
    ]
    rows.sort(key=lambda r: (not r["learned"], r["threshold_m"]))
    return {"global_fallback_m": config.BUNCH_DISTANCE_M, "routes": rows}


@app.get("/api/history")
async def history():
    return {"history": list(store.history)}


async def _read_filters(
    websocket: WebSocket, sub: Subscriber, closed: asyncio.Event
) -> None:
    """Handle `subscribe` messages for the life of the socket.

    The client tells the server which routes and which map viewport it is
    actually drawing, and stops receiving the rest of the fleet. Changing the
    filter pushes a fresh replay straight away -- widening it otherwise leaves
    the map missing vehicles until the next poll, which reads as a bug.

    Every exit path sets `closed` so the send loop stops waiting on a queue no
    one will read again.
    """
    try:
        while True:
            # receive() rather than receive_json()/receive_text(): those decide
            # for themselves what a frame should have been and raise when it is
            # not -- a binary frame comes back without a "text" key and takes
            # the socket down with a KeyError. Inspecting the frame is the only
            # way to actually ignore the ones this endpoint does not care about.
            frame = await websocket.receive()
            if frame["type"] == "websocket.disconnect":
                break
            text = frame.get("text")
            if text is None:
                continue  # a binary frame; nothing here speaks binary
            try:
                message = json.loads(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # a malformed frame is ignored, not fatal
            if not isinstance(message, dict) or message.get("type") != "subscribe":
                continue
            if sub.set_filter(message.get("routes"), message.get("bbox")):
                try:
                    sub.queue.put_nowait(store.replay())
                except asyncio.QueueFull:
                    pass  # a tick is already queued; the filter applies to it
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        closed.set()


async def _send(websocket: WebSocket, sub: Subscriber, message: dict) -> None:
    """Serialise once and send.

    send_text rather than send_json: the compact separators cut roughly a
    quarter off the wire size compared with the default encoder, and the
    client measures the frame it receives.
    """
    await websocket.send_text(
        json.dumps(sub.shape(message), separators=(",", ":"))
    )


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    sub = hub.subscribe()
    closed = asyncio.Event()
    reader = asyncio.create_task(_read_filters(websocket, sub, closed))
    try:
        # Replay first so the page renders without waiting for the next tick.
        await _send(websocket, sub, store.replay())
        while True:
            # Race the next tick against the socket closing, so a disconnected
            # browser is reaped immediately instead of at the next poll.
            nxt = asyncio.ensure_future(sub.queue.get())
            gone = asyncio.ensure_future(closed.wait())
            done, pending = await asyncio.wait(
                {nxt, gone}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if nxt not in done:
                break
            await _send(websocket, sub, nxt.result())
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.info("websocket closed: %s", exc)
    finally:
        reader.cancel()
        hub.unsubscribe(sub)
