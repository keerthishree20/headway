"""Streaming analytics over a fleet snapshot.

Everything here is deliberately explainable -- distances, ages and a rolling
z-score. No model, no training data, no labels. A transit planner should be
able to read a flagged event and agree or disagree with it on the spot.
"""

from __future__ import annotations

import collections
import math
import statistics
import time
from collections import deque
from typing import Callable, Iterable, Sequence

from . import config

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _parked_together(a: dict, b: dict) -> bool:
    """True for two vehicles sitting at the same stop, both stationary.

    That is a terminal layover or a coupled light-rail consist -- normal
    operations, not a collapsed gap. Without this the Green Line reports
    permanent 0.0 m "bunching" at every terminus.
    """
    return (
        a["status"] == "stopped_at"
        and b["status"] == "stopped_at"
        and a["stop_id"] is not None
        and a["stop_id"] == b["stop_id"]
    )


def _routable(v: dict) -> bool:
    """Vehicle has the position and route needed to reason about spacing."""
    if v["lat"] is None or v["lon"] is None or not v["route_id"]:
        return False
    return not v["route_id"].lower().startswith(config.EXCLUDED_ROUTE_PREFIXES)


def group_by_route_direction(vehicles: Sequence[dict]) -> dict[tuple[str, int], list[dict]]:
    """Vehicles bucketed by the unit that actually shares a headway.

    Same route in opposite directions are different services -- two buses nose
    to nose across the street are not bunched.
    """
    groups: dict[tuple[str, int], list[dict]] = {}
    for v in vehicles:
        if _routable(v):
            groups.setdefault((v["route_id"], v["direction_id"]), []).append(v)
    return groups


def nearest_neighbour_gaps(members: Sequence[dict]) -> list[float]:
    """Each vehicle's distance to its closest peer in the same group.

    This is the raw material for "normal spacing on this route": it is what
    the gap between consecutive vehicles looks like when nothing is wrong.
    """
    if len(members) < 2:
        return []
    gaps = []
    for i, a in enumerate(members):
        gaps.append(
            min(
                haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                for j, b in enumerate(members)
                if j != i
            )
        )
    return gaps


def _running(members: Sequence[dict]) -> list[dict]:
    """Members of a group that are not laying over with a peer at the same stop."""
    parked_stops = collections.Counter(
        v["stop_id"] for v in members if v["status"] == "stopped_at" and v["stop_id"]
    )
    return [
        v
        for v in members
        if not (
            v["status"] == "stopped_at"
            and v["stop_id"]
            and parked_stops[v["stop_id"]] > 1
        )
    ]


class RouteBaselines:
    """Learns each route-direction's normal spacing from the live stream.

    A single global threshold cannot fit a network where one route runs every
    90 seconds through a dense corridor and another runs hourly through
    suburbs. Rather than requiring the agency's schedule, the detector watches
    what spacing that route actually keeps and calls a gap collapsed when it
    falls to a fraction of it.

    The percentile is deliberately high. Bunching lives at the *bottom* of a
    route's gap distribution, so learning from the median would let a route
    that is bunching right now drag its own threshold down until the detector
    went quiet -- the failure feeding back into its own detection. The upper
    quartile is barely moved by a few collapsed pairs.
    """

    def __init__(self) -> None:
        self._gaps: dict[tuple[str, int], deque[float]] = {}

    def observe(self, vehicles: Sequence[dict]) -> None:
        """Fold one tick's spacing into the rolling windows.

        Vehicles laying over together at a terminal are held out. They are
        already excluded from detection, and letting them into the baseline
        would teach the route that sitting nose to tail is its normal spacing
        -- on route 66 that alone understated normal spacing by 79%.
        """
        for key, members in group_by_route_direction(vehicles).items():
            gaps = nearest_neighbour_gaps(_running(members))
            if not gaps:
                continue
            window = self._gaps.get(key)
            if window is None:
                window = self._gaps[key] = deque(maxlen=config.BUNCH_BASELINE_WINDOW)
            window.extend(gaps)

    def normal_spacing_m(self, key: tuple[str, int]) -> float | None:
        """The route's learned normal spacing, or None while still learning."""
        window = self._gaps.get(key)
        if window is None or len(window) < config.BUNCH_BASELINE_MIN_SAMPLES:
            return None
        return percentile(window, config.BUNCH_BASELINE_PCT)

    def threshold_m(self, key: tuple[str, int]) -> float:
        """Distance at which this route-direction counts as bunched."""
        if not config.BUNCH_ADAPTIVE:
            return config.BUNCH_DISTANCE_M
        spacing = self.normal_spacing_m(key)
        if spacing is None:
            return config.BUNCH_DISTANCE_M
        return min(
            max(config.BUNCH_RATIO * spacing, config.BUNCH_DISTANCE_MIN_M),
            config.BUNCH_DISTANCE_MAX_M,
        )

    def explain(self, key: tuple[str, int]) -> dict:
        """Why this route flags where it does -- shown in the UI."""
        spacing = self.normal_spacing_m(key)
        return {
            "threshold_m": round(self.threshold_m(key), 1),
            "normal_spacing_m": round(spacing, 1) if spacing is not None else None,
            "samples": len(self._gaps.get(key, ())),
            "learned": spacing is not None,
        }

    def keys(self) -> list[tuple[str, int]]:
        return list(self._gaps)

    def learned_routes(self) -> int:
        return sum(1 for key in self._gaps if self.normal_spacing_m(key) is not None)


def percentile(values: Iterable[float], pct: float) -> float:
    """Linear-interpolated percentile.

    statistics.quantiles needs at least two points and only gives fixed cut
    points; this takes an arbitrary percentile and works on a single sample.
    """
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sequence")
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * (pct / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[int(pos)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def find_bunching(
    vehicles: Sequence[dict],
    threshold_for: Callable[[tuple[str, int]], float] | None = None,
) -> list[dict]:
    """Pairs of vehicles serving the same route and direction that have closed
    the gap between them.

    Bus bunching is the classic failure mode of frequency-based service: one
    vehicle runs late, picks up the passengers meant for the next one, falls
    further behind, and the follower catches it. Riders then wait a long gap
    and two vehicles arrive together. The live positions show it happening
    before any schedule-adherence report would.

    `threshold_for` supplies the distance at which each route-direction counts
    as bunched -- normally `RouteBaselines.threshold_m`, which has learned that
    route's own spacing from the stream. Without it every route falls back to
    the single global distance.
    """
    pairs: list[dict] = []
    for key, members in group_by_route_direction(vehicles).items():
        if len(members) < 2:
            continue
        route_id, direction_id = key
        limit = (
            threshold_for(key) if threshold_for is not None else config.BUNCH_DISTANCE_M
        )
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if _parked_together(a, b):
                    continue
                d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                if d <= limit:
                    lo, hi = sorted((a["id"], b["id"]))
                    pairs.append(
                        {
                            "pair_id": f"{route_id}:{direction_id}:{lo}:{hi}",
                            "route_id": route_id,
                            "direction_id": direction_id,
                            "vehicles": [lo, hi],
                            "distance_m": round(d, 1),
                            "threshold_m": round(limit, 1),
                            "lat": round((a["lat"] + b["lat"]) / 2, 6),
                            "lon": round((a["lon"] + b["lon"]) / 2, 6),
                        }
                    )
    pairs.sort(key=lambda p: p["distance_m"])
    return pairs


def find_stale(vehicles: Sequence[dict], now: float) -> list[dict]:
    """Vehicles the feed still advertises but has not heard from recently.

    These are the "ghost buses" riders see on an app that never arrive. The
    same staging-lot exclusion as bunching applies: a replacement shuttle
    parked under the catch-all route id is not a ghost, it is a spare sitting
    in a yard with its transponder idle, and nobody is waiting for it.
    """
    out = []
    for v in vehicles:
        if not v["ts"]:
            continue
        if v["route_id"] and v["route_id"].lower().startswith(
            config.EXCLUDED_ROUTE_PREFIXES
        ):
            continue
        age = now - v["ts"]
        if age > config.STALE_AFTER_SEC:
            out.append(
                {
                    "id": v["id"],
                    "route_id": v["route_id"],
                    "age_sec": round(age, 1),
                    "lat": v["lat"],
                    "lon": v["lon"],
                }
            )
    out.sort(key=lambda v: -v["age_sec"])
    return out


def rolling_zscore(series: Sequence[float], value: float) -> float | None:
    """z-score of `value` against the trailing window in `series`.

    Returns None until there is enough history, or when the window is flat
    (stdev 0), which would otherwise divide by zero.
    """
    window = list(series)[-config.ZSCORE_WINDOW :]
    if len(window) < 8:
        return None
    mu = statistics.fmean(window)
    try:
        sigma = statistics.stdev(window)
    except statistics.StatisticsError:
        return None
    if sigma < 1e-9:
        return None
    return (value - mu) / sigma


def summarize(
    vehicles: Sequence[dict],
    now: float | None = None,
    baselines: "RouteBaselines | None" = None,
) -> dict:
    """Per-tick metrics: fleet health, bunching and the worst-affected routes."""
    now = now or time.time()
    bunched = find_bunching(
        vehicles, baselines.threshold_m if baselines is not None else None
    )
    stale = find_stale(vehicles, now)
    stale_ids = {s["id"] for s in stale}

    speeds = [v["speed"] for v in vehicles if v["speed"] is not None]
    moving = [s for s in speeds if s >= config.MOVING_SPEED_MIN_MPS]

    per_route: dict[str, dict] = {}
    for v in vehicles:
        r = per_route.setdefault(
            v["route_id"] or "?", {"route_id": v["route_id"] or "?", "vehicles": 0, "bunched": 0, "stale": 0}
        )
        r["vehicles"] += 1
        if v["id"] in stale_ids:
            r["stale"] += 1
    bunched_vehicle_ids: set[str] = set()
    for p in bunched:
        bunched_vehicle_ids.update(p["vehicles"])
        per_route.setdefault(
            p["route_id"], {"route_id": p["route_id"], "vehicles": 0, "bunched": 0, "stale": 0}
        )
    for r_id, r in per_route.items():
        r["bunched"] = sum(1 for p in bunched if p["route_id"] == r_id)

    worst = sorted(
        (r for r in per_route.values() if r["bunched"] or r["stale"]),
        key=lambda r: (-r["bunched"], -r["stale"]),
    )[:8]

    # A flagged route should be able to say why it flags where it does. Both
    # directions share a row here, so report the tighter of the two.
    if baselines is not None:
        for r in worst:
            options = [
                baselines.explain((r["route_id"], d))
                for d in (0, 1)
                if baselines.normal_spacing_m((r["route_id"], d)) is not None
            ]
            if options:
                r.update(min(options, key=lambda e: e["threshold_m"]))

    return {
        "active": len(vehicles),
        "routes": len(per_route),
        "bunched_pairs": len(bunched),
        "bunched_vehicles": len(bunched_vehicle_ids),
        "stale": len(stale),
        "moving": len(moving),
        "avg_speed_mps": round(statistics.fmean(moving), 2) if moving else 0.0,
        "bunching": bunched[:60],
        "stale_vehicles": stale[:40],
        "worst_routes": worst,
        "learned_routes": baselines.learned_routes() if baselines is not None else 0,
    }


def diff_keys(previous: Iterable[str], current: Iterable[str]) -> tuple[set[str], set[str]]:
    """Keys that appeared and keys that cleared between two ticks."""
    prev, cur = set(previous), set(current)
    return cur - prev, prev - cur
