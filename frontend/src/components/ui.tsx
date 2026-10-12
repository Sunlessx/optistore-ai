import { AlertTriangle, CheckCircle2, CircleAlert, Info, Loader2, OctagonAlert } from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "../lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-accent text-accent-ink hover:brightness-110",
        variant === "secondary" && "border border-line bg-surface text-ink hover:bg-surface-2",
        variant === "ghost" && "text-ink-2 hover:bg-surface-2 hover:text-ink",
        variant === "danger" && "bg-critical text-white hover:brightness-110",
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Card({ title, description, actions, children, className }: { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-xl border border-line bg-surface", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-ink-2">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function StatTile({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "good" }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-xs font-medium text-ink-2">{label}</div>
      <div className={cn("mt-1.5 text-2xl font-semibold tracking-tight", tone === "good" && "text-good-ink")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const badgeTones = {
  neutral: "bg-surface-2 text-ink-2",
  accent: "bg-accent/12 text-accent",
  good: "bg-good/12 text-good-ink",
  warning: "bg-warning/18 text-ink",
  serious: "bg-serious/18 text-ink",
  critical: "bg-critical/12 text-critical",
};

export function Badge({ tone = "neutral", children, className }: { tone?: keyof typeof badgeTones; children: ReactNode; className?: string }) {
  return <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-medium", badgeTones[tone], className)}>{children}</span>;
}

const statusMap: Record<string, { tone: keyof typeof badgeTones; icon: typeof Info }> = {
  completed: { tone: "good", icon: CheckCircle2 },
  analyzed: { tone: "good", icon: CheckCircle2 },
  executed: { tone: "good", icon: CheckCircle2 },
  success: { tone: "good", icon: CheckCircle2 },
  measured: { tone: "good", icon: CheckCircle2 },
  approved: { tone: "accent", icon: CheckCircle2 },
  acknowledged: { tone: "accent", icon: CheckCircle2 },
  running: { tone: "accent", icon: Loader2 },
  pending: { tone: "neutral", icon: Info },
  proposed: { tone: "neutral", icon: Info },
  draft: { tone: "neutral", icon: Info },
  reviewed: { tone: "accent", icon: CheckCircle2 },
  exported: { tone: "good", icon: CheckCircle2 },
  info: { tone: "neutral", icon: Info },
  dry_run: { tone: "neutral", icon: Info },
  recommended: { tone: "neutral", icon: Info },
  skipped: { tone: "warning", icon: AlertTriangle },
  cancelled: { tone: "warning", icon: AlertTriangle },
  rejected: { tone: "warning", icon: AlertTriangle },
  partially_executed: { tone: "accent", icon: Info },
  missing: { tone: "warning", icon: AlertTriangle },
  low: { tone: "good", icon: CheckCircle2 },
  medium: { tone: "warning", icon: AlertTriangle },
  high: { tone: "critical", icon: OctagonAlert },
  error: { tone: "critical", icon: CircleAlert },
  failed: { tone: "critical", icon: CircleAlert },
  failure: { tone: "critical", icon: CircleAlert },
};

/** Status always carries an icon + label, never colour alone. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const entry = statusMap[status] ?? { tone: "neutral" as const, icon: Info };
  const Icon = entry.icon;
  return (
    <Badge tone={entry.tone}>
      <Icon className={cn("size-3", status === "running" && "animate-spin")} aria-hidden />
      {label ?? status.replace(/_/g, " ")}
    </Badge>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-2" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <div className="text-sm font-medium">{title}</div>
      {description && <div className="max-w-md text-sm text-ink-2">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Alert({ tone = "info", title, children }: { tone?: "info" | "warning" | "critical" | "good"; title?: string; children: ReactNode }) {
  const Icon = tone === "critical" ? CircleAlert : tone === "warning" ? AlertTriangle : tone === "good" ? CheckCircle2 : Info;
  return (
    <div
      role={tone === "critical" ? "alert" : undefined}
      className={cn(
        "flex gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm",
        tone === "info" && "border-accent/30 bg-accent/6",
        tone === "warning" && "border-warning/50 bg-warning/10",
        tone === "critical" && "border-critical/40 bg-critical/8",
        tone === "good" && "border-good/40 bg-good/8",
      )}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", tone === "critical" ? "text-critical" : tone === "good" ? "text-good-ink" : tone === "warning" ? "text-ink" : "text-accent")} aria-hidden />
      <div className="min-w-0">
        {title && <div className="font-medium">{title}</div>}
        <div className="text-ink-2">{children}</div>
      </div>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <Alert tone="critical" title="Something went wrong">
      <span>{message}</span>
      {onRetry && (
        <Button size="sm" variant="secondary" className="ml-2" onClick={onRetry}>
          Retry
        </Button>
      )}
    </Alert>
  );
}

export function Progress({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max}>
      <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

const inputClass = "h-9 rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-muted focus:outline-2 focus:outline-accent";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(inputClass, className)} {...props} />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(inputClass, "pr-8", className)} {...props}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn("rounded-lg border border-line bg-surface p-3 font-mono text-xs leading-relaxed text-ink focus:outline-2 focus:outline-accent", className)} {...props} />;
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, className, align = "left" }: { children?: ReactNode; className?: string; align?: "left" | "right" }) {
  return <th className={cn("border-b border-line px-3 py-2 text-xs font-medium whitespace-nowrap text-ink-2", align === "right" ? "text-right" : "text-left", className)}>{children}</th>;
}

export function Td({ children, className, align = "left", title }: { children?: ReactNode; className?: string; align?: "left" | "right"; title?: string }) {
  return (
    <td title={title} className={cn("border-b border-line px-3 py-2 align-top", align === "right" && "tabular text-right", className)}>
      {children}
    </td>
  );
}

export function Tabs<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5" role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn("rounded-md px-3 py-1.5 text-xs font-medium transition-colors", value === o.value ? "bg-surface text-ink shadow-sm" : "text-ink-2 hover:text-ink")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function DemoLabel({ children = "Synthetic demo data" }: { children?: ReactNode }) {
  return (
    <Badge tone="warning">
      <AlertTriangle className="size-3" aria-hidden />
      {children}
    </Badge>
  );
}
