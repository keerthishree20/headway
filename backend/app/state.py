"""In-process fan-out hub and rolling history.

One ingestion task writes here; every connected browser reads. A single
asyncio process is enough for this workload -- no Redis, no Kafka. The ring
buffer exists so a browser that connects mid-stream immediately sees the last
half hour instead of an empty chart.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from . import analytics, config

# A browser has no reason to name more routes than this; the cap keeps a
# malformed or hostile subscribe message from turning into a large set.
MAX_FILTER_ROUTES = 64


class Subscriber:
    """One connected browser: a bounded queue plus what it asked to receive.

    The whole fleet is a large message -- roughly 156 KB per tick on MBTA --
    and a browser showing one route, or one corner of the map, needs almost
    none of it. Rather than shipping everything and hiding the rest in the
    client, each subscriber declares a filter and the server sends only what
    it will actually draw.

    The filter lives here, not in the Hub, so the Hub still broadcasts one
    shared message object to everyone. Narrowing happens in the socket's own
    send loop, which means a client with an expensive filter pays for it in
    its own task and cannot slow the ingestion loop or any other viewer.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self.routes: set[str] | None = None  # None means every route
        self.bbox: tuple[float, float, float, float] | None = None
        # Counts of what was actually kept, so the saving shown in the UI is
        # an observation rather than a projection. Payload *size* is measured
        # by the client instead: a byte count cannot live inside the message
        # it is counting without being one serialisation out of date.
        self.sent_vehicles = 0
        self.total_vehicles = 0

    # -- filter ---------------------------------------------------------------

    def set_filter(self, routes: Any, bbox: Any) -> bool:
        """Apply a client-supplied filter, ignoring anything malformed.

        This is the one place a browser can influence the server, so nothing
        here trusts its input: a bad filter leaves the subscriber unfiltered
        rather than raising into the socket loop.
        """
        changed = False

        parsed_routes: set[str] | None = None
        if isinstance(routes, list) and routes:
            picked = {str(r) for r in routes[:MAX_FILTER_ROUTES] if isinstance(r, (str, int))}
            parsed_routes = picked or None
        if parsed_routes != self.routes:
            self.routes = parsed_routes
            changed = True

        parsed_bbox: tuple[float, float, float, float] | None = None
        if isinstance(bbox, list) and len(bbox) == 4:
            try:
                south, west, north, east = (float(x) for x in bbox)
            except (TypeError, ValueError):
                south = None  # type: ignore[assignment]
            else:
                if -90 <= south < north <= 90 and -180 <= west < east <= 180:
                    parsed_bbox = (south, west, north, east)
        if parsed_bbox != self.bbox:
            self.bbox = parsed_bbox
            changed = True

        return changed

    @property
    def filtered(self) -> bool:
        return self.routes is not None or self.bbox is not None

    def _wanted(self, v: dict) -> bool:
        if self.routes is not None and v["route_id"] not in self.routes:
            return False
        if self.bbox is not None:
            if v["lat"] is None or v["lon"] is None:
                return False
            south, west, north, east = self.bbox
            if not (south <= v["lat"] <= north and west <= v["lon"] <= east):
                return False
        return True

    def shape(self, message: dict) -> dict:
        """Narrow a broadcast message to what this subscriber asked for.

        Only the vehicle array is filtered. Metrics, events and the route
        table stay network-wide on purpose: a viewer watching one route still
        needs to see that the fleet as a whole just dropped 200 vehicles, and
        those fields are small.
        """
        if message.get("type") not in ("tick", "replay"):
            return message
        vehicles = message.get("vehicles") or []
        if not self.filtered:
            shaped = message
            kept = len(vehicles)
        else:
            kept_vehicles = [v for v in vehicles if self._wanted(v)]
            shaped = {**message, "vehicles": kept_vehicles}
            kept = len(kept_vehicles)
        self.sent_vehicles = kept
        self.total_vehicles = len(vehicles)
        return {
            **shaped,
            "filter": {
                "routes": sorted(self.routes) if self.routes else None,
                "bbox": list(self.bbox) if self.bbox else None,
                "vehicles_sent": kept,
                "vehicles_total": len(vehicles),
            },
        }


class Hub:
    """Broadcasts a message to every subscriber without blocking the producer.

    A subscriber that falls behind loses its oldest queued ticks rather than
    stalling ingestion -- for a live dashboard the newest tick is the only one
    that matters.
    """

    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()

    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def filtered_subscribers(self) -> int:
        return sum(1 for s in self._subscribers if s.filtered)

    def broadcast(self, message: dict) -> None:
        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()  # drop the stalest tick
                    sub.queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


class Store:
    """Latest snapshot plus the rolling history replayed on connect."""

    def __init__(self) -> None:
        self.vehicles: list[dict] = []
        self.history: deque[dict] = deque(maxlen=config.RING_SIZE)
        self.events: deque[dict] = deque(maxlen=config.EVENT_LOG_SIZE)
        # Alerts are also kept in `events`, but a busy peak fills that log with
        # routine bunching faster than a rare fleet anomaly can survive in it.
        # This second, smaller log holds only the severe ones so they stay on
        # screen for as long as they matter.
        self.alerts: deque[dict] = deque(maxlen=config.ALERT_LOG_SIZE)
        self.baselines = analytics.RouteBaselines()
        self.feed_status: dict[str, Any] = {
            "connected": False,
            "source": config.FEED_NAME,
            "url": config.FEED_URL,
            "simulated": config.SIMULATE,
            "last_ok": None,
            "last_error": None,
            "consecutive_failures": 0,
        }
        self._active_bunches: set[str] = set()
        self._stale_ids: set[str] = set()
        self._fleet_series: deque[float] = deque(maxlen=config.ZSCORE_WINDOW * 2)
        self._last_zscore: float | None = None

    # -- ingestion side ------------------------------------------------------

    def record_tick(self, vehicles: list[dict], feed_ts: float | None) -> dict:
        """Fold a new snapshot into state and return the message to broadcast."""
        now = time.time()
        # Learn from this tick before judging it. A route's normal spacing has
        # to include the moment being scored, or the very first tick after a
        # restart would be measured against nothing.
        self.baselines.observe(vehicles)
        summary = analytics.summarize(vehicles, now, self.baselines)

        z = analytics.rolling_zscore(self._fleet_series, summary["active"])
        self._fleet_series.append(summary["active"])
        summary["fleet_zscore"] = round(z, 2) if z is not None else None
        self._last_zscore = summary["fleet_zscore"]

        events = self._detect_events(summary, now, z)

        self.vehicles = vehicles
        point = {
            "ts": now,
            "feed_ts": feed_ts,
            "active": summary["active"],
            "bunched_pairs": summary["bunched_pairs"],
            "stale": summary["stale"],
            "avg_speed_mps": summary["avg_speed_mps"],
        }
        self.history.append(point)
        self.feed_status.update(
            {"connected": True, "last_ok": now, "consecutive_failures": 0}
        )

        return {
            "type": "tick",
            "ts": now,
            "feed_ts": feed_ts,
            "feed_age_sec": round(now - feed_ts, 1) if feed_ts else None,
            "vehicles": vehicles,
            "metrics": summary,
            "events": events,
            "alerts": list(self.alerts),
            "status": self.feed_status,
        }

    def _detect_events(self, summary: dict, now: float, z: float | None) -> list[dict]:
        """Emit an event when a condition starts or clears -- not every tick."""
        events: list[dict] = []

        current_bunches = {p["pair_id"]: p for p in summary["bunching"]}
        started, cleared = analytics.diff_keys(self._active_bunches, current_bunches)
        for pair_id in started:
            p = current_bunches[pair_id]
            events.append(
                {
                    "ts": now,
                    "kind": "bunching_started",
                    "severity": "warn",
                    "route_id": p["route_id"],
                    "message": (
                        f"Route {p['route_id']} dir {p['direction_id']}: vehicles "
                        f"{p['vehicles'][0]} and {p['vehicles'][1]} are "
                        f"{p['distance_m']:.0f} m apart"
                    ),
                    "detail": p,
                }
            )
        for pair_id in cleared:
            route_id = pair_id.split(":", 1)[0]
            events.append(
                {
                    "ts": now,
                    "kind": "bunching_cleared",
                    "severity": "ok",
                    "route_id": route_id,
                    "message": f"Route {route_id}: gap recovered",
                    "detail": {"pair_id": pair_id},
                }
            )
        self._active_bunches = set(current_bunches)

        current_stale = {s["id"]: s for s in summary["stale_vehicles"]}
        newly_stale, recovered = analytics.diff_keys(self._stale_ids, current_stale)
        for vid in newly_stale:
            s = current_stale[vid]
            events.append(
                {
                    "ts": now,
                    "kind": "vehicle_stale",
                    "severity": "warn",
                    "route_id": s["route_id"],
                    "message": (
                        f"Vehicle {vid} on route {s['route_id']} has not "
                        f"reported for {s['age_sec']:.0f}s"
                    ),
                    "detail": s,
                }
            )
        for vid in recovered:
            events.append(
                {
                    "ts": now,
                    "kind": "vehicle_recovered",
                    "severity": "ok",
                    "route_id": None,
                    "message": f"Vehicle {vid} is reporting again",
                    "detail": {"id": vid},
                }
            )
        self._stale_ids = set(current_stale)

        if z is not None and abs(z) >= config.ZSCORE_THRESHOLD:
            direction = "above" if z > 0 else "below"
            events.append(
                {
                    "ts": now,
                    "kind": "fleet_anomaly",
                    "severity": "alert",
                    "route_id": None,
                    "message": (
                        f"Active fleet of {summary['active']} is {abs(z):.1f}σ "
                        f"{direction} the recent norm"
                    ),
                    "detail": {"zscore": round(z, 2), "active": summary["active"]},
                }
            )

        for e in events:
            self.events.appendleft(e)
            if e["severity"] == "alert":
                self.alerts.appendleft(e)
        return events

    def record_failure(self, error: str) -> dict:
        now = time.time()
        self.feed_status["connected"] = False
        self.feed_status["last_error"] = {"ts": now, "error": error}
        self.feed_status["consecutive_failures"] += 1
        event = {
            "ts": now,
            "kind": "feed_error",
            "severity": "alert",
            "route_id": None,
            "message": f"Feed unreachable ({error})",
            "detail": {"failures": self.feed_status["consecutive_failures"]},
        }
        self.events.appendleft(event)
        self.alerts.appendleft(event)
        return {
            "type": "status",
            "ts": now,
            "status": self.feed_status,
            "events": [event],
            "alerts": list(self.alerts),
        }

    # -- reader side ---------------------------------------------------------

    def _replay_metrics(self) -> dict | None:
        """Recompute the last snapshot's metrics for a joining client.

        summarize() knows nothing about the z-score -- that needs the series
        history -- so carry the last computed value across, or the client
        receives a metrics object missing a field it expects.
        """
        if not self.vehicles:
            return None
        summary = analytics.summarize(self.vehicles, None, self.baselines)
        summary["fleet_zscore"] = self._last_zscore
        return summary

    def replay(self) -> dict:
        """Everything a newly connected browser needs to render immediately."""
        return {
            "type": "replay",
            "ts": time.time(),
            "vehicles": self.vehicles,
            "metrics": self._replay_metrics(),
            "history": list(self.history),
            "events": list(self.events),
            "alerts": list(self.alerts),
            "status": self.feed_status,
            "config": {
                "bunch_distance_m": config.BUNCH_DISTANCE_M,
                "stale_after_sec": config.STALE_AFTER_SEC,
                "poll_interval_sec": config.POLL_INTERVAL_SEC,
                "adaptive_bunching": config.BUNCH_ADAPTIVE,
                "bunch_ratio": config.BUNCH_RATIO,
                "bunch_distance_min_m": config.BUNCH_DISTANCE_MIN_M,
                "bunch_distance_max_m": config.BUNCH_DISTANCE_MAX_M,
                "map_center": [config.MAP_CENTER_LAT, config.MAP_CENTER_LON],
                "map_zoom": config.MAP_ZOOM,
            },
        }


hub = Hub()
store = Store()
