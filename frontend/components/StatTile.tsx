type Props = {
  label: string;
  value: string | number;
  unit?: string;
  hint?: string;
  tone?: "neutral" | "warning" | "critical" | "good";
};

const TONE: Record<NonNullable<Props["tone"]>, string> = {
  neutral: "var(--text-primary)",
  good: "var(--status-good)",
  warning: "var(--status-warning)",
  critical: "var(--status-critical)",
};

/** A headline number. No plot, so no hover layer -- the value is the message. */
export default function StatTile({
  label,
  value,
  unit,
  hint,
  tone = "neutral",
}: Props) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{
        background: "var(--surface-1)",
        borderColor: "var(--border)",
      }}
    >
      <div
        className="text-[11px] font-medium uppercase tracking-wider"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
      </div>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span
          className="tabular text-3xl font-semibold leading-none"
          style={{ color: TONE[tone] }}
        >
          {value}
        </span>
        {unit ? (
          <span className="text-sm" style={{ color: "var(--text-secondary)" }}>
            {unit}
          </span>
        ) : null}
      </div>
      {hint ? (
        <div className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}
