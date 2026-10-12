import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip, Legend } from "../components/charts";
import { axisProps, gridProps } from "../lib/chartTheme";
import { LocationSelect } from "../components/locations";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, Field, Input, PageHeader, Select, Spinner, StatTile, Table, Td, Th } from "../components/ui";
import { api, post, qs } from "../lib/api";
import { classLabel, formatBytes, formatUsd } from "../lib/format";

type Strategy = "conservative" | "balanced" | "aggressive";

interface SimRow {
  file_id: number;
  name: string;
  path: string;
  size_bytes: number;
  current_class: string;
  recommended_class: string;
  current_cost: number;
  projected_cost: number;
  saving: number;
  changed: boolean;
  basis: string;
  reliability: string;
  simulated_current_class: boolean;
  breakeven_months: number | null;
  risks: string[];
}
interface Simulation {
  strategy: string;
  strategy_description: string;
  horizon_months: number;
  region: string;
  totals: { current_cost: number; projected_cost: number; saving: number; objects_changed: number };
  rows: SimRow[];
  assumptions: Record<string, string | number>;
}

export function CostsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Cost optimisation simulator"
        description="Total-cost estimates per S3 storage class: storage (with minimum billable size and duration), requests, retrieval, transitions and monitoring. A cheaper storage price does not always mean a cheaper total."
      />
      <InventorySimulation />
      <WhatIf />
      <Forecast />
      <PricingAssumptions />
    </div>
  );
}

function InventorySimulation() {
  const [strategy, setStrategy] = useState<Strategy>("balanced");
  const [months, setMonths] = useState(12);
  const [locationId, setLocationId] = useState<number>();
  const sim = useMutation({ mutationFn: () => post<Simulation>("/costs/simulate", { strategy, horizon_months: months, location_id: locationId }) });
  const compare = useMutation({
    mutationFn: () => post<Record<Strategy, { totals: Simulation["totals"]; description: string }>>("/costs/compare", { horizon_months: months, location_id: locationId }),
  });
  const run = () => {
    sim.mutate();
    compare.mutate();
  };
  const d = sim.data;
  const compareData = compare.data ? (Object.entries(compare.data) as [Strategy, { totals: Simulation["totals"] }][]).map(([name, v]) => ({ name, projected: v.totals.projected_cost, current: v.totals.current_cost })) : [];

  return (
    <Card title="Simulate your inventory" description="Uses access predictions when available. Local files are costed as if they were in S3 Standard (simulation).">
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Strategy">
          <Select value={strategy} onChange={(e) => setStrategy(e.target.value as Strategy)}>
            <option value="conservative">Conservative</option>
            <option value="balanced">Balanced</option>
            <option value="aggressive">Aggressive</option>
          </Select>
        </Field>
        <Field label="Horizon (months)">
          <Input type="number" min={1} max={60} value={months} onChange={(e) => setMonths(Number(e.target.value))} className="w-28" />
        </Field>
        <Field label="Location">
          <LocationSelect value={locationId} onChange={setLocationId} />
        </Field>
        <Button variant="primary" onClick={run} loading={sim.isPending}>Run simulation</Button>
      </div>
      {sim.error && <ErrorState error={sim.error} />}
      {!d && !sim.isPending && <EmptyState title="No simulation yet" description="Choose a strategy and run the simulation over your analysed files." />}
      {sim.isPending && <Spinner label="Estimating costs…" />}
      {d && (
        <div className="space-y-4">
          <p className="text-xs text-ink-2">{d.strategy_description}</p>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label={`Current estimate (${d.horizon_months} mo)`} value={formatUsd(d.totals.current_cost)} />
            <StatTile label="Projected estimate" value={formatUsd(d.totals.projected_cost)} />
            <StatTile label="Estimated saving" value={formatUsd(d.totals.saving)} tone={d.totals.saving > 0 ? "good" : undefined} hint="Estimate, not measured" />
            <StatTile label="Objects changing class" value={`${d.totals.objects_changed} / ${d.rows.length}`} />
          </div>
          {d.totals.objects_changed === 0 && (
            <Alert>No class change pays off under this strategy. Typical reasons: objects below the 128 KB minimum billable size, transition request fees that exceed savings, frequent predicted reads, or unknown access patterns.</Alert>
          )}
          {compareData.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Projected cost by strategy</h3>
              <Legend items={[{ label: "Current", color: "var(--series-1)" }, { label: "Projected", color: "var(--series-3)" }]} />
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={compareData} barGap={2}>
                    <CartesianGrid {...gridProps} />
                    <XAxis dataKey="name" {...axisProps} />
                    <YAxis {...axisProps} tickFormatter={(v) => formatUsd(v)} width={80} />
                    <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => formatUsd(v)} />} />
                    <Bar isAnimationActive={false} dataKey="current" name="Current" fill="var(--series-1)" radius={[4, 4, 0, 0]} />
                    <Bar isAnimationActive={false} dataKey="projected" name="Projected" fill="var(--series-3)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
          <Table>
            <thead>
              <tr>
                <Th>Object</Th>
                <Th align="right">Size</Th>
                <Th>Class</Th>
                <Th align="right">Current</Th>
                <Th align="right">Projected</Th>
                <Th>Basis</Th>
                <Th>Risks</Th>
              </tr>
            </thead>
            <tbody>
              {d.rows.slice(0, 100).map((r) => (
                <tr key={r.file_id}>
                  <Td className="max-w-56"><div className="truncate" title={r.path}>{r.name}</div></Td>
                  <Td align="right">{formatBytes(r.size_bytes)}</Td>
                  <Td>
                    {classLabel[r.current_class] ?? r.current_class}
                    {r.simulated_current_class && <span className="text-muted"> (sim.)</span>}
                    {r.changed && <> → <Badge tone="accent">{classLabel[r.recommended_class]}</Badge></>}
                  </Td>
                  <Td align="right">{formatUsd(r.current_cost)}</Td>
                  <Td align="right">{formatUsd(r.projected_cost)}</Td>
                  <Td className="text-xs text-ink-2">{r.basis}<div><Badge tone={r.reliability === "ok" ? "good" : r.reliability === "low" ? "warning" : "neutral"}>prediction {r.reliability}</Badge></div></Td>
                  <Td className="max-w-xs text-xs text-ink-2">{r.risks.join(" ")}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </Card>
  );
}

interface WhatIfResult {
  options: { class: string; total: number; storage: number; requests: number; retrieval: number; transition: number; monitoring: number; latency: string; allowed_by_strategy: boolean }[];
  recommendation: { class: string; current_cost: number; projected_cost: number; saving: number; breakeven_months: number | null; risks: string[] };
  note: string;
}

function WhatIf() {
  const [form, setForm] = useState({ object_size_mb: 50, object_count: 10000, reads_per_object_per_month: 0.05, horizon_months: 12, strategy: "balanced" as Strategy, current_class: "STANDARD" });
  const calc = useMutation({ mutationFn: () => post<WhatIfResult>("/costs/what-if", form) });
  const set = (k: keyof typeof form, v: string) => setForm({ ...form, [k]: k === "strategy" || k === "current_class" ? v : Number(v) });
  const r = calc.data;
  return (
    <Card title="What-if calculator" description="Explore a hypothetical workload (independent of your inventory). Access frequency is treated as known.">
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Field label="Object size (MB)"><Input type="number" min={0.001} step="any" value={form.object_size_mb} onChange={(e) => set("object_size_mb", e.target.value)} /></Field>
        <Field label="Object count"><Input type="number" min={1} value={form.object_count} onChange={(e) => set("object_count", e.target.value)} /></Field>
        <Field label="Reads / object / month"><Input type="number" min={0} step="any" value={form.reads_per_object_per_month} onChange={(e) => set("reads_per_object_per_month", e.target.value)} /></Field>
        <Field label="Horizon (months)"><Input type="number" min={1} max={60} value={form.horizon_months} onChange={(e) => set("horizon_months", e.target.value)} /></Field>
        <Field label="Current class">
          <Select value={form.current_class} onChange={(e) => set("current_class", e.target.value)}>
            {Object.entries(classLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Strategy">
          <Select value={form.strategy} onChange={(e) => set("strategy", e.target.value)}>
            <option value="conservative">Conservative</option>
            <option value="balanced">Balanced</option>
            <option value="aggressive">Aggressive</option>
          </Select>
        </Field>
      </div>
      <Button variant="primary" onClick={() => calc.mutate()} loading={calc.isPending}>Calculate</Button>
      {calc.error && <div className="mt-3"><ErrorState error={calc.error} /></div>}
      {r && (
        <div className="mt-4 space-y-3">
          <Alert tone={r.recommendation.saving > 0 ? "good" : "info"} title={`Recommendation: ${classLabel[r.recommendation.class] ?? r.recommendation.class}`}>
            {formatUsd(r.recommendation.current_cost)} → {formatUsd(r.recommendation.projected_cost)} over {form.horizon_months} months (saving {formatUsd(r.recommendation.saving)}
            {r.recommendation.breakeven_months !== null && `, break-even after ${r.recommendation.breakeven_months} months`}). {r.recommendation.risks.join(" ")}
          </Alert>
          <Table>
            <thead>
              <tr>
                <Th>Class</Th>
                <Th align="right">Storage</Th>
                <Th align="right">Requests</Th>
                <Th align="right">Retrieval</Th>
                <Th align="right">Transition</Th>
                <Th align="right">Monitoring</Th>
                <Th align="right">Total</Th>
                <Th>First-byte latency</Th>
              </tr>
            </thead>
            <tbody>
              {r.options.map((o) => (
                <tr key={o.class} className={o.allowed_by_strategy ? undefined : "text-muted"}>
                  <Td>{classLabel[o.class] ?? o.class} {!o.allowed_by_strategy && <span className="text-xs">(not in strategy)</span>}</Td>
                  <Td align="right">{formatUsd(o.storage)}</Td>
                  <Td align="right">{formatUsd(o.requests)}</Td>
                  <Td align="right">{formatUsd(o.retrieval)}</Td>
                  <Td align="right">{formatUsd(o.transition)}</Td>
                  <Td align="right">{formatUsd(o.monitoring)}</Td>
                  <Td align="right" className="font-medium">{formatUsd(o.total)}</Td>
                  <Td className="text-xs">{o.latency}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="text-xs text-muted">{r.note}</p>
        </div>
      )}
    </Card>
  );
}

interface ForecastData {
  current_bytes: number;
  method: string;
  measured_history: { date: string; bytes: number }[];
  reconstructed_history: { month: string; bytes: number }[];
  projection: { month: string; bytes: number; low: number; high: number; monthly_cost_usd: number }[];
  scenarios: Record<string, { month: string; bytes: number; monthly_cost_usd: number }[]>;
  assumptions: string[];
}

function Forecast() {
  const [growth, setGrowth] = useState(5);
  const f = useQuery({ queryKey: ["forecast", growth], queryFn: () => api<ForecastData>(`/forecast${qs({ months: 12, growth_pct: growth })}`) });
  if (f.isLoading) return <Card title="Storage growth forecast"><Spinner /></Card>;
  if (f.error) return <Card title="Storage growth forecast"><ErrorState error={f.error} /></Card>;
  const d = f.data!;
  const scenarioKey = Object.keys(d.scenarios).find((k) => k !== "flat");
  const history = d.measured_history.length >= 3 ? d.measured_history.map((h) => ({ month: h.date, history: h.bytes })) : d.reconstructed_history.map((h) => ({ month: h.month, history: h.bytes }));
  const future = (d.projection.length ? d.projection : d.scenarios.flat).map((p, i) => ({
    month: p.month,
    projected: d.projection.length ? p.bytes : undefined,
    flat: d.scenarios.flat[i]?.bytes,
    scenario: scenarioKey ? d.scenarios[scenarioKey][i]?.bytes : undefined,
  }));
  const data = [...history, ...future];
  const regression = d.method === "linear_regression_on_measured_snapshots";
  return (
    <Card
      title="Storage growth forecast"
      description={regression ? "Linear trend fitted to measured scan snapshots." : "Not enough measured history for a statistical forecast — showing transparent scenarios."}
      actions={
        <Field label={`Scenario growth: ${growth}%/month`}>
          <Input type="range" min={0} max={30} step={1} value={growth} onChange={(e) => setGrowth(Number(e.target.value))} className="h-6 px-0" aria-label="Scenario growth" />
        </Field>
      }
    >
      {data.length ? (
        <>
          <Legend
            items={[
              { label: d.measured_history.length >= 3 ? "Measured" : "Reconstructed from file dates", color: "var(--series-1)" },
              ...(regression ? [{ label: "Projection", color: "var(--series-1)", dashed: true }] : []),
              { label: "Flat scenario", color: "var(--muted)", dashed: true },
              { label: `${growth}%/month scenario`, color: "var(--series-2)", dashed: true },
            ]}
          />
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data} margin={{ left: 8, right: 16 }}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="month" {...axisProps} minTickGap={24} />
                <YAxis {...axisProps} tickFormatter={(v) => formatBytes(v, 1)} width={70} />
                <Tooltip content={<ChartTooltip format={(v) => formatBytes(v)} />} cursor={{ stroke: "var(--axis)" }} />
                <Line isAnimationActive={false} dataKey="history" name="History" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }} connectNulls />
                {regression && <Line isAnimationActive={false} dataKey="projected" name="Projection" stroke="var(--series-1)" strokeWidth={2} strokeDasharray="5 4" dot={false} />}
                <Line isAnimationActive={false} dataKey="flat" name="Flat" stroke="var(--muted)" strokeWidth={2} strokeDasharray="3 3" dot={false} />
                <Line isAnimationActive={false} dataKey="scenario" name={`${growth}%/month`} stroke="var(--series-2)" strokeWidth={2} strokeDasharray="5 4" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      ) : (
        <EmptyState title="No storage data yet" />
      )}
      <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-ink-2">{d.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
    </Card>
  );
}

function PricingAssumptions() {
  const pricing = useQuery({ queryKey: ["pricing"], queryFn: () => api<{ source: string; as_of: string; _comment: string; regions: Record<string, Record<string, Record<string, number | string | boolean>>> }>("/costs/pricing") });
  if (!pricing.data) return null;
  const [region, classes] = Object.entries(pricing.data.regions)[0];
  return (
    <Card title={`Pricing assumptions (${region})`} description={`${pricing.data.as_of}. Source: ${pricing.data.source}`}>
      <Alert tone="warning">{pricing.data._comment}</Alert>
      <Table className="mt-3">
        <thead>
          <tr>
            <Th>Class</Th>
            <Th align="right">$/GB-month</Th>
            <Th align="right">GET /1k</Th>
            <Th align="right">Transition /1k</Th>
            <Th align="right">Retrieval $/GB</Th>
            <Th align="right">Min days</Th>
            <Th align="right">Min billable</Th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(classes).map(([cls, p]) => (
            <tr key={cls}>
              <Td>{classLabel[cls] ?? cls}</Td>
              <Td align="right">{String(p.storage_gb_month)}</Td>
              <Td align="right">{String(p.get_per_1000)}</Td>
              <Td align="right">{String(p.transition_per_1000)}</Td>
              <Td align="right">{String(p.retrieval_per_gb)}</Td>
              <Td align="right">{String(p.min_storage_days)}</Td>
              <Td align="right">{formatBytes(Number(p.min_billable_bytes))}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}
