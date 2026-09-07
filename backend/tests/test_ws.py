"""End-to-end WebSocket tests against the real app.

The filter protocol is the one place a browser drives the server, so these run
through an actual socket rather than calling the filter directly: the bugs
worth catching here are in the socket loop, not in the predicate.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.state import store


def veh(vid, route="1", lat=42.36, lon=-71.06):
    return {
        "id": vid, "label": vid, "route_id": route, "trip_id": None,
        "direction_id": 0, "lat": lat, "lon": lon, "bearing": None,
        "speed": 5.0, "ts": None, "stop_id": None,
        "status": "in_transit_to", "occupancy": None,
    }


@pytest.fixture
def fleet(monkeypatch):
    """A tiny fixed fleet, so a live feed is never needed to run the tests."""
    monkeypatch.setattr(config, "SIMULATE", True)
    vehicles = [
        veh("a", route="1", lat=42.36),
        veh("b", route="66", lat=42.37),
        veh("c", route="66", lat=10.0),   # far outside any Boston viewport
    ]
    monkeypatch.setattr(store, "vehicles", vehicles)
    return vehicles


@pytest.fixture
def client(monkeypatch):
    # The ingestion task would start polling the network on app startup.
    async def no_ingestion(stop):
        return

    monkeypatch.setattr("app.main.ingest.run_ingestion", no_ingestion)
    with TestClient(app) as c:
        yield c


def test_replay_arrives_before_any_tick(client, fleet):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "replay"
    assert len(msg["vehicles"]) == 3
    assert msg["filter"]["vehicles_sent"] == 3
    assert "alerts" in msg


def test_subscribing_to_a_route_narrows_the_next_message(client, fleet):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # initial replay
        ws.send_json({"type": "subscribe", "routes": ["66"]})
        msg = ws.receive_json()
    assert {v["id"] for v in msg["vehicles"]} == {"b", "c"}
    assert msg["filter"]["vehicles_sent"] == 2
    assert msg["filter"]["vehicles_total"] == 3


def test_subscribing_to_a_viewport_narrows_the_next_message(client, fleet):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "subscribe", "bbox": [42.0, -71.5, 42.5, -71.0]})
        msg = ws.receive_json()
    assert {v["id"] for v in msg["vehicles"]} == {"a", "b"}


def test_widening_the_filter_pushes_a_fresh_replay_immediately(client, fleet):
    """Without this the map is missing vehicles until the next poll."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "subscribe", "routes": ["66"]})
        assert len(ws.receive_json()["vehicles"]) == 2
        ws.send_json({"type": "subscribe", "routes": None})
        msg = ws.receive_json()
    assert len(msg["vehicles"]) == 3


def test_a_malformed_frame_does_not_kill_the_socket(client, fleet):
    """A bad frame is ignored, not fatal.

    json.JSONDecodeError subclasses ValueError, so catching ValueError around
    the whole read loop would close a live socket on one bad frame.
    """
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("{not json at all")
        ws.send_text(json.dumps(["a", "list", "not", "an", "object"]))
        ws.send_json({"type": "something-else"})
        # The socket is still usable.
        ws.send_json({"type": "subscribe", "routes": ["66"]})
        msg = ws.receive_json()
    assert msg["filter"]["vehicles_sent"] == 2


def test_a_binary_frame_does_not_kill_the_socket(client, fleet):
    """receive_text asserts on a binary frame; the assertion must not escape."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00\x01\x02not text at all")
        ws.send_json({"type": "subscribe", "routes": ["66"]})
        msg = ws.receive_json()
    assert msg["filter"]["vehicles_sent"] == 2


def test_a_nonsense_filter_leaves_the_client_unfiltered(client, fleet):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "subscribe", "bbox": ["north", "south", 1, 2]})
        ws.send_json({"type": "subscribe", "routes": ["66"]})
        msg = ws.receive_json()
    # Only the valid route filter took effect; the bad bbox was dropped.
    assert msg["filter"]["bbox"] is None
    assert msg["filter"]["routes"] == ["66"]


def test_health_reports_subscriber_and_baseline_state(client, fleet):
    body = client.get("/api/health").json()
    assert body["vehicles"] == 3
    assert body["filtered_subscribers"] == 0
    assert "learned_routes" in body


def test_baselines_endpoint_is_inspectable(client, fleet):
    body = client.get("/api/baselines").json()
    assert body["global_fallback_m"] == config.BUNCH_DISTANCE_M
    assert isinstance(body["routes"], list)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
