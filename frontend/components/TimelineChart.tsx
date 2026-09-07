"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HistoryPoint } from "@/lib/types";
import { useThemeTokens } from "@/lib/useThemeTokens";

const TOKENS = [
  "--series-1",
  "--series-2",
  "--text-muted",
  "--text-secondary",
  "--border",
  "--surface-1",
] as const;

type Props = {
  title: string;
  subtitle: string;
  history: HistoryPoint[];
  metric: "active" | "bunched_pairs";
  unit: string;
  /** Area from a zero baseline, or a line on a fitted axis. See below. */
  zeroBased: boolean;
};

function clockLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * One measure per chart. Fleet size and bunched pairs live on different
 * scales, so they get two charts rather than two y-axes -- a dual axis lets
 * the reader infer a relationship the data does not contain.
 *
 * A single series needs no legend: the title names it.
 *
 * Fleet size varies by a few percent around ~650, so a zero-baseline area
 * would be a flat block. It gets a line on a fitted axis with the range
 * spelled out under the title -- a filled area on a truncated baseline is the
 * misleading combination, a labelled line is not.
 */
export default function TimelineChart({
  title,
  subtitle,
  history,
  metric,
  unit,
  zeroBased,
}: Props) {
  const t = useThemeTokens(TOKENS);
  const Chart = zeroBased ? AreaChart : LineChart;
  const stroke = metric === "active" ? t["--series-1"] : t["--series-2"];
  const gradientId = `grad-${metric}`;
  const latest = history.at(-1);

  return (
    <div
      className="rounded-lg border p-4"
      style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
    >
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        {latest ? (
          <span className="tabular text-sm" style={{ color: "var(--text-secondary)" }}>
            {latest[metric]} {unit}
          </span>
        ) : null}
      </div>
      <p className="mb-3 text-xs" style={{ color: "var(--text-muted)" }}>
        {subtitle}
        {!zeroBased && history.length > 1 ? (
          <>
            {" "}
            Axis is fitted to the observed range ({Math.min(...history.map((h) => h[metric]))}–
            {Math.max(...history.map((h) => h[metric]))}), not zero.
          </>
        ) : null}
      </p>
      <div className="h-40">
        {history.length < 2 ? (
          <div
            className="flex h-full items-center justify-center text-xs"
            style={{ color: "var(--text-muted)" }}
          >
            Collecting history…
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <Chart
              data={history}
              margin={{ top: 6, right: 10, bottom: 0, left: 0 }}
            >
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={stroke} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid
                stroke={t["--border"]}
                strokeDasharray="2 4"
                vertical={false}
              />
              <XAxis
                dataKey="ts"
                tickFormatter={clockLabel}
                tick={{ fontSize: 11, fill: t["--text-muted"] }}
                axisLine={{ stroke: t["--border"] }}
                tickLine={false}
                minTickGap={48}
              />
              <YAxis
                width={40}
                domain={zeroBased ? [0, "auto"] : ["dataMin - 2", "dataMax + 2"]}
                tick={{ fontSize: 11, fill: t["--text-muted"] }}
                axisLine={false}
                tickLine={false}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ stroke: t["--text-muted"], strokeDasharray: "3 3" }}
                labelFormatter={(ts) => clockLabel(Number(ts))}
                formatter={(value: number) => [`${value} ${unit}`, title]}
                contentStyle={{
                  background: t["--surface-1"],
                  border: `1px solid ${t["--border"]}`,
                  borderRadius: 8,
                  fontSize: 12,
                  color: t["--text-secondary"],
                }}
              />
              {zeroBased ? (
                <Area
                  type="monotone"
                  dataKey={metric}
                  stroke={stroke}
                  strokeWidth={2}
                  fill={`url(#${gradientId})`}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              ) : (
                <Line
                  type="monotone"
                  dataKey={metric}
                  stroke={stroke}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              )}
            </Chart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
