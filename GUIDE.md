# Headway — Complete Project Guide

## Table of Contents
1. [What is Headway?](#what-is-headway)
2. [Quick Start](#quick-start)
3. [Core Concepts](#core-concepts)
4. [Architecture](#architecture)
5. [Backend Deep Dive](#backend-deep-dive)
6. [The WebSocket Protocol](#the-websocket-protocol)
7. [Frontend Deep Dive](#frontend-deep-dive)
8. [Configuration](#configuration)
9. [Using Another City's Feed](#using-another-citys-feed)
10. [Testing Strategy](#testing-strategy)
11. [Limitations](#limitations)
12. [Troubleshooting](#troubleshooting)

---

## What is Headway?

Headway is a live dashboard that watches real bus and train positions and flags reliability problems
as they happen:

- **Bunching.** Two vehicles on the same route and direction that have closed the gap between them.
- **Ghost vehicles.** Vehicles the feed still advertises but whose position has not updated for
  150 seconds or more.
- **Fleet anomalies.** A sudden drop or spike in the number of active vehicles.

It reads the public GTFS-Realtime feed of any transit agency. The default is MBTA in Boston, which
needs no API key, so it runs immediately. Everything is measured from live positions, not the
timetable.

Every detector is a rule a transit planner can read and argue with. There is deliberately no machine
learning model.

---

## Quick Start

Needs Python 3.10 or newer and Node 18 or newer.

The easy way, from the project root:

```bash
./run.sh
```

It picks a Python of at least 3.10, creates the backend venv, installs both halves on first run, and
starts:
- the API on http://127.0.0.1:8100/api/health
- the dashboard on http://127.0.0.1:3100

Or run the halves separately:

```bash
cd backend
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.main:app --port 8100
```

```bash
cd frontend
npm install
npm run dev          # http://localhost:3100
```

Offline, with a synthetic fleet:

```bash
SIMULATE=1 ./.venv/bin/uvicorn app.main:app --port 8100
```

The header then says `SIMULATED DATA`.

---

## Core Concepts

### GTFS-Realtime
A standard protobuf format transit agencies publish. Headway reads the `VehiclePositions` feed: for
each vehicle, its route, direction, position, current stop and timestamp.

### Bunching
Two vehicles on the same route and direction closer than a threshold. The distance is great-circle,
straight line, computed with `haversine_m()`.

### Per-route learned threshold
Normal spacing differs hugely between routes. On MBTA the median gap ranged from 86 m to 3,317 m. So
each route and direction learns its own:
1. Record the nearest-neighbour gaps observed on that route, keeping the last 600.
2. Take the 75th percentile as "normal spacing". A high percentile, so a route that is bunching right
   now cannot drag its own threshold down.
3. A gap under 30% of normal counts as collapsed.
4. Clamp the result between 80 m and 400 m.
5. Until a route has 90 observed gaps, use the global 220 m.

The 400 m ceiling was measured, not chosen. Past it, flagged pairs stop sharing stops and look like
vehicles on opposite legs of a folded route. The README has the evidence.

### False-positive filters
- Routes starting with `Shuttle-` are excluded. MBTA parks spare shuttles together under one route.
- Two vehicles `stopped_at` the same stop are laying over or coupled, not bunched.

### Start and clear events
Conditions are reported when they begin and when they end, not every tick.

---

## Architecture

```
GTFS-Realtime feed (protobuf over HTTP)
          │  one asyncio task, polls every 8 s, backs off on failure
          ▼
     ingest.py ──► analytics.py     bunching, ghosts, rolling z-score
          │              │          RouteBaselines: each route's own spacing
          ▼              ▼
              state.py               Store: snapshot + 240-tick ring buffer
                 │                   Hub: fan-out to every browser
                 │                   Subscriber: one queue + that client's filter
                 ▼
    FastAPI ── WS /ws ──► Next.js dashboard (map, charts, event stream)
            └─ REST /api/health, /snapshot, /events, /history, /baselines
```

One process, one ingestion task, everything in memory. No database and no message broker. At one
poll every 8 seconds over a few hundred vehicles, they would be infrastructure for its own sake.

A slow browser drops its own oldest tick rather than stalling ingestion or other viewers.

---

## Backend Deep Dive

### `app/config.py`
Reads every setting from environment variables with defaults.

### `app/ingest.py`
- `parse_feed(payload)` decodes the protobuf into vehicle dictionaries.
- `run_ingestion(stop)` polls `FEED_URL`, sends the optional API key header, analyses each tick, and
  publishes it. Failures back off exponentially.

### `app/analytics.py`
| function | purpose |
|---|---|
| `haversine_m()` | great-circle distance in metres |
| `group_by_route_direction()` | groups vehicles by route and direction, skipping excluded routes |
| `nearest_neighbour_gaps()` | the gaps fed into each route's baseline |
| `RouteBaselines` | the rolling window, percentile and clamped threshold per route |
| `find_bunching()` | pairs under their route's threshold, minus the layover filter |
| `find_stale()` | ghost vehicles past `STALE_AFTER_SEC` |
| `rolling_zscore()` | fleet-size anomaly over a 30-tick window |
| `summarize()` | builds one tick's message: vehicles, pairs, ghosts, metrics |
| `diff_keys()` | which conditions started and which cleared since the last tick |

### `app/state.py`
- `Store` keeps the latest snapshot, a ring buffer of 240 ticks, the event log and a separate held
  alert log. `replay()` gives a new browser the last ~30 minutes at once.
- `Hub` broadcasts one shared message to all subscribers.
- `Subscriber` holds one browser's queue and its route and viewport filter. Filtering happens in that
  browser's own send loop, so an expensive filter slows only that browser.

### `app/simulate.py`
A deterministic synthetic fleet with one route seeded to bunch and vehicles that freeze at random,
used when `SIMULATE=1`.

### `app/main.py`
FastAPI app with the lifespan that starts ingestion, the REST routes, and the WebSocket.

| route | returns |
|---|---|
| `GET /api/health` | feed connection status, subscriber counts, vehicles, ticks buffered, learned routes |
| `GET /api/snapshot` | the latest tick |
| `GET /api/events?limit=50` | recent start and clear events |
| `GET /api/history` | the ring buffer for charts |
| `GET /api/baselines` | every route's learned threshold and the sample count behind it |
| `WS /ws` | the live stream |

---

## The WebSocket Protocol

1. The browser connects to `/ws`.
2. The server immediately sends a replay of recent history.
3. The server then pushes every new tick.
4. The browser may send a filter at any time:

```json
{"type": "subscribe", "routes": ["1", "22"], "bbox": [south, west, north, east]}
```

The server then sends only vehicles on those routes inside that box. Measured live, this cut a tick
from 113.7 KB to 14.6 KB. Metrics stay network-wide, so a viewer watching one route still sees the
fleet drop.

Malformed or binary frames are ignored rather than closing the connection.

---

## Frontend Deep Dive

Next.js 15 with React, Tailwind, and Leaflet through react-leaflet.

| file | purpose |
|---|---|
| `app/page.tsx` | the dashboard layout |
| `lib/useLiveFeed.ts` | the WebSocket connection, reconnects, and sending filters |
| `components/FleetMap.tsx` | the Leaflet map, vehicles drawn to canvas for ~660 live markers |
| `components/TimelineChart.tsx` | fleet size and bunched pairs, as two separate charts |
| `components/EventFeed.tsx` | the start and clear event stream, and held alerts |
| `components/RouteTable.tsx` | per-route counts and learned thresholds. Clicking a route filters everything |
| `components/StatTile.tsx` | headline numbers |
| `lib/useThemeTokens.ts` | reads CSS colour tokens so the canvas renderer can use them |
| `lib/useIsDark.ts` | light or dark theme, each with its own basemap |

Design choices worth keeping:
- Two charts, never one chart with two axes.
- Vehicle state is shown by colour and by size and fill, so it survives colour blindness and print.
- Every event has an icon and a text label.

---

## Configuration

All settings are environment variables on the backend. The most useful:

| variable | default | meaning |
|---|---|---|
| `FEED_URL` | MBTA VehiclePositions | the GTFS-RT endpoint |
| `FEED_NAME` | `MBTA (Boston, MA)` | shown in the header |
| `FEED_API_KEY_HEADER`, `FEED_API_KEY` | none | auth header if the agency needs one |
| `POLL_INTERVAL_SEC` | `8` | poll cadence |
| `BUNCH_DISTANCE_M` | `220` | threshold before a route has learned its own |
| `BUNCH_ADAPTIVE` | `1` | learn per-route thresholds |
| `BUNCH_DISTANCE_MIN_M`, `BUNCH_DISTANCE_MAX_M` | `80`, `400` | clamps on the learned threshold |
| `STALE_AFTER_SEC` | `150` | ghost threshold |
| `EXCLUDED_ROUTE_PREFIXES` | `Shuttle-` | routes skipped by bunching and ghost detection |
| `ZSCORE_WINDOW`, `ZSCORE_THRESHOLD` | `30`, `2.5` | fleet anomaly sensitivity |
| `MAP_CENTER_LAT`, `MAP_CENTER_LON`, `MAP_ZOOM` | Boston | initial map view |
| `SIMULATE` | `0` | synthetic fleet |

The README lists every variable. The frontend has one, `NEXT_PUBLIC_WS_URL`, defaulting to
`ws://127.0.0.1:8100/ws`.

---

## Using Another City's Feed

Any `VehiclePositions.pb` endpoint works. For Delhi:

```bash
FEED_URL=https://otd.delhi.gov.in/api/realtime/VehiclePositions.pb \
FEED_NAME="DTC (Delhi)" \
FEED_API_KEY_HEADER=key \
FEED_API_KEY=$OTD_KEY \
MAP_CENTER_LAT=28.6139 MAP_CENTER_LON=77.2090 \
./.venv/bin/uvicorn app.main:app --port 8100
```

After switching, check `EXCLUDED_ROUTE_PREFIXES`. Other agencies name their shuttle or depot routes
differently, and those produce false bunching.

---

## Testing Strategy

```bash
cd backend
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -v
```

| file | what it covers |
|---|---|
| `tests/test_analytics.py` | the detectors, the shuttle and layover filters, learned baselines, z-scores |
| `tests/test_state.py` | the store, ring buffer, held alerts, subscriber filtering |
| `tests/test_ws.py` | the WebSocket protocol through a real socket, including malformed and binary frames |

---

## Limitations

The README discusses each one. The ones to know before relying on it:

- **Straight-line distance, not distance along the route.** Two vehicles on opposite sides of a
  hairpin can look close. Using the agency's static `shapes.txt` would fix it.
- **Spatial gap, not minutes apart.**
- **Memory only.** A restart loses history, and it runs as one process.
- **Most learned thresholds hit the 400 m ceiling** because of the straight-line limitation.
- **No time-of-day awareness** in the learned baselines.
- **Not deployed.**

---

## Troubleshooting

### The dashboard stays empty
Check http://127.0.0.1:8100/api/health. If the feed is failing, the API backs off and retries. Try
`SIMULATE=1` to confirm the frontend works.

### The dashboard connects to nothing in production
Set `NEXT_PUBLIC_WS_URL` to the deployed API's `wss://` address before building.

### Too many bunching alerts after switching cities
The agency parks spare vehicles under a catch-all route. Add its prefix to `EXCLUDED_ROUTE_PREFIXES`.

### `run.sh` says it needs Python 3.10
The system `python3` here is 3.6. Install 3.12, or run with `PYTHON=/usr/bin/python3.12 ./run.sh`.

### A route's threshold looks wrong
Check `GET /api/baselines`. It shows each learned threshold and how many gaps it was learned from.
