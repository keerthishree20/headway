# Headway — Complete Project Guide

A complete guide from zero to a live transit reliability dashboard. Covers every feature, every
design decision and the reason behind it, with the real code. It is self-contained: you can paste it
into any AI chat and ask questions about the project without sharing the repository.

**Repository:** https://github.com/keerthishree20/headway

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack & Why](#2-tech-stack--why)
3. [Project Setup from Scratch](#3-project-setup-from-scratch)
4. [Core Ideas in Plain Words](#4-core-ideas-in-plain-words)
5. [Project Structure](#5-project-structure)
6. [Ingesting GTFS-Realtime](#6-ingesting-gtfs-realtime)
7. [Bunching Detection](#7-bunching-detection)
8. [Per-Route Learned Thresholds](#8-per-route-learned-thresholds)
9. [Why the Threshold Is Capped at 400 m](#9-why-the-threshold-is-capped-at-400-m)
10. [False-Positive Filters](#10-false-positive-filters)
11. [Ghost Vehicles](#11-ghost-vehicles)
12. [Fleet Anomalies](#12-fleet-anomalies)
13. [Start and Clear Events](#13-start-and-clear-events)
14. [State, History and Held Alerts](#14-state-history-and-held-alerts)
15. [Fan-Out to Browsers](#15-fan-out-to-browsers)
16. [The WebSocket Protocol & Server-Side Filtering](#16-the-websocket-protocol--server-side-filtering)
17. [REST API](#17-rest-api)
18. [The Dashboard](#18-the-dashboard)
19. [Design Choices in the Charts and Map](#19-design-choices-in-the-charts-and-map)
20. [Simulation Mode](#20-simulation-mode)
21. [Configuration](#21-configuration)
22. [Using Another City's Feed](#22-using-another-citys-feed)
23. [Testing](#23-testing)
24. [Limitations](#24-limitations)
25. [Troubleshooting](#25-troubleshooting)
26. [Complete Feature Summary](#26-complete-feature-summary)

---

## 1. Project Overview

Headway is a **real-time transit reliability monitor**. It watches live bus and train positions and
flags problems as they happen:

- **Bunching**: two vehicles on the same route and direction that have closed the gap between them.
- **Ghost vehicles**: vehicles the feed still shows but whose position has not updated for 150 s.
- **Fleet anomalies**: a sudden drop or spike in the number of active vehicles.

### Why bunching?
Buses do not usually fail by breaking down. They fail by **bunching**: one bus runs a little late,
picks up the passengers meant for the next one, falls further behind, and the next bus catches it.
Riders wait through a long gap, then two buses arrive together. The timetable still says "every 8
minutes" and the monthly on-time report looks fine, because the failure lives between the scheduled
stops. Headway watches the positions themselves.

It reads any agency's public **GTFS-Realtime** feed. The default is MBTA (Boston), which needs no key.

**Status:** complete. 81 tests pass. Public on GitHub. Not deployed.

---

## 2. Tech Stack & Why

| Technology | Role | Why We Chose It |
|---|---|---|
| **FastAPI** | Backend | async WebSocket and REST in one app |
| **asyncio** | Ingestion | one task polls the feed; no threads needed |
| **httpx** | Feed fetch | async HTTP with timeouts |
| **gtfs-realtime-bindings** | Protobuf | decodes the standard vehicle positions format |
| **WebSocket** | Live updates | the server pushes each tick; no polling |
| **Next.js 15 + React** | Dashboard | map, charts, tables |
| **Leaflet (react-leaflet)** | Map | free, keyless basemaps; canvas mode for 660 markers |
| **Tailwind** | Styling | light and dark themes |
| **In-memory state** | Storage | at one poll every 8 s, a database or broker would be infrastructure for its own sake |

### Why rules, not machine learning?
Every detector is a rule a transit planner can read and argue with: a distance, an age, a z-score. No
labels, no training, no model drift. A flagged event names the two vehicles and the distance between
them, so anyone can check it in seconds. On a civic dashboard, that is worth more than a few points
of accuracy.

---

## 3. Project Setup from Scratch

Needs Python 3.10+ and Node 18+.

### The easy way
```bash
git clone https://github.com/keerthishree20/headway.git
cd headway
./run.sh
```
`run.sh` picks a Python of at least 3.10 (the system `python3` here is 3.6), creates the backend venv,
installs both halves on first run, and starts:
- API: http://127.0.0.1:8100/api/health
- Dashboard: http://127.0.0.1:3100

### Separately
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

### Offline
```bash
SIMULATE=1 ./.venv/bin/uvicorn app.main:app --port 8100
```

---

## 4. Core Ideas in Plain Words

| Idea | Meaning |
|---|---|
| **GTFS-Realtime** | the standard protobuf format agencies publish live vehicle data in |
| **Vehicle position** | one vehicle's route, direction, latitude, longitude, current stop, status and timestamp |
| **Tick** | one poll of the feed, every 8 seconds |
| **Route-direction** | a route in one direction, e.g. route 1 outbound |
| **Great-circle distance** | straight-line distance on the earth's surface (haversine) |
| **z-score** | how many standard deviations a value is from its recent average |

---

## 5. Project Structure

```
backend/app/
  config.py      every setting, read from environment variables
  ingest.py      parse_feed(), run_ingestion(): poll, decode, analyse, publish
  analytics.py   haversine, grouping, bunching, baselines, ghosts, z-score, summarize, diff_keys
  state.py       Subscriber (per-browser filter), Hub (fan-out), Store (snapshot, history, events)
  simulate.py    deterministic synthetic fleet
  main.py        FastAPI app: REST routes and the WebSocket
backend/tests/
  test_analytics.py  test_state.py  test_ws.py
frontend/
  app/page.tsx               the dashboard
  components/FleetMap.tsx    Leaflet map, canvas markers
  components/TimelineChart.tsx
  components/EventFeed.tsx
  components/RouteTable.tsx
  components/StatTile.tsx
  lib/useLiveFeed.ts         WebSocket connection and filters
  lib/useThemeTokens.ts      CSS colours for the canvas
  lib/useIsDark.ts  lib/types.ts
docs/  dashboard-dark.png  dashboard-light.png
run.sh
```

---

## 6. Ingesting GTFS-Realtime

`backend/app/ingest.py`:
- `parse_feed(payload)` decodes the protobuf into plain vehicle dictionaries: `id`, `route_id`,
  `direction_id`, `lat`, `lon`, `stop_id`, `status`, `ts`.
- `run_ingestion(stop)` is one asyncio task that polls `FEED_URL` every `POLL_INTERVAL_SEC` (8 s),
  sends the optional API key header, analyses the tick, and publishes it. Failures back off
  exponentially instead of hammering the agency.

---

## 7. Bunching Detection

```python
def find_bunching(vehicles, threshold_for=None):
    pairs = []
    for key, members in group_by_route_direction(vehicles).items():
        if len(members) < 2:
            continue
        route_id, direction_id = key
        limit = threshold_for(key) if threshold_for else config.BUNCH_DISTANCE_M
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if _parked_together(a, b):
                    continue                              # layover, not bunching
                d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                if d <= limit:
                    pairs.append({"pair_id": f"{route_id}:{direction_id}:{lo}:{hi}",
                                  "vehicles": [lo, hi], "distance_m": round(d, 1),
                                  "threshold_m": round(limit, 1), ...})
    pairs.sort(key=lambda p: p["distance_m"])
    return pairs
```

Vehicles are grouped by route and direction, and every pair within that group's threshold is a
bunched pair. Each pair names both vehicles, the distance, and the threshold used.

---

## 8. Per-Route Learned Thresholds

One global distance cannot fit a real network. On the live MBTA feed, the median gap between a vehicle
and its nearest peer ranged from **86 m on route 1 to 3,317 m on route 110**, a 38× spread. A fixed
220 m is a collapsed gap on the first and an impossible one on the second.

So each route-direction **learns its own normal spacing** from the stream:

```python
def threshold_m(self, key):
    if not config.BUNCH_ADAPTIVE:
        return config.BUNCH_DISTANCE_M
    spacing = self.normal_spacing_m(key)       # 75th percentile of recent gaps
    if spacing is None:
        return config.BUNCH_DISTANCE_M         # still learning: global 220 m
    return min(max(config.BUNCH_RATIO * spacing, config.BUNCH_DISTANCE_MIN_M),
               config.BUNCH_DISTANCE_MAX_M)     # 30% of normal, clamped to 80–400 m
```

1. Keep the last 600 nearest-neighbour gaps per route-direction.
2. **Normal spacing** is their **75th percentile**.
3. A gap under **30%** of normal counts as collapsed.
4. Clamp between 80 m and 400 m.
5. Until a route has 90 observed gaps, use the global 220 m.

### Why the 75th percentile and not the median?
Bunching lives at the **bottom** of a route's gap distribution. Learning from the median would let a
route that is bunching right now drag its own threshold down until the detector went quiet. A few
collapsed pairs barely move the upper quartile.

### Layovers are kept out of learning too
Vehicles laying over together at a terminal would teach the route that sitting nose to tail is normal.
On route 66, that alone understated normal spacing by 79%.

`GET /api/baselines` shows every route's learned number and the sample count behind it, and the
dashboard prints the threshold next to each count. An adaptive threshold nobody can inspect is worse
than a fixed one.

---

## 9. Why the Threshold Is Capped at 400 m

The cap was **measured**, not chosen by taste.

Raising a threshold always finds more pairs, so "it detects more" proves nothing. Scoring pairs by how
long they persist turned out to be circular too (a pair survives as long as it stays under the
threshold).

A threshold-independent test: vehicles report the stop they are at, so a **genuine follower traces the
leader's stops**. Two vehicles passing close on opposite legs of a folded route serve completely
different stops. Scoring 30 recorded ticks:

| Pairs | Share sharing ≥ 1 stop | Median stop overlap |
|---|---|---|
| flagged by the fixed 220 m rule (control) | 65% | 0.33 |
| added by the adaptive rule, ceiling 400 m | 67% | 0.20 |
| added by the adaptive rule, ceiling 600 m+ | 33–50% | 0.00 |
| same route and direction, never flagged (negative control) | — | 0.00 |

Up to about 400 m, the added pairs look like trusted detections. Beyond it, they look like pairs that
were never bunched. Net effect on the same window: **5.8 → 8.9 bunched pairs per tick**.

---

## 10. False-Positive Filters

Running the naive detector on live MBTA data gave **56 bunched pairs out of ~630 vehicles**, far too
many to be real. Two causes:

1. **Staging lots.** Agencies park spare shuttles together under one catch-all route (`Shuttle-Generic`
   on MBTA). That alone was 30 of the 56. Routes starting with `EXCLUDED_ROUTE_PREFIXES` (`Shuttle-`)
   are excluded from bunching and ghost detection.
2. **Terminal layovers and coupled trains.**

```python
def _parked_together(a, b):
    return (a["status"] == "stopped_at" and b["status"] == "stopped_at"
            and a["stop_id"] is not None and a["stop_id"] == b["stop_id"])
```
This is what produced permanent "0.0 m bunching" on the Green Line.

After both filters: about **20 pairs**, concentrated on routes 1, 22, 23, 66, 111 and 116, Boston's
real high-frequency bus corridors, exactly the routes that bunch.

---

## 11. Ghost Vehicles

```python
def find_stale(vehicles, now):
    for v in vehicles:
        if not v["ts"] or excluded route:
            continue
        age = now - v["ts"]
        if age > config.STALE_AFTER_SEC:          # 150 s
            out.append({"id": ..., "route_id": ..., "age_sec": round(age, 1), ...})
    out.sort(key=lambda v: -v["age_sec"])
```

These are the buses that show on a rider's app and never arrive. A spare parked in a yard with an idle
transponder is not a ghost, so the same staging-lot exclusion applies.

---

## 12. Fleet Anomalies

```python
def rolling_zscore(series, value):
    window = list(series)[-config.ZSCORE_WINDOW:]      # last 30 ticks
    if len(window) < 8:
        return None
    mu = statistics.fmean(window)
    sigma = statistics.stdev(window)                   # None if flat, to avoid dividing by zero
    ...
```

When the active fleet size is more than **2.5 standard deviations** from its recent average, an alert
fires. It catches a depot dropping out or a feed partially failing.

---

## 13. Start and Clear Events

Conditions are reported **when they start and when they clear**, not every tick:

```python
current_bunches = {p["pair_id"]: p for p in summary["bunching"]}
started, cleared = analytics.diff_keys(self._active_bunches, current_bunches)
for pair_id in started:
    events.append({"kind": "bunching_started", "severity": "warn",
                   "message": f"Route {route} dir {dir}: vehicles {a} and {b} are {d:.0f} m apart", ...})
for pair_id in cleared:
    events.append({"kind": "bunching_cleared", "severity": "ok",
                   "message": f"Route {route}: gap recovered", ...})
```

The same for ghosts (`vehicle_stale` / `vehicle_recovered`) and fleet anomalies. Without this, a
single bunched pair would fill the event log with the same message every 8 seconds.

---

## 14. State, History and Held Alerts

`Store` in `state.py` keeps:
- the latest snapshot,
- a **240-tick ring buffer** (about 30 minutes) for charts and replay,
- the event log (`EVENT_LOG_SIZE`, 120),
- a separate **held-alert** log (`ALERT_LOG_SIZE`, 25) for severe events.

### Why a second log for alerts?
During peak hour, routine bunching events can flood the main log. Without a separate log, a fleet
anomaly could be pushed out within minutes.

### Replay on connect
A browser joining mid-stream immediately receives the last ~30 minutes, so the page is never blank.

---

## 15. Fan-Out to Browsers

```python
def broadcast(self, message):
    for sub in list(self._subscribers):
        try:
            sub.queue.put_nowait(message)
        except asyncio.QueueFull:
            sub.queue.get_nowait()          # drop that browser's stalest tick
            sub.queue.put_nowait(message)
```

The `Hub` puts one shared message into every browser's own queue. A **slow browser drops its own
oldest tick**; it can never stall ingestion or any other viewer.

---

## 16. The WebSocket Protocol & Server-Side Filtering

1. The browser connects to `WS /ws`.
2. The server sends a replay of recent history.
3. The server pushes every new tick.
4. At any time, the browser can send a filter:

```json
{"type": "subscribe", "routes": ["1", "22"], "bbox": [south, west, north, east]}
```

The server then sends only vehicles on those routes inside that map box. Measured live: **113.7 KB →
14.6 KB per tick**. Metrics stay network-wide, so someone watching one route still sees the fleet drop.

Filtering happens in **each socket's own send loop**, not in the Hub, so an expensive filter costs only
that browser. Malformed or binary frames are ignored rather than closing the connection (tested).

---

## 17. REST API

| Route | Returns |
|---|---|
| `GET /api/health` | feed connection status, subscriber counts, vehicles, ticks buffered, learned routes |
| `GET /api/snapshot` | the latest tick |
| `GET /api/events?limit=50` | recent start and clear events |
| `GET /api/history` | the ring buffer |
| `GET /api/baselines` | each route's learned threshold, normal spacing and sample count |
| `WS /ws` | the live stream |

---

## 18. The Dashboard

Next.js 15, React, Tailwind, Leaflet via react-leaflet.

| Component | Purpose |
|---|---|
| `FleetMap` | every vehicle, state-coded; bunched pairs and ghosts highlighted |
| `TimelineChart` | fleet size and bunched pairs, as two separate charts |
| `EventFeed` | start and clear events, plus held alerts |
| `RouteTable` | per-route counts and learned thresholds; click a route to filter everything |
| `StatTile` | headline numbers |
| `useLiveFeed` | the WebSocket, reconnects, sending filters as you pan and zoom |

---

## 19. Design Choices in the Charts and Map

- **Two charts, never one chart with two axes.** Fleet size (~660) and bunched pairs (~20) live on
  different scales; a dual axis invites readers to see a correlation that isn't there.
- **Fleet size uses a fitted axis, and says so.** It moves a few percent around 660; a zero-based area
  would be a flat block. So it is a line with the observed range printed under the title.
- **State is shown twice on the map**, by colour **and** by size and fill, so it works for colourblind
  viewers and in print. Every event has an icon and a text label.
- **Light and dark each have their own basemap** (Esri's grey canvas, keyless), not an inverted filter.
- **Vehicles draw to canvas** (`preferCanvas`) so ~660 live markers redraw smoothly. The canvas cannot
  read CSS variables, so `useThemeTokens.ts` resolves the colours.

---

## 20. Simulation Mode

`SIMULATE=1` streams a deterministic synthetic fleet: one route seeded to bunch, and vehicles that
freeze at random so the ghost detector fires. It lets you work on the frontend without a network. It is
**never the default**, and the header says `SIMULATED DATA` when it is on.

---

## 21. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FEED_URL` | MBTA VehiclePositions | GTFS-RT endpoint |
| `FEED_NAME` | `MBTA (Boston, MA)` | shown in the header |
| `FEED_API_KEY_HEADER`, `FEED_API_KEY` | — | auth header, if the agency needs one |
| `POLL_INTERVAL_SEC` | 8 | poll cadence |
| `BUNCH_DISTANCE_M` | 220 | threshold before a route has learned its own |
| `BUNCH_ADAPTIVE` | 1 | learn per-route thresholds |
| `BUNCH_RATIO` | 0.30 | share of normal spacing that counts as collapsed |
| `BUNCH_BASELINE_PCT` | 75 | percentile taken as normal spacing |
| `BUNCH_BASELINE_WINDOW`, `BUNCH_BASELINE_MIN_SAMPLES` | 600, 90 | gaps kept, and needed before learning is used |
| `BUNCH_DISTANCE_MIN_M`, `BUNCH_DISTANCE_MAX_M` | 80, 400 | clamps on the learned threshold |
| `STALE_AFTER_SEC` | 150 | ghost threshold |
| `EXCLUDED_ROUTE_PREFIXES` | `Shuttle-` | routes skipped |
| `ZSCORE_WINDOW`, `ZSCORE_THRESHOLD` | 30, 2.5 | fleet anomaly sensitivity |
| `EVENT_LOG_SIZE`, `ALERT_LOG_SIZE` | 120, 25 | event log and held-alert log |
| `MAP_CENTER_LAT`, `MAP_CENTER_LON`, `MAP_ZOOM` | Boston | initial map view |
| `SIMULATE` | 0 | synthetic fleet |

Frontend: `NEXT_PUBLIC_WS_URL`, default `ws://127.0.0.1:8100/ws`.

---

## 22. Using Another City's Feed

Any `VehiclePositions.pb` endpoint works (Delhi OTD, BMTC, TriMet, Bay Area 511, MTA):

```bash
FEED_URL=https://otd.delhi.gov.in/api/realtime/VehiclePositions.pb \
FEED_NAME="DTC (Delhi)" \
FEED_API_KEY_HEADER=key \
FEED_API_KEY=$OTD_KEY \
MAP_CENTER_LAT=28.6139 MAP_CENTER_LON=77.2090 \
./.venv/bin/uvicorn app.main:app --port 8100
```

After switching, check `EXCLUDED_ROUTE_PREFIXES`: other agencies name their spare-vehicle routes
differently.

---

## 23. Testing

```bash
cd backend
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -v          # 81 tests
```

| File | Covers |
|---|---|
| `test_analytics.py` | detectors, shuttle and layover filters, learned baselines, clamps, z-scores |
| `test_state.py` | store, ring buffer, start/clear events, held alerts, subscriber filters |
| `test_ws.py` | the WebSocket protocol through a real socket, including malformed and binary frames |

---

## 24. Limitations

| Limitation | Detail |
|---|---|
| straight-line distance | vehicles on opposite sides of a hairpin can look close; loading `shapes.txt` and measuring along the route would fix it and let the 400 m cap rise |
| spatial gap, not minutes | converting to "minutes apart" needs the schedule or a speed estimate |
| memory only | a restart loses history; one process. TimescaleDB and Redis pub/sub are the next step |
| most thresholds hit the cap | 37 of 41 learned route-directions wanted more than 400 m, a consequence of straight-line distance |
| no time-of-day awareness | as evening service thins, learned thresholds rise |
| filtering by route and box only | a viewer watching the whole network gets the whole fleet |
| not deployed | needs a Render or Railway login for the WebSocket server |

---

## 25. Troubleshooting

| Problem | Fix |
|---|---|
| dashboard stays empty | check `/api/health`; try `SIMULATE=1` to test the frontend |
| nothing connects in production | set `NEXT_PUBLIC_WS_URL` to the deployed `wss://` address before building |
| too many bunching alerts in a new city | add its spare-vehicle route prefix to `EXCLUDED_ROUTE_PREFIXES` |
| `run.sh` needs Python 3.10 | `PYTHON=/usr/bin/python3.12 ./run.sh` |
| a threshold looks wrong | `GET /api/baselines` shows each learned number and its sample count |

---

## 26. Complete Feature Summary

### All Features Built

| # | Feature | Type | Key Files |
|---|---|---|---|
| 1 | GTFS-Realtime polling with backoff | Backend | `ingest.py` |
| 2 | Bunching detection | Detection | `analytics.py` |
| 3 | Per-route learned thresholds | Detection | `analytics.py` |
| 4 | Measured 400 m ceiling | Detection | `config.py` |
| 5 | Staging-lot and layover filters | Detection | `analytics.py` |
| 6 | Ghost vehicle detection | Detection | `analytics.py` |
| 7 | Fleet anomaly z-score | Detection | `analytics.py` |
| 8 | Start/clear events | State | `state.py` |
| 9 | 30-minute replay and held alerts | State | `state.py` |
| 10 | Non-blocking fan-out | Backend | `state.py` |
| 11 | WebSocket filters by route and viewport | Backend | `main.py`, `state.py` |
| 12 | REST API including baselines | Backend | `main.py` |
| 13 | Canvas map with 660 live markers | Frontend | `FleetMap.tsx` |
| 14 | Separate honest charts | Frontend | `TimelineChart.tsx` |
| 15 | Route table with learned thresholds | Frontend | `RouteTable.tsx` |
| 16 | Light and dark with own basemaps | Frontend | `useIsDark.ts`, `useThemeTokens.ts` |
| 17 | Simulation mode | Tooling | `simulate.py` |
| 18 | One-command start | Tooling | `run.sh` |

### Data Flow Architecture

```
GTFS-Realtime feed (protobuf over HTTP)
  └── ingest.py: poll every 8 s, back off on failure ──► parse_feed()
        └── analytics.py
              RouteBaselines.observe() ──► threshold per route-direction
              find_bunching(threshold_for) · find_stale() · rolling_zscore()
              └── summarize()
        └── Store.record_tick()
              snapshot + 240-tick ring + start/clear events + held alerts
        └── Hub.broadcast() ──► each Subscriber queue (drops its own stalest tick)

Browser ──WS /ws──► replay first, then every tick
  └── sends {"type":"subscribe","routes":[...],"bbox":[...]}
        └── that socket's send loop filters vehicles (metrics stay network-wide)
Browser ──REST──► /api/health · /api/snapshot · /api/events · /api/history · /api/baselines
```

### Tech Stack at a Glance

```
Backend:   FastAPI, asyncio, httpx, gtfs-realtime-bindings (Python 3.10+)
Frontend:  Next.js 15 + React + Tailwind + Leaflet (react-leaflet)
Data:      MBTA GTFS-Realtime (keyless), or any agency's feed
State:     in memory: snapshot, 240-tick ring buffer, event logs
Testing:   pytest, real WebSocket tests
```
