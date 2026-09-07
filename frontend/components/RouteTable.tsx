"use client";

import type { RouteStat } from "@/lib/types";

/**
 * The table view: every number on the map and in the charts is also readable
 * as text, which is what makes the colour encoding optional rather than
 * load-bearing.
 */
export default function RouteTable({
  routes,
  selected,
  onSelectRoute,
}: {
  routes: RouteStat[];
  selected: string | null;
  onSelectRoute: (routeId: string | null) => void;
}) {
  return (
    <div
      className="rounded-lg border"
      style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
    >
      <div
        className="flex items-baseline justify-between border-b px-4 py-3"
        style={{ borderColor: "var(--border)" }}
      >
        <h3 className="text-sm font-semibold">Routes needing attention</h3>
        {selected ? (
          <button
            onClick={() => onSelectRoute(null)}
            className="text-xs hover:underline"
            style={{ color: "var(--text-secondary)" }}
          >
            clear filter
          </button>
        ) : null}
      </div>
      {routes.length === 0 ? (
        <p className="px-4 py-6 text-xs" style={{ color: "var(--text-muted)" }}>
          Every route is running to headway right now.
        </p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead>
            <tr style={{ color: "var(--text-muted)" }}>
              <th className="px-4 py-2 font-medium">Route</th>
              <th className="px-2 py-2 text-right font-medium">Vehicles</th>
              <th className="px-2 py-2 text-right font-medium">Bunched</th>
              <th className="px-2 py-2 text-right font-medium">Not reporting</th>
              <th
                className="px-4 py-2 text-right font-medium"
                title="Distance at which this route counts as bunched, learned from its own spacing"
              >
                Flags under
              </th>
            </tr>
          </thead>
          <tbody>
            {routes.map((r) => (
              <tr
                key={r.route_id}
                onClick={() =>
                  onSelectRoute(selected === r.route_id ? null : r.route_id)
                }
                className="cursor-pointer border-t"
                style={{
                  borderColor: "var(--border)",
                  background:
                    selected === r.route_id ? "var(--surface-2)" : "transparent",
                }}
              >
                <td className="px-4 py-2 font-medium">{r.route_id}</td>
                <td className="tabular px-2 py-2 text-right" style={{ color: "var(--text-secondary)" }}>
                  {r.vehicles}
                </td>
                <td
                  className="tabular px-2 py-2 text-right font-semibold"
                  style={{
                    color: r.bunched
                      ? "var(--status-critical)"
                      : "var(--text-muted)",
                  }}
                >
                  {r.bunched}
                </td>
                <td
                  className="tabular px-2 py-2 text-right font-semibold"
                  style={{
                    color: r.stale ? "var(--status-warning)" : "var(--text-muted)",
                  }}
                >
                  {r.stale}
                </td>
                {/* An adaptive threshold nobody can see is worse than a fixed
                    one: the number a pair was judged against belongs on screen
                    next to the count it produced. */}
                <td
                  className="tabular px-4 py-2 text-right"
                  style={{ color: "var(--text-secondary)" }}
                  title={
                    r.learned
                      ? `Learned from ${r.samples} observed gaps; this route normally runs ${Math.round(r.normal_spacing_m ?? 0)} m apart`
                      : "Not enough observed gaps yet — using the global default"
                  }
                >
                  {r.learned ? (
                    `${Math.round(r.threshold_m ?? 0)} m`
                  ) : (
                    <span style={{ color: "var(--text-muted)" }}>default</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
