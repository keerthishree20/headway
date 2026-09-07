"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import EventFeed from "@/components/EventFeed";
import RouteTable from "@/components/RouteTable";
import StatTile from "@/components/StatTile";
import TimelineChart from "@/components/TimelineChart";
import { useLiveFeed } from "@/lib/useLiveFeed";

// Leaflet touches `window` at import time, so it must not render on the server.
const FleetMap = dynamic(() => import("@/components/FleetMap"), {
  ssr: false,
  loading: () => (
    <div
      className="flex h-full items-center justify-center text-xs"
      style={{ color: "var(--text-muted)" }}
    >
      Loading map…
    </div>
  ),
});

const LEGEND = [
  { label: "In service", token: "var(--series-1)", size: 7 },
  { label: "Bunched", token: "var(--status-critical)", size: 13 },
  { label: "Not reporting", token: "var(--status-warning)", size: 10 },
];

function bytes(n: number): string {
  return n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`;
}

export default function Page() {
  const live = useLiveFeed();
  const { setFilter } = live;
  const [routeFilter, setRouteFilter] = useState<string | null>(null);
  const bboxRef = useRef<[number, number, number, number] | null>(null);
  const m = live.metrics;

  // Selecting a route stops the server sending the other several hundred
  // vehicles, rather than shipping the whole fleet and hiding most of it here.
  useEffect(() => {
    setFilter({ routes: routeFilter ? [routeFilter] : null, bbox: bboxRef.current });
  }, [routeFilter, setFilter]);

  const handleViewport = useCallback(
    (bbox: [number, number, number, number]) => {
      bboxRef.current = bbox;
      setFilter({ routes: routeFilter ? [routeFilter] : null, bbox });
    },
    [routeFilter, setFilter],
  );

  const f = live.filter;
  const withheld = f ? f.vehicles_total - f.vehicles_sent : 0;
  // The z-score is null until there is enough history and absent entirely from
  // a replay taken before the first tick, so treat anything non-numeric as "—".
  // `+ 0` collapses -0 so a fleet sitting on its mean never renders "-0.0".
  const z = typeof m?.fleet_zscore === "number" ? m.fleet_zscore + 0 : null;

  const feedAge =
    typeof live.feedAgeSec === "number"
      ? `${live.feedAgeSec.toFixed(0)}s behind the agency`
      : "connected";

  return (
    <main className="mx-auto flex min-h-screen max-w-[1600px] flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Headway
            <span
              className="ml-2 text-sm font-normal"
              style={{ color: "var(--text-secondary)" }}
            >
              real-time transit reliability monitor
            </span>
          </h1>
          <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
            {live.status?.source ?? "connecting…"} · GTFS-Realtime vehicle
            positions polled every {live.config?.poll_interval_sec ?? "—"}s
            {live.status?.simulated ? " · SIMULATED DATA" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${live.connected ? "live-dot" : ""}`}
            style={{
              background: live.connected
                ? "var(--status-good)"
                : "var(--status-critical)",
            }}
            aria-hidden
          />
          <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
            {live.connected ? `Live · ${feedAge}` : "Reconnecting…"}
          </span>
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Vehicles reporting"
          value={m?.active ?? "—"}
          hint={`across ${m?.routes ?? 0} routes`}
        />
        <StatTile
          label="Bunched pairs"
          value={m?.bunched_pairs ?? "—"}
          tone={m && m.bunched_pairs > 0 ? "critical" : "good"}
          hint={
            live.config?.adaptive_bunching && m?.learned_routes
              ? `judged against each route's own spacing · ${m.learned_routes} routes learned`
              : `within ${live.config?.bunch_distance_m ?? 220} m, same route & direction`
          }
        />
        <StatTile
          label="Not reporting"
          value={m?.stale ?? "—"}
          tone={m && m.stale > 0 ? "warning" : "good"}
          hint={`no position for ${live.config?.stale_after_sec ?? 150}s+`}
        />
        <StatTile
          label="Fleet deviation"
          value={typeof z === "number" ? (Math.abs(z) < 0.05 ? "0.0" : z.toFixed(1)) : "—"}
          unit={typeof z === "number" ? "σ" : undefined}
          tone={typeof z === "number" && Math.abs(z) >= 2.5 ? "critical" : "neutral"}
          hint="rolling z-score of active fleet size"
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
        <div
          className="overflow-hidden rounded-lg border"
          style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
        >
          <div
            className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3"
            style={{ borderColor: "var(--border)" }}
          >
            <h2 className="text-sm font-semibold">
              Live fleet
              {routeFilter ? (
                <span
                  className="ml-2 font-normal"
                  style={{ color: "var(--text-secondary)" }}
                >
                  · route {routeFilter}
                </span>
              ) : null}
              {/* The saving is the point of the subscribe protocol, so it is
                  reported rather than left invisible. */}
              {f && withheld > 0 ? (
                <span
                  className="ml-2 text-[11px] font-normal"
                  style={{ color: "var(--text-muted)" }}
                  title="Vehicles outside this route or viewport are not sent at all"
                >
                  · streaming {f.vehicles_sent} of {f.vehicles_total} vehicles
                  {live.lastTickBytes ? ` (${bytes(live.lastTickBytes)}/tick)` : ""}
                </span>
              ) : null}
            </h2>
            <div className="flex flex-wrap items-center gap-4">
              {LEGEND.map((l) => (
                <span key={l.label} className="flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className="inline-block rounded-full"
                    style={{
                      background: l.token,
                      width: l.size,
                      height: l.size,
                    }}
                  />
                  <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                    {l.label}
                  </span>
                </span>
              ))}
            </div>
          </div>
          <div className="h-[420px] lg:h-[520px]">
            <FleetMap
              vehicles={live.vehicles}
              metrics={m}
              config={live.config}
              routeFilter={routeFilter}
              onViewportChange={handleViewport}
            />
          </div>
        </div>

        <div className="flex min-h-[420px] flex-col gap-4 lg:h-[calc(520px+53px)]">
          <div className="min-h-0 flex-1">
            <EventFeed
              events={live.events}
              alerts={live.alerts}
              onSelectRoute={setRouteFilter}
            />
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(320px,1fr)]">
        <TimelineChart
          title="Vehicles reporting"
          subtitle="Fleet size over the last half hour, one point per poll."
          history={live.history}
          metric="active"
          unit="vehicles"
          zeroBased={false}
        />
        <TimelineChart
          title="Bunched pairs"
          subtitle="Vehicle pairs whose gap has collapsed, per poll."
          history={live.history}
          metric="bunched_pairs"
          unit="pairs"
          zeroBased
        />
        <RouteTable
          routes={m?.worst_routes ?? []}
          selected={routeFilter}
          onSelectRoute={setRouteFilter}
        />
      </section>

      <footer
        className="pb-2 text-[11px]"
        style={{ color: "var(--text-muted)" }}
      >
        Positions from {live.status?.url ?? "the configured GTFS-Realtime feed"}.
        Bunching is measured between live positions, not against a timetable —
        it reflects the gap riders actually experience.
        {live.config?.adaptive_bunching ? (
          <>
            {" "}
            Each route is judged against{" "}
            {Math.round((live.config.bunch_ratio ?? 0.3) * 100)}% of its own
            observed spacing, capped at {live.config.bunch_distance_max_m} m,
            with the global {live.config.bunch_distance_m} m used until a route
            has learned its own.
          </>
        ) : null}
      </footer>
    </main>
  );
}
