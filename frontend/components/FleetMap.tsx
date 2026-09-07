"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  CircleMarker,
  MapContainer,
  Popup,
  TileLayer,
  useMapEvents,
} from "react-leaflet";
import type { Map as LeafletMap } from "leaflet";
import type { FeedConfig, Metrics, Vehicle } from "@/lib/types";
import { useIsDark } from "@/lib/useIsDark";
import { useThemeTokens } from "@/lib/useThemeTokens";

const TOKENS = ["--status-critical", "--status-warning", "--series-1"] as const;

type Props = {
  vehicles: Vehicle[];
  metrics: Metrics | null;
  config: FeedConfig | null;
  routeFilter: string | null;
  onViewportChange?: (bbox: [number, number, number, number]) => void;
};

/**
 * Reports the visible bounds so the server can stop sending vehicles that are
 * off screen.
 *
 * Panning fires continuously, so the report is debounced -- otherwise a single
 * drag would send dozens of subscribe messages and the server would answer
 * each one with a fresh replay. The bounds are padded by half a screen so a
 * small nudge does not blank the edges before the next message lands.
 */
function ViewportReporter({
  onChange,
}: {
  onChange: (bbox: [number, number, number, number]) => void;
}) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const report = (map: LeafletMap) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const b = map.getBounds().pad(0.5);
      onChange([b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]);
    }, 400);
  };

  const map = useMapEvents({
    moveend: () => report(map),
    zoomend: () => report(map),
  });

  useEffect(() => {
    report(map);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  return null;
}

/**
 * Vehicle state is encoded twice -- colour AND size/ring -- so the map stays
 * readable for a colourblind viewer and in print. The legend labels every
 * state; colour never carries meaning on its own.
 */
const STATE_STYLE = {
  bunched: { token: "--status-critical", radius: 7, weight: 2, fill: 0.85 },
  stale: { token: "--status-warning", radius: 5, weight: 2, fill: 0.15 },
  normal: { token: "--series-1", radius: 3.5, weight: 1, fill: 0.7 },
} as const;

function ageLabel(ts: number | null): string {
  if (!ts) return "unknown";
  const age = Date.now() / 1000 - ts;
  return age < 60 ? `${age.toFixed(0)}s ago` : `${(age / 60).toFixed(1)} min ago`;
}

export default function FleetMap({
  vehicles,
  metrics,
  config,
  routeFilter,
  onViewportChange,
}: Props) {
  const colors = useThemeTokens(TOKENS);
  const dark = useIsDark();

  const bunchedIds = useMemo(() => {
    const ids = new Set<string>();
    metrics?.bunching.forEach((p) => p.vehicles.forEach((v) => ids.add(v)));
    return ids;
  }, [metrics]);

  const staleIds = useMemo(
    () => new Set(metrics?.stale_vehicles.map((v) => v.id) ?? []),
    [metrics],
  );

  const shown = useMemo(
    () =>
      routeFilter
        ? vehicles.filter((v) => v.route_id === routeFilter)
        : vehicles,
    [vehicles, routeFilter],
  );

  const center = config?.map_center ?? [42.3555, -71.0605];

  return (
    <MapContainer
      center={center}
      zoom={config?.map_zoom ?? 12}
      preferCanvas
      scrollWheelZoom
      className="h-full w-full"
    >
      {/* A muted grey basemap so the vehicle marks carry the colour, not the
          map. Esri's Canvas tiles are keyless and ship a real dark set --
          CARTO's now demand an API key, and CSS-inverting street tiles turns
          roads pink and water teal. */}
      <TileLayer
        key={dark ? "dark" : "light"}
        attribution='Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors'
        url={
          dark
            ? "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
            : "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
        }
        maxZoom={16}
      />
      {onViewportChange ? <ViewportReporter onChange={onViewportChange} /> : null}
      {shown.map((v) => {
        const state = bunchedIds.has(v.id)
          ? "bunched"
          : staleIds.has(v.id)
            ? "stale"
            : "normal";
        const style = STATE_STYLE[state];
        const color = colors[style.token];
        return (
          <CircleMarker
            key={v.id}
            center={[v.lat, v.lon]}
            radius={style.radius}
            pathOptions={{
              color,
              weight: style.weight,
              fillColor: color,
              fillOpacity: style.fill,
            }}
          >
            <Popup>
              <div className="text-xs leading-relaxed">
                <div className="font-semibold">
                  Route {v.route_id ?? "—"} · vehicle {v.label ?? v.id}
                </div>
                <div style={{ color: "var(--text-secondary)" }}>
                  State:{" "}
                  <strong>
                    {state === "bunched"
                      ? "bunched"
                      : state === "stale"
                        ? "not reporting"
                        : "in service"}
                  </strong>
                  <br />
                  Direction: {v.direction_id}
                  <br />
                  Speed:{" "}
                  {v.speed === null ? "not reported" : `${v.speed.toFixed(1)} m/s`}
                  <br />
                  Last position: {ageLabel(v.ts)}
                  {v.occupancy ? (
                    <>
                      <br />
                      Occupancy: {v.occupancy.replace(/_/g, " ")}
                    </>
                  ) : null}
                </div>
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}
