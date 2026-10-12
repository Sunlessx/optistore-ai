/** Shared chart styling: palette slots from CSS tokens, recessive grid and axes. */
export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

export const axisProps = {
  stroke: "var(--axis)",
  tick: { fill: "var(--muted)", fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: "var(--axis)" },
} as const;

export const gridProps = { stroke: "var(--grid)", strokeDasharray: "0", vertical: false } as const;
