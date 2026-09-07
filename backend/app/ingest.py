"""GTFS-Realtime ingestion.

A single asyncio task polls the agency feed, decodes the protobuf, and hands a
normalised snapshot to the store, which broadcasts it. Failures back off
exponentially and are surfaced to the dashboard instead of being swallowed.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from google.transit import gtfs_realtime_pb2

from . import config, simulate
from .state import hub, store

log = logging.getLogger("headway.ingest")

CURRENT_STATUS = {0: "incoming_at", 1: "stopped_at", 2: "in_transit_to"}
OCCUPANCY = {
    0: "empty",
    1: "many_seats",
    2: "few_seats",
    3: "standing_room",
    4: "crushed_standing_room",
    5: "full",
    6: "not_accepting",
}


def parse_feed(payload: bytes) -> tuple[list[dict], float | None]:
    """Decode VehiclePositions into flat dicts the frontend can use directly."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)

    vehicles: list[dict] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        if not v.HasField("position"):
            continue
        pos = v.position
        vehicles.append(
            {
                "id": v.vehicle.id or entity.id,
                "label": v.vehicle.label or None,
                "route_id": v.trip.route_id or None,
                "trip_id": v.trip.trip_id or None,
                "direction_id": v.trip.direction_id if v.trip.HasField("direction_id") else 0,
                "lat": round(pos.latitude, 6),
                "lon": round(pos.longitude, 6),
                "bearing": round(pos.bearing, 1) if pos.HasField("bearing") else None,
                "speed": round(pos.speed, 2) if pos.HasField("speed") else None,
                "ts": float(v.timestamp) if v.timestamp else None,
                "stop_id": v.stop_id or None,
                "status": CURRENT_STATUS.get(v.current_status),
                "occupancy": OCCUPANCY.get(v.occupancy_status)
                if v.HasField("occupancy_status")
                else None,
            }
        )

    header_ts = float(feed.header.timestamp) if feed.header.timestamp else None
    return vehicles, header_ts


async def _fetch(client: httpx.AsyncClient) -> tuple[list[dict], float | None]:
    headers = {"User-Agent": "headway-transit-monitor/1.0"}
    if config.FEED_API_KEY and config.FEED_API_KEY_HEADER:
        headers[config.FEED_API_KEY_HEADER] = config.FEED_API_KEY
    resp = await client.get(config.FEED_URL, headers=headers)
    resp.raise_for_status()
    return parse_feed(resp.content)


async def run_ingestion(stop: asyncio.Event) -> None:
    """Poll forever until `stop` is set. Started from the app lifespan."""
    if config.SIMULATE:
        log.warning("SIMULATE=1 -- streaming a synthetic fleet, not live data")
        await simulate.run(stop, hub, store)
        return

    backoff = config.POLL_INTERVAL_SEC
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SEC, follow_redirects=True) as client:
        while not stop.is_set():
            started = time.monotonic()
            try:
                vehicles, feed_ts = await _fetch(client)
                message = store.record_tick(vehicles, feed_ts)
                hub.broadcast(message)
                backoff = config.POLL_INTERVAL_SEC
                log.info(
                    "tick: %d vehicles, %d bunched pairs, %d stale",
                    message["metrics"]["active"],
                    message["metrics"]["bunched_pairs"],
                    message["metrics"]["stale"],
                )
            except Exception as exc:  # network, decode, anything the feed throws
                log.warning("feed poll failed: %s", exc)
                hub.broadcast(store.record_failure(f"{type(exc).__name__}: {exc}"))
                backoff = min(backoff * 2, config.BACKOFF_MAX_SEC)

            elapsed = time.monotonic() - started
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(backoff - elapsed, 0.5))
            except asyncio.TimeoutError:
                continue
