"""Tests for the detectors.

These pin the behaviour that was actually wrong on live MBTA data: the raw
detector counted parked shuttles and terminal layovers as bunching, which was
more than half of all detections.
"""

import time

import pytest

from app import analytics, config


def vehicle(vid, route="1", direction=0, lat=42.36, lon=-71.06, ts=None, **kw):
    base = {
        "id": vid,
        "label": vid,
        "route_id": route,
        "trip_id": f"trip-{vid}",
        "direction_id": direction,
        "lat": lat,
        "lon": lon,
        "bearing": 0.0,
        "speed": 5.0,
        "ts": time.time() if ts is None else ts,
        "stop_id": None,
        "status": "in_transit_to",
        "occupancy": None,
    }
    base.update(kw)
    return base


class TestHaversine:
    def test_known_distance(self):
        # One degree of latitude is ~111.2 km.
        d = analytics.haversine_m(42.0, -71.0, 43.0, -71.0)
        assert 111_000 < d < 111_400

    def test_zero(self):
        assert analytics.haversine_m(42.36, -71.06, 42.36, -71.06) == 0.0


class TestBunching:
    def test_close_pair_on_same_route_and_direction_is_bunched(self):
        pairs = analytics.find_bunching(
            [vehicle("a", lat=42.3600), vehicle("b", lat=42.3608)]
        )
        assert len(pairs) == 1
        assert pairs[0]["vehicles"] == ["a", "b"]
        assert pairs[0]["distance_m"] < config.BUNCH_DISTANCE_M

    def test_far_apart_is_not_bunched(self):
        assert analytics.find_bunching(
            [vehicle("a", lat=42.3600), vehicle("b", lat=42.3800)]
        ) == []

    def test_opposite_directions_are_not_bunched(self):
        # Two vehicles passing each other on opposite sides of the street are
        # nose to nose but serve different riders.
        assert analytics.find_bunching(
            [vehicle("a", direction=0), vehicle("b", direction=1)]
        ) == []

    def test_different_routes_are_not_bunched(self):
        assert analytics.find_bunching(
            [vehicle("a", route="1"), vehicle("b", route="66")]
        ) == []

    def test_excluded_route_prefix_is_ignored(self):
        assert analytics.find_bunching(
            [
                vehicle("a", route="Shuttle-Generic"),
                vehicle("b", route="Shuttle-Generic", lat=42.3601),
            ]
        ) == []

    def test_both_stopped_at_the_same_stop_is_a_layover_not_bunching(self):
        assert analytics.find_bunching(
            [
                vehicle("a", status="stopped_at", stop_id="70200"),
                vehicle("b", status="stopped_at", stop_id="70200", lat=42.3601),
            ]
        ) == []

    def test_one_moving_at_the_same_stop_is_still_bunching(self):
        pairs = analytics.find_bunching(
            [
                vehicle("a", status="stopped_at", stop_id="70200"),
                vehicle("b", status="in_transit_to", stop_id="70200", lat=42.3601),
            ]
        )
        assert len(pairs) == 1

    def test_pair_id_is_stable_regardless_of_input_order(self):
        forward = analytics.find_bunching(
            [vehicle("a"), vehicle("b", lat=42.3601)]
        )
        backward = analytics.find_bunching(
            [vehicle("b", lat=42.3601), vehicle("a")]
        )
        assert forward[0]["pair_id"] == backward[0]["pair_id"]

    def test_three_close_vehicles_yield_three_pairs(self):
        pairs = analytics.find_bunching(
            [
                vehicle("a", lat=42.3600),
                vehicle("b", lat=42.3602),
                vehicle("c", lat=42.3604),
            ]
        )
        assert len(pairs) == 3

    def test_missing_route_is_skipped(self):
        assert analytics.find_bunching(
            [vehicle("a", route=None), vehicle("b", route=None, lat=42.3601)]
        ) == []


class TestStale:
    def test_old_position_is_stale(self):
        now = time.time()
        stale = analytics.find_stale(
            [vehicle("a", ts=now - config.STALE_AFTER_SEC - 10)], now
        )
        assert [s["id"] for s in stale] == ["a"]

    def test_fresh_position_is_not_stale(self):
        now = time.time()
        assert analytics.find_stale([vehicle("a", ts=now - 5)], now) == []

    def test_excluded_route_prefix_is_ignored(self):
        # A staging-lot shuttle with an idle transponder is not a ghost bus --
        # the same exclusion bunching applies has to apply here too.
        now = time.time()
        assert analytics.find_stale(
            [vehicle("a", route="Shuttle-Generic", ts=now - 9999)], now
        ) == []

    def test_excluded_routes_stay_out_of_worst_routes(self):
        now = time.time()
        s = analytics.summarize(
            [
                vehicle("a", route="Shuttle-Generic", ts=now - 9999),
                vehicle("b", route="Shuttle-Generic", lat=42.3601, ts=now - 9999),
            ],
            now,
        )
        assert s["worst_routes"] == []
        assert s["stale"] == 0
        assert s["bunched_pairs"] == 0

    def test_vehicle_without_timestamp_is_skipped(self):
        # No timestamp means the agency never reported one -- absence of
        # evidence, so it is not called a ghost.
        assert analytics.find_stale([vehicle("a", ts=None)], time.time()) == []

    def test_sorted_oldest_first(self):
        now = time.time()
        stale = analytics.find_stale(
            [
                vehicle("a", ts=now - 200),
                vehicle("b", ts=now - 900),
            ],
            now,
        )
        assert [s["id"] for s in stale] == ["b", "a"]


class TestZScore:
    def test_none_until_enough_history(self):
        assert analytics.rolling_zscore([1, 2, 3], 4) is None

    def test_none_when_window_is_flat(self):
        assert analytics.rolling_zscore([10] * 12, 10) is None

    def test_outlier_scores_high(self):
        series = [100, 101, 99, 100, 102, 98, 100, 101, 99, 100]
        z = analytics.rolling_zscore(series, 130)
        assert z is not None and z > 3

    def test_typical_value_scores_low(self):
        series = [100, 101, 99, 100, 102, 98, 100, 101, 99, 100]
        z = analytics.rolling_zscore(series, 100)
        assert z is not None and abs(z) < 1


class TestSummarize:
    def test_counts_and_worst_routes(self):
        now = time.time()
        vehicles = [
            vehicle("a", route="1", lat=42.3600),
            vehicle("b", route="1", lat=42.3602),
            vehicle("c", route="66", lat=42.4000),
            vehicle("d", route="66", lat=42.5000, ts=now - 9999),
        ]
        s = analytics.summarize(vehicles, now)
        assert s["active"] == 4
        assert s["routes"] == 2
        assert s["bunched_pairs"] == 1
        assert s["bunched_vehicles"] == 2
        assert s["stale"] == 1
        worst = {r["route_id"]: r for r in s["worst_routes"]}
        assert worst["1"]["bunched"] == 1
        assert worst["66"]["stale"] == 1

    def test_empty_fleet(self):
        s = analytics.summarize([], time.time())
        assert s["active"] == 0
        assert s["bunched_pairs"] == 0
        assert s["avg_speed_mps"] == 0.0


class TestDiffKeys:
    def test_started_and_cleared(self):
        started, cleared = analytics.diff_keys({"a", "b"}, {"b", "c"})
        assert started == {"c"}
        assert cleared == {"a"}


class TestPercentile:
    def test_single_value(self):
        assert analytics.percentile([42.0], 75) == 42.0

    def test_interpolates_between_points(self):
        # p50 of [0, 10] sits halfway between them.
        assert analytics.percentile([0.0, 10.0], 50) == 5.0

    def test_endpoints(self):
        vals = [1.0, 2.0, 3.0, 4.0]
        assert analytics.percentile(vals, 0) == 1.0
        assert analytics.percentile(vals, 100) == 4.0

    def test_ignores_input_order(self):
        assert analytics.percentile([9.0, 1.0, 5.0], 50) == 5.0

    def test_empty_is_an_error_not_a_silent_zero(self):
        with pytest.raises(ValueError):
            analytics.percentile([], 50)


class TestNearestNeighbourGaps:
    def test_single_vehicle_has_no_gap(self):
        assert analytics.nearest_neighbour_gaps([vehicle("a")]) == []

    def test_gap_is_to_the_closest_peer_not_the_furthest(self):
        gaps = analytics.nearest_neighbour_gaps(
            [
                vehicle("a", lat=42.3600),
                vehicle("b", lat=42.3610),   # ~111 m from a
                vehicle("c", lat=42.4000),   # ~4.4 km from a
            ]
        )
        # a and b are each other's nearest; c's nearest is b.
        assert len(gaps) == 3
        assert gaps[0] < 200 and gaps[1] < 200
        assert gaps[2] > 4000


class TestRouteBaselines:
    def spread_fleet(self, route="1", direction=0, n=6, spacing_deg=0.009):
        """A route running normally: vehicles evenly spaced ~1 km apart."""
        return [
            vehicle(f"{route}-{i}", route=route, direction=direction,
                    lat=42.30 + i * spacing_deg)
            for i in range(n)
        ]

    def test_falls_back_to_the_global_threshold_while_learning(self):
        b = analytics.RouteBaselines()
        b.observe(self.spread_fleet())
        assert b.threshold_m(("1", 0)) == config.BUNCH_DISTANCE_M
        assert b.normal_spacing_m(("1", 0)) is None

    def test_learns_a_wider_threshold_for_a_widely_spaced_route(self):
        b = analytics.RouteBaselines()
        fleet = self.spread_fleet()
        for _ in range(30):          # 30 ticks * 6 vehicles = 180 samples
            b.observe(fleet)
        spacing = b.normal_spacing_m(("1", 0))
        assert spacing is not None and spacing > 900
        # 0.35 * ~1000 m is above the 220 m global default, so this route now
        # flags pairs the fixed threshold would have missed entirely.
        assert b.threshold_m(("1", 0)) > config.BUNCH_DISTANCE_M

    def test_threshold_is_clamped_at_both_ends(self):
        b = analytics.RouteBaselines()
        # Vehicles ~10 km apart would imply a threshold far above the ceiling.
        for _ in range(40):
            b.observe(self.spread_fleet(n=5, spacing_deg=0.09))
        assert b.threshold_m(("1", 0)) == config.BUNCH_DISTANCE_MAX_M

        tight = analytics.RouteBaselines()
        # Vehicles ~11 m apart would imply a threshold below the floor.
        for _ in range(40):
            tight.observe(self.spread_fleet(n=5, spacing_deg=0.0001))
        assert tight.threshold_m(("1", 0)) == config.BUNCH_DISTANCE_MIN_M

    def test_bunching_does_not_teach_the_route_to_ignore_it(self):
        """The reason the baseline is a high percentile, pinned.

        Three quarters of this route runs normally and one quarter is bunched.
        Learning from the median would drag the threshold toward the collapsed
        gaps; the upper quartile barely moves.
        """
        b = analytics.RouteBaselines()
        normal = self.spread_fleet(n=6)
        bunched = [
            vehicle("x", lat=42.3600),
            vehicle("y", lat=42.3601),
            vehicle("z", lat=42.3602),
        ]
        for i in range(40):
            b.observe(bunched if i % 4 == 0 else normal)
        assert b.threshold_m(("1", 0)) > config.BUNCH_DISTANCE_M

    def test_directions_learn_separately(self):
        b = analytics.RouteBaselines()
        for _ in range(30):
            b.observe(
                self.spread_fleet(direction=0, spacing_deg=0.009)
                + self.spread_fleet(direction=1, spacing_deg=0.0009)
            )
        assert b.normal_spacing_m(("1", 0)) > b.normal_spacing_m(("1", 1))

    def test_layovers_do_not_teach_a_route_that_parking_is_normal_spacing(self):
        """Vehicles laying over together are held out of the baseline.

        They are already excluded from detection; letting them into the
        learning set would drag the route's normal spacing toward zero. On
        live MBTA data this understated route 66's spacing by 79%.
        """
        running = self.spread_fleet(n=6)
        laid_over = [
            vehicle("p", status="stopped_at", stop_id="70200", lat=42.3600),
            vehicle("q", status="stopped_at", stop_id="70200", lat=42.3601),
        ]
        with_layovers = analytics.RouteBaselines()
        without = analytics.RouteBaselines()
        for _ in range(30):
            with_layovers.observe(running + laid_over)
            without.observe(running)
        # The two must agree: the parked pair contributed nothing either way.
        assert with_layovers.normal_spacing_m(("1", 0)) == pytest.approx(
            without.normal_spacing_m(("1", 0))
        )

    def test_a_vehicle_stopped_alone_still_counts_as_running(self):
        # One bus dwelling at a stop is ordinary service, not a layover.
        b = analytics.RouteBaselines()
        fleet = self.spread_fleet(n=6)
        fleet[0] = {**fleet[0], "status": "stopped_at", "stop_id": "70200"}
        for _ in range(30):
            b.observe(fleet)
        assert b.explain(("1", 0))["samples"] == 180

    def test_excluded_routes_are_never_learned(self):
        b = analytics.RouteBaselines()
        for _ in range(40):
            b.observe(self.spread_fleet(route="Shuttle-Generic"))
        assert b.keys() == []
        assert b.learned_routes() == 0

    def test_explain_reports_what_the_number_was_built_from(self):
        b = analytics.RouteBaselines()
        for _ in range(30):
            b.observe(self.spread_fleet())
        e = b.explain(("1", 0))
        assert e["learned"] is True
        assert e["samples"] == 180
        assert e["threshold_m"] == round(
            min(max(config.BUNCH_RATIO * e["normal_spacing_m"],
                    config.BUNCH_DISTANCE_MIN_M), config.BUNCH_DISTANCE_MAX_M), 1
        )

    def test_find_bunching_uses_the_learned_threshold(self):
        b = analytics.RouteBaselines()
        for _ in range(30):
            b.observe(self.spread_fleet())
        # Place the pair between the global default and this route's learned
        # threshold: the fixed rule misses it, the adaptive one does not.
        learned = b.threshold_m(("1", 0))
        assert learned > config.BUNCH_DISTANCE_M, "test needs a wider learned threshold"
        gap_m = (config.BUNCH_DISTANCE_M + learned) / 2
        pair = [vehicle("a", lat=42.3600), vehicle("b", lat=42.3600 + gap_m / 111_320)]
        assert analytics.find_bunching(pair) == []
        found = analytics.find_bunching(pair, b.threshold_m)
        assert len(found) == 1
        assert found[0]["threshold_m"] == round(b.threshold_m(("1", 0)), 1)

    def test_summarize_without_baselines_is_unchanged(self):
        s = analytics.summarize([vehicle("a"), vehicle("b", lat=42.3601)])
        assert s["bunched_pairs"] == 1
        assert s["learned_routes"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
