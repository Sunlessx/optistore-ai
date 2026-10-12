import { useQuery } from "@tanstack/react-query";
import {
  Archive,
  Cloud,
  Copy,
  FileStack,
  FolderSearch,
  GitCompareArrows,
  History,
  LayoutDashboard,
  ListChecks,
  Menu,
  Moon,
  Search,
  Settings,
  Sun,
  TrendingUp,
  Wallet,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../lib/api";
import type { EmbeddingStatus } from "../lib/types";
import { Badge } from "./ui";
import { cn } from "../lib/cn";

const NAV: { to: string; label: string; icon: typeof LayoutDashboard; group: string }[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, group: "Analyse" },
  { to: "/files", label: "File explorer", icon: FolderSearch, group: "Analyse" },
  { to: "/duplicates", label: "Duplicates", icon: Copy, group: "Analyse" },
  { to: "/compare", label: "Semantic comparison", icon: GitCompareArrows, group: "Analyse" },
  { to: "/search", label: "Semantic search", icon: Search, group: "Analyse" },
  { to: "/consolidation", label: "Consolidation", icon: FileStack, group: "Optimise" },
  { to: "/compression", label: "Compression", icon: Archive, group: "Optimise" },
  { to: "/costs", label: "Cost simulator", icon: Wallet, group: "Optimise" },
  { to: "/predictions", label: "Access prediction", icon: TrendingUp, group: "Optimise" },
  { to: "/plans", label: "Optimisation plans", icon: ListChecks, group: "Optimise" },
  { to: "/aws", label: "AWS S3", icon: Cloud, group: "Govern" },
  { to: "/history", label: "Operation history", icon: History, group: "Govern" },
  { to: "/settings", label: "Settings", icon: Settings, group: "Govern" },
];

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    try {
      const stored = localStorage.getItem("optistore.theme");
      if (stored === "light" || stored === "dark") return stored;
    } catch {
      /* ignore */
    }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("optistore.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, setTheme] as const;
}

export function Layout({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api<{ status: string; embedding: EmbeddingStatus }>("/health"),
    refetchInterval: 30_000,
  });
  const groups = [...new Set(NAV.map((n) => n.group))];
  const embedding = health.data?.embedding;

  const sidebar = (
    <nav className="flex h-full flex-col gap-5 overflow-y-auto px-3 py-4" aria-label="Main">
      <div className="flex items-center gap-2 px-2">
        <img src="/favicon.svg" alt="" className="size-7" />
        <div>
          <div className="text-sm font-semibold">OptiStore AI</div>
          <div className="text-[11px] text-muted">Storage intelligence</div>
        </div>
      </div>
      {groups.map((group) => (
        <div key={group}>
          <div className="px-2 pb-1 text-[11px] font-medium tracking-wide text-muted uppercase">{group}</div>
          {NAV.filter((n) => n.group === group).map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn("flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm", isActive ? "bg-accent/10 font-medium text-accent" : "text-ink-2 hover:bg-surface-2 hover:text-ink")
              }
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </div>
      ))}
      <div className="mt-auto space-y-2 px-2 text-xs text-ink-2">
        <div className="flex items-center gap-1.5">
          <span className={cn("size-2 rounded-full", health.isSuccess ? "bg-good" : health.isError ? "bg-critical" : "bg-muted")} aria-hidden />
          API {health.isSuccess ? "connected" : health.isError ? "unreachable" : "checking…"}
        </div>
        {embedding?.active_backend && (
          <div title={embedding.active_model ?? ""}>
            Embeddings: {embedding.active_backend === "hashing" ? <Badge tone="warning">lexical fallback</Badge> : <Badge tone="good">semantic</Badge>}
          </div>
        )}
        {embedding && !embedding.active_backend && <div className="text-muted">Embedding model loads on first scan.</div>}
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r border-line bg-surface lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 w-64 bg-surface shadow-xl">{sidebar}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center justify-between border-b border-line bg-surface/90 px-4 backdrop-blur">
          <button className="rounded-md p-1.5 text-ink-2 hover:bg-surface-2 lg:hidden" onClick={() => setOpen(!open)} aria-label="Toggle navigation">
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
          <div className="text-xs text-muted">Originals are never modified · cloud writes require approval</div>
          <button
            className="rounded-md p-1.5 text-ink-2 hover:bg-surface-2"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
