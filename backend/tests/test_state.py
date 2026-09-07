"""Tests for fan-out, per-subscriber filtering and the retained alert log.

The filter is the only surface a browser can drive on the server, so most of
what is pinned here is that malformed input leaves a subscriber unfiltered
rather than raising into the socket loop.
"""

import pytest

from app import config
from app.state import MAX_FILTER_ROUTES, Hub, Store, Subscriber


def veh(vid, route="1", lat=42.36, lon=-71.06):
    return {
        "id": vid, "label": vid, "route_id": route, "trip_id": None,
        "direction_id": 0, "lat": lat, "lon": lon, "bearing": None,
        "speed": 5.0, "ts": None, "stop_id": None,
        "status": "in_transit_to", "occupancy": None,
    }


def tick(vehicles):
    return {"type": "tick", "ts": 0.0, "vehicles": vehicles, "metrics": {}, "events": []}


class TestSubscriberFilter:
    def test_unfiltered_by_default(self):
        s = Subscriber()
        assert s.filtered is False
        out = s.shape(tick([veh("a"), veh("b", route="66")]))
        assert len(out["vehicles"]) == 2
        assert out["filter"]["vehicles_sent"] == 2

    def test_route_filter_keeps_only_that_route(self):
        s = Subscriber()
        assert s.set_filter(["66"], None) is True
        out = s.shape(tick([veh("a", route="1"), veh("b", route="66")]))
        assert [v["id"] for v in out["vehicles"]] == ["b"]
        assert out["filter"]["vehicles_sent"] == 1
        assert out["filter"]["vehicles_total"] == 2

    def test_bbox_filter_keeps_only_the_viewport(self):
        s = Subscriber()
        s.set_filter(None, [42.0, -71.5, 42.5, -71.0])
        out = s.shape(tick([veh("inside", lat=42.36, lon=-71.06),
                            veh("outside", lat=40.0, lon=-74.0)]))
        assert [v["id"] for v in out["vehicles"]] == ["inside"]

    def test_route_and_bbox_are_both_applied(self):
        s = Subscriber()
        s.set_filter(["1"], [42.0, -71.5, 42.5, -71.0])
        out = s.shape(tick([
            veh("right-route-right-place", route="1", lat=42.36),
            veh("right-route-wrong-place", route="1", lat=10.0),
            veh("wrong-route-right-place", route="66", lat=42.36),
        ]))
        assert [v["id"] for v in out["vehicles"]] == ["right-route-right-place"]

    def test_vehicle_without_a_position_cannot_be_in_a_bbox(self):
        s = Subscriber()
        s.set_filter(None, [42.0, -71.5, 42.5, -71.0])
        ghost = veh("g")
        ghost["lat"] = ghost["lon"] = None
        assert s.shape(tick([ghost]))["vehicles"] == []

    def test_clearing_the_filter_restores_the_whole_fleet(self):
        s = Subscriber()
        s.set_filter(["66"], None)
        assert s.set_filter(None, None) is True
        assert s.filtered is False
        assert len(s.shape(tick([veh("a"), veh("b", route="66")]))["vehicles"]) == 2

    def test_setting_the_same_filter_twice_reports_no_change(self):
        # The socket only pushes a fresh replay when something actually moved.
        s = Subscriber()
        assert s.set_filter(["66"], None) is True
        assert s.set_filter(["66"], None) is False

    @pytest.mark.parametrize(
        "routes",
        ["66", 66, {}, [], [None], [{"route": "66"}]],
    )
    def test_malformed_route_filters_are_ignored(self, routes):
        s = Subscriber()
        s.set_filter(routes, None)
        assert s.routes is None

    @pytest.mark.parametrize(
        "bbox",
        [
            [1, 2, 3],                       # too short
            [1, 2, 3, 4, 5],                 # too long
            "42,-71,43,-70",                 # not a list
            ["a", "b", "c", "d"],            # not numbers
            [42.5, -71.5, 42.0, -71.0],      # south above north
            [42.0, -71.0, 42.5, -71.5],      # west east of east
            [-200.0, -71.5, 200.0, -71.0],   # off the planet
        ],
    )
    def test_malformed_bboxes_are_ignored(self, bbox):
        s = Subscriber()
        s.set_filter(None, bbox)
        assert s.bbox is None

    def test_route_list_is_capped(self):
        s = Subscriber()
        s.set_filter([str(i) for i in range(MAX_FILTER_ROUTES * 3)], None)
        assert len(s.routes) == MAX_FILTER_ROUTES

    def test_status_messages_pass_through_untouched(self):
        # A feed failure has no vehicle array to narrow; filtering it would
        # bolt a meaningless `filter` block onto an error.
        s = Subscriber()
        s.set_filter(["66"], None)
        msg = {"type": "status", "ts": 0.0, "status": {}, "events": []}
        assert s.shape(msg) is msg


class TestHub:
    def test_broadcast_reaches_every_subscriber(self):
        hub = Hub()
        a, b = hub.subscribe(), hub.subscribe()
        hub.broadcast({"type": "tick"})
        assert a.queue.qsize() == 1 and b.queue.qsize() == 1

    def test_a_slow_subscriber_drops_its_stalest_tick_and_keeps_the_newest(self):
        hub = Hub()
        sub = hub.subscribe()
        for i in range(sub.queue.maxsize + 4):
            hub.broadcast({"type": "tick", "n": i})
        assert sub.queue.qsize() == sub.queue.maxsize
        newest = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())][-1]
        assert newest["n"] == sub.queue.maxsize + 3

    def test_unsubscribe_stops_delivery(self):
        hub = Hub()
        sub = hub.subscribe()
        hub.unsubscribe(sub)
        hub.broadcast({"type": "tick"})
        assert sub.queue.qsize() == 0
        assert hub.subscriber_count == 0

    def test_filtered_subscribers_are_counted(self):
        hub = Hub()
        hub.subscribe()
        hub.subscribe().set_filter(["66"], None)
        assert hub.filtered_subscribers == 1


class TestRetainedAlerts:
    def test_an_alert_survives_a_flood_of_routine_events(self):
        """The bug this feature exists for.

        A fleet anomaly is the rarest and most serious thing here, and the
        shared event log is sized for the busiest. Without a second log the
        alert is gone within a minute of being raised.
        """
        store = Store()
        store.alerts.appendleft({"kind": "fleet_anomaly", "severity": "alert"})
        for i in range(config.EVENT_LOG_SIZE * 2):
            store.events.appendleft({"kind": "bunching_started", "severity": "warn"})

        assert all(e["kind"] != "fleet_anomaly" for e in store.events)
        assert store.alerts[0]["kind"] == "fleet_anomaly"

    def test_feed_errors_are_retained_as_alerts(self):
        store = Store()
        msg = store.record_failure("connection refused")
        assert store.alerts[0]["kind"] == "feed_error"
        assert msg["alerts"][0]["kind"] == "feed_error"

    def test_the_alert_log_is_bounded(self):
        store = Store()
        for i in range(config.ALERT_LOG_SIZE * 3):
            store.record_failure(f"error {i}")
        assert len(store.alerts) == config.ALERT_LOG_SIZE
        assert store.alerts[0]["detail"]["failures"] == config.ALERT_LOG_SIZE * 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
