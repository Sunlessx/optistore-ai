import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { DashboardPage } from "../pages/DashboardPage";
import { DuplicatesPage } from "../pages/DuplicatesPage";
import { PlansPage } from "../pages/PlansPage";

type Handler = (url: string, init?: RequestInit) => unknown;

function mockFetch(routes: Record<string, Handler | unknown>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const path = url.replace(/^\/api/, "");
      const key = Object.keys(routes).find((k) => {
        const [method, route] = k.includes(" ") ? k.split(" ") : ["GET", k];
        return (init?.method ?? "GET") === method && path.split("?")[0] === route;
      });
      if (!key) return new Response(JSON.stringify({ detail: `unmocked ${path}` }), { status: 404 });
      const value = routes[key];
      const body = typeof value === "function" ? (value as Handler)(url, init) : value;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

const dashboard = {
  has_data: true,
  locations: 1,
  total_files: 23,
  total_bytes: 4_153_794,
  by_category: { document: { files: 10, bytes: 100_000 }, image: { files: 3, bytes: 1_100_000 } },
  by_status: { analyzed: 22, error: 1 },
  duplicates: { groups: 3, redundant_copies: 3, redundant_bytes: 2219 },
  similar_pairs: 5,
  similarity_threshold: 0.75,
  compression: { files_with_recommendation: 4, files_benchmarked: 6, original_bytes: 1000, bytes_saved: 600, lossless_recommendations: 3, lossy_recommendations: 1 },
  consolidations: 0,
  estimated_monthly_storage_cost_usd: 0.0000891,
  cost_basis: "All files priced at S3 Standard",
  storage_class_simulation: { strategy: "balanced", objects: 0, current_cost: 0, projected_cost: 0, horizon_months: null },
  predictions: {},
  latest_plan: null,
  recent_scans: [],
};

describe("DashboardPage", () => {
  it("shows backend figures, not fabricated ones", async () => {
    mockFetch({ "/dashboard": dashboard, "/locations": [] });
    wrap(<DashboardPage />);
    expect(await screen.findByText("23")).toBeInTheDocument();
    expect(screen.getByText("2.2 KB")).toBeInTheDocument(); // verified duplicate bytes
    expect(screen.getByText("600 B")).toBeInTheDocument(); // measured compression saving
    expect(screen.getByText(/3 redundant copies in 3 groups/)).toBeInTheDocument();
  });

  it("shows an error state when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("network"))));
    wrap(<DashboardPage />);
    expect(await screen.findByText(/Cannot reach the OptiStore API/)).toBeInTheDocument();
  });
});

describe("DuplicatesPage", () => {
  it("renders an empty state when there are no duplicates", async () => {
    mockFetch({ "/duplicates/summary": { groups: 0, duplicate_files: 0, redundant_copies: 0, redundant_bytes: 0 }, "/duplicates": [], "/locations": [] });
    wrap(<DuplicatesPage />);
    expect(await screen.findByText("No exact duplicates found")).toBeInTheDocument();
  });
});

describe("PlansPage", () => {
  const action = {
    id: 7, plan_id: 1, priority: 1, action_type: "s3_change_storage_class", title: "Move cold.bin", reason: "cheaper",
    evidence: {}, target_file_ids: [1], params: {}, expected_bytes_saved: 0, expected_monthly_savings_usd: 0.5, estimated_cost_usd: 0.00002,
    risk_level: "high", risks: ["Restore takes hours"], reversible: false, reversibility_note: "Requires restore", executable: true,
    status: "proposed", result: {}, decided_at: null, executed_at: null,
  };
  const plan = { id: 1, location_id: null, strategy: "aggressive", status: "draft", created_at: "2026-10-11T10:00:00Z", summary: { inventory: { files: 1, bytes: 10 }, savings: {}, storage_classes: {}, predictions: {} }, actions: [action] };

  it("requires risk acknowledgement before approving a cloud action", async () => {
    const calls = mockFetch({
      "/plans": [plan],
      "/plans/1": plan,
      "/locations": [],
      "POST /plans/1/actions/7/decision": { ...action, status: "approved" },
    });
    wrap(<PlansPage />);
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByLabelText("I accept the listed risks"));
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/decision"))).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.url.endsWith("/decision"))!.init!.body));
    expect(body).toEqual({ approve: true, acknowledge_risks: true });
    expect(screen.getByRole("button", { name: /Execute approved/ })).toBeDisabled();
  });
});
