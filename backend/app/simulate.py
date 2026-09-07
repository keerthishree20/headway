"""Synthetic fleet for offline development.

Enabled only with SIMULATE=1. It exists so the dashboard can be worked on
without a network, and so the bunching detector can be exercised on demand --
one route here is seeded to bunch deliberately. It is never the default: a
civic dashboard demoing invented numbers is worth nothing.
"""

from __future__ import annotations

import asyncio
import math
import random
import time

from . import config

_ROUTES = [
    # route_id, centre lat/lon, radius in degrees, vehicles, speed factor
    ("SIM-1", 42.3601, -71.0589, 0.020, 8, 1.0),
    ("SIM-2", 42.3736, -71.1097, 0.015, 6, 0.8),
    ("SIM-BUNCH", 42.3400, -71.0800, 0.012, 5, 0.6),
]


def _build_fleet() -> list[dict]:
    fleet = []
    for route_id, lat, lon, radius, count, speed in _ROUTES:
        for i in range(count):
            # The bunching route starts three vehicles almost on top of each
            # other so the detector has something to find immediately.
            if route_id == "SIM-BUNCH" and i < 3:
                phase = 0.02 * i
            else:
                phase = (2 * math.pi / count) * i
            fleet.append(
                {
                    "id": f"{route_id}-{i:02d}",
                    "route_id": route_id,
                    "centre": (lat, lon),
                    "radius": radius,
                    "phase": phase,
                    "rate": 0.02 * speed * random.uniform(0.85, 1.15),
                    "direction_id": i % 2,
                    "stalled_until": 0.0,
                }
            )
    return fleet


async def run(stop: asyncio.Event, hub, store) -> None:
    fleet = _build_fleet()
    while not stop.is_set():
        now = time.time()
        vehicles = []
        for v in fleet:
            # Occasionally freeze a vehicle so the ghost-bus detector fires.
            if random.random() < 0.004:
                v["stalled_until"] = now + config.STALE_AFTER_SEC * 1.5
            stalled = now < v["stalled_until"]
            if not stalled:
                v["phase"] += v["rate"]
            lat = v["centre"][0] + v["radius"] * math.sin(v["phase"])
            lon = v["centre"][1] + v["radius"] * math.cos(v["phase"]) * 1.35
            vehicles.append(
                {
                    "id": v["id"],
                    "label": v["id"],
                    "route_id": v["route_id"],
                    "trip_id": f"trip-{v['id']}",
                    "direction_id": v["direction_id"],
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "bearing": round((math.degrees(v["phase"]) + 90) % 360, 1),
                    "speed": 0.0 if stalled else round(8 * v["rate"] / 0.02, 2),
                    "ts": v["stalled_until"] - config.STALE_AFTER_SEC * 1.5 if stalled else now,
                    "stop_id": None,
                    "status": "stopped_at" if stalled else "in_transit_to",
                    "occupancy": random.choice(["many_seats", "few_seats", "standing_room"]),
                }
            )
        hub.broadcast(store.record_tick(vehicles, now))
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.POLL_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue
