export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  const sign = bytes < 0 ? "-" : "";
  let value = Math.abs(bytes);
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${sign}${i === 0 ? value : value.toFixed(digits)} ${units[i]}`;
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const abs = Math.abs(value);
  const digits = abs === 0 ? 2 : abs < 0.01 ? 6 : abs < 1 ? 4 : 2;
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)}%`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US");
}

export const classLabel: Record<string, string> = {
  STANDARD: "Standard",
  STANDARD_IA: "Standard-IA",
  INTELLIGENT_TIERING: "Intelligent-Tiering",
  GLACIER_IR: "Glacier Instant",
  GLACIER: "Glacier Flexible",
  DEEP_ARCHIVE: "Deep Archive",
};
