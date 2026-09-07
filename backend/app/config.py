"""Runtime configuration.

Every value can be overridden with an environment variable of the same name,
so the app can point at any GTFS-Realtime feed without a code change.
"""

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# --- Feed -------------------------------------------------------------------
# Default: MBTA (Boston) publishes GTFS-Realtime with no API key, refreshed
# roughly every 5 seconds. Swap FEED_URL for any agency's VehiclePositions.pb
# (Delhi OTD, BMTC, TriMet, ...) and set FEED_API_KEY_HEADER/FEED_API_KEY if
# that agency requires one.
FEED_URL = os.environ.get(
    "FEED_URL", "https://cdn.mbta.com/realtime/VehiclePositions.pb"
)
FEED_NAME = os.environ.get("FEED_NAME", "MBTA (Boston, MA)")
FEED_API_KEY_HEADER = os.environ.get("FEED_API_KEY_HEADER", "")
FEED_API_KEY = os.environ.get("FEED_API_KEY", "")

# Map centre used by the frontend when it has no vehicles yet.
MAP_CENTER_LAT = _float("MAP_CENTER_LAT", 42.3555)
MAP_CENTER_LON = _float("MAP_CENTER_LON", -71.0605)
MAP_ZOOM = _int("MAP_ZOOM", 12)

# --- Ingestion --------------------------------------------------------------
POLL_INTERVAL_SEC = _float("POLL_INTERVAL_SEC", 8.0)
HTTP_TIMEOUT_SEC = _float("HTTP_TIMEOUT_SEC", 15.0)
BACKOFF_MAX_SEC = _float("BACKOFF_MAX_SEC", 60.0)

# Run against a deterministic synthetic fleet instead of the network.
# Offline development only -- never the default.
SIMULATE = os.environ.get("SIMULATE", "0") not in ("0", "", "false", "False")

# --- Analytics --------------------------------------------------------------
# Two vehicles on the same route and direction closer than this are bunched:
# the gap riders experience has collapsed to ~zero.
BUNCH_DISTANCE_M = _float("BUNCH_DISTANCE_M", 220.0)
# A position older than this is a "ghost" -- the feed still lists the vehicle
# but has not heard from it recently.
STALE_AFTER_SEC = _float("STALE_AFTER_SEC", 150.0)
# Vehicles reporting below this are treated as stopped for speed stats (m/s).
MOVING_SPEED_MIN_MPS = _float("MOVING_SPEED_MIN_MPS", 0.5)

# --- Adaptive bunching ------------------------------------------------------
# A single global distance is wrong on a mixed network. Measured live on MBTA,
# the median nearest-neighbour gap between two vehicles on the same route and
# direction ranges from ~86 m on route 1 to ~3.3 km on route 110 -- a 38x
# spread. 220 m is a collapsed gap on the first and an unreachable one on the
# second.
#
# So each route learns its own normal spacing from the stream: keep a rolling
# window of observed nearest-neighbour gaps and take a high percentile of it.
# A high percentile, not the median, because bunching itself lives at the
# bottom of the distribution -- a route that is bunching right now would
# otherwise teach the detector to stop noticing.
BUNCH_ADAPTIVE = os.environ.get("BUNCH_ADAPTIVE", "1") not in ("0", "", "false", "False")
# Fraction of a route's normal spacing at which the gap counts as collapsed.
BUNCH_RATIO = _float("BUNCH_RATIO", 0.30)
# Percentile of the observed-gap window treated as that route's normal spacing.
BUNCH_BASELINE_PCT = _float("BUNCH_BASELINE_PCT", 75.0)
# Gaps kept per route-direction, and how many are needed before the learned
# threshold replaces the global one.
BUNCH_BASELINE_WINDOW = _int("BUNCH_BASELINE_WINDOW", 600)
BUNCH_BASELINE_MIN_SAMPLES = _int("BUNCH_BASELINE_MIN_SAMPLES", 90)
# Clamps. The floor stops a permanently bunched route from learning its way
# down to a threshold nothing can trip.
BUNCH_DISTANCE_MIN_M = _float("BUNCH_DISTANCE_MIN_M", 80.0)
# The ceiling is not a matter of taste -- it is where straight-line distance
# stops being a usable proxy for a collapsed gap, and it was measured. Pairs
# the fixed 220 m rule already flagged trace each other's stops (65% share at
# least one stop, median Jaccard 0.33): a real follower works the same stretch
# of the same corridor. Pairs added by raising the threshold match that
# profile up to ~400 m and stop matching it beyond, falling to the same stop
# overlap as vehicle pairs that were never flagged at all -- vehicles passing
# close on opposite legs of a folded route, which is exactly what a
# great-circle distance cannot tell apart from a follower. See the README.
BUNCH_DISTANCE_MAX_M = _float("BUNCH_DISTANCE_MAX_M", 400.0)

# Route ids starting with any of these are excluded from bunching. Agencies
# park replacement shuttles together in a staging lot under one catch-all
# route id; on MBTA ("Shuttle-Generic") that alone produced more than half of
# all raw detections. Comma-separated, case-insensitive.
EXCLUDED_ROUTE_PREFIXES = tuple(
    p.strip().lower()
    for p in os.environ.get("EXCLUDED_ROUTE_PREFIXES", "Shuttle-").split(",")
    if p.strip()
)

# --- History ----------------------------------------------------------------
# Ticks kept in memory and replayed to a browser the moment it connects, so a
# fresh page load is never blank. 240 ticks * 8s ~= 32 minutes of history.
RING_SIZE = _int("RING_SIZE", 240)
EVENT_LOG_SIZE = _int("EVENT_LOG_SIZE", 120)
# Alerts get their own log. A busy peak produces bunching events faster than
# the shared log can hold them, and a fleet anomaly -- the rarest and most
# serious thing here -- would be evicted within a minute of being raised.
ALERT_LOG_SIZE = _int("ALERT_LOG_SIZE", 25)

# Window used for the rolling z-score on fleet size.
ZSCORE_WINDOW = _int("ZSCORE_WINDOW", 30)
ZSCORE_THRESHOLD = _float("ZSCORE_THRESHOLD", 2.5)
