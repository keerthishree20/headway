"use client";

import type { LiveEvent } from "@/lib/types";

const SEVERITY: Record<
  LiveEvent["severity"],
  { token: string; icon: string; label: string }
> = {
  ok: { token: "var(--status-good)", icon: "✓", label: "Resolved" },
  warn: { token: "var(--status-warning)", icon: "▲", label: "Degraded" },
  alert: { token: "var(--status-critical)", icon: "●", label: "Alert" },
};

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * The event stream, with severe events pinned above it.
 *
 * The stream is capped and a busy peak fills it with routine bunching faster
 * than a rare fleet anomaly can survive in it -- the alert would scroll out of
 * existence within a minute of being raised. The server keeps those in a
 * second, smaller log, and they are shown here until they age out of it.
 */
export default function EventFeed({
  events,
  alerts,
  onSelectRoute,
}: {
  events: LiveEvent[];
  alerts: LiveEvent[];
  onSelectRoute: (routeId: string | null) => void;
}) {
  const pinned = alerts.slice(0, 3);
  return (
    <div
      className="flex h-full flex-col rounded-lg border"
      style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
    >
      <div
        className="flex items-baseline justify-between border-b px-4 py-3"
        style={{ borderColor: "var(--border)" }}
      >
        <h3 className="text-sm font-semibold">Event stream</h3>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {events.length} recent
        </span>
      </div>

      {pinned.length > 0 ? (
        <div
          className="border-b px-4 py-2.5"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-2)",
          }}
        >
          <div className="mb-1.5 flex items-baseline justify-between">
            <span
              className="text-[10px] font-semibold uppercase tracking-wide"
              style={{ color: "var(--status-critical)" }}
            >
              ● Held alerts
            </span>
            <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              {alerts.length > pinned.length
                ? `${pinned.length} of ${alerts.length} · kept out of the stream`
                : "kept out of the stream"}
            </span>
          </div>
          <ul className="space-y-1.5">
            {pinned.map((a, i) => (
              <li key={`${a.ts}-${a.kind}-${i}`} className="flex items-baseline gap-2">
                <span
                  className="tabular shrink-0 text-[10px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  {clock(a.ts)}
                </span>
                <span
                  className="text-xs leading-snug"
                  style={{ color: "var(--text-primary)" }}
                >
                  {a.message}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <ul className="flex-1 divide-y overflow-y-auto" style={{ borderColor: "var(--border)" }}>
        {events.length === 0 ? (
          <li className="px-4 py-6 text-xs" style={{ color: "var(--text-muted)" }}>
            No events yet. Service is running to headway.
          </li>
        ) : (
          events.map((e, i) => {
            const s = SEVERITY[e.severity];
            return (
              <li
                key={`${e.ts}-${e.kind}-${i}`}
                className="px-4 py-2.5"
                style={{ borderColor: "var(--border)" }}
              >
                <div className="flex items-start gap-2.5">
                  {/* Icon + label, never colour alone. */}
                  <span
                    aria-hidden
                    className="mt-0.5 text-[11px] leading-4"
                    style={{ color: s.token }}
                  >
                    {s.icon}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className="text-[10px] font-semibold uppercase tracking-wide"
                        style={{ color: s.token }}
                      >
                        {s.label}
                      </span>
                      <span
                        className="tabular text-[10px]"
                        style={{ color: "var(--text-muted)" }}
                      >
                        {clock(e.ts)}
                      </span>
                      {e.route_id ? (
                        <button
                          onClick={() => onSelectRoute(e.route_id)}
                          className="rounded px-1.5 py-0.5 text-[10px] font-medium hover:underline"
                          style={{
                            background: "var(--surface-2)",
                            color: "var(--text-secondary)",
                          }}
                        >
                          route {e.route_id}
                        </button>
                      ) : null}
                    </div>
                    <p
                      className="mt-0.5 text-xs leading-snug"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      {e.message}
                    </p>
                  </div>
                </div>
              </li>
            );
          })
        )}
      </ul>
    </div>
  );
}
