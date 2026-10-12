import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip } from "../components/charts";
import { axisProps, gridProps } from "../lib/chartTheme";
import { LocationsPanel } from "../components/locations";
import { Badge, Card, ErrorState, PageHeader, Spinner, StatTile, StatusBadge, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { formatBytes, formatDate, formatNumber, formatPct, formatUsd } from "../lib/format";

interface Dashboard {
  has_data: boolean;
  locations: number;
  total_files: number;
  total_bytes: number;
  by_category: Record<string, { files: number; bytes: number }>;
  by_status: Record<string, number>;
  duplicates: { groups: number; redundant_copies: number; redundant_bytes: number };
  similar_pairs: number;
  similarity_threshold: number;
  compression: { files_with_recommendation: number; files_benchmarked: number; original_bytes: number; bytes_saved: number; lossless_recommendations: number; lossy_recommendations: number };
  consolidations: number;
  estimated_monthly_storage_cost_usd: number;
  cost_basis: string;
  storage_class_simulation: { strategy: string; objects: number; current_cost: number; projected_cost: number; horizon_months: number | null };
  predictions: Record<string, number>;
  latest_plan: { id: number; strategy: string; status: string; actions: number; created_at: string } | null;
  recent_scans: { id: number; location_id: number; status: string; processed: number; finished_at: string | null }[];
}

export function DashboardPage() {
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/dashboard") });
  if (isLoading) return <Spinner />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (!data) return null;

  const categories = Object.entries(data.by_category)
    .map(([category, v]) => ({ category, bytes: v.bytes, files: v.files }))
    .sort((a, b) => b.bytes - a.bytes);
  const sim = data.storage_class_simulation;
  const compressionPct = data.compression.original_bytes ? (100 * data.compression.bytes_saved) / data.compression.original_bytes : null;

  return (
    <div className="space-y-6">
      <PageHeader title="Overview" description="Live figures from your analysed locations. Every number here comes from the backend; savings are labelled measured, verified or estimated." />
      {!data.has_data && <LocationsPanel />}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Files analysed" value={formatNumber(data.total_files)} hint={`${data.locations} location(s) · ${data.by_status.error ?? 0} with errors`} />
        <StatTile label="Total storage" value={formatBytes(data.total_bytes)} hint={`≈ ${formatUsd(data.estimated_monthly_storage_cost_usd)}/month at S3 Standard`} />
        <StatTile
          label="Verified duplicate storage"
          value={formatBytes(data.duplicates.redundant_bytes)}
          hint={`${data.duplicates.redundant_copies} redundant copies in ${data.duplicates.groups} groups (SHA-256)`}
          tone={data.duplicates.redundant_bytes ? "good" : undefined}
        />
        <StatTile
          label="Measured compression saving"
          value={formatBytes(data.compression.bytes_saved)}
          hint={`${data.compression.files_with_recommendation} files · ${compressionPct === null ? "not benchmarked yet" : `${formatPct(compressionPct)} of benchmarked bytes`}`}
          tone={data.compression.bytes_saved ? "good" : undefined}
        />
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Storage by category" description="Bytes per file category (hover for file counts)" className="lg:col-span-2">
          {categories.length ? (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={categories} layout="vertical" margin={{ left: 8, right: 24 }}>
                  <CartesianGrid {...gridProps} horizontal={false} vertical />
                  <XAxis type="number" {...axisProps} tickFormatter={(v) => formatBytes(v, 1)} />
                  <YAxis type="category" dataKey="category" {...axisProps} width={80} />
                  <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => formatBytes(v)} />} />
                  <Bar isAnimationActive={false} dataKey="bytes" name="Storage" fill="var(--series-1)" radius={[0, 4, 4, 0]} barSize={18} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="py-10 text-center text-sm text-ink-2">No files analysed yet.</div>
          )}
        </Card>
        <Card title="Optimisation opportunities">
          <ul className="space-y-3 text-sm">
            <li className="flex justify-between gap-2">
              <Link className="text-accent hover:underline" to="/compare">
                Similar document pairs
              </Link>
              <span className="tabular font-medium">{data.similar_pairs}</span>
            </li>
            <li className="text-xs text-muted">Cosine ≥ {data.similarity_threshold}; candidates for review, not proof of equivalence.</li>
            <li className="flex justify-between gap-2">
              <Link className="text-accent hover:underline" to="/consolidation">
                Consolidation drafts
              </Link>
              <span className="tabular font-medium">{data.consolidations}</span>
            </li>
            <li className="flex justify-between gap-2">
              <Link className="text-accent hover:underline" to="/compression">
                Compression recommendations
              </Link>
              <span className="tabular font-medium">
                {data.compression.lossless_recommendations} lossless · {data.compression.lossy_recommendations} lossy
              </span>
            </li>
            <li className="flex justify-between gap-2">
              <Link className="text-accent hover:underline" to="/costs">
                Storage-class simulation ({sim.strategy})
              </Link>
              <span className="tabular font-medium">{sim.objects ? formatUsd(sim.current_cost - sim.projected_cost) : "not run"}</span>
            </li>
            {sim.objects > 0 && <li className="text-xs text-muted">Estimated saving over {sim.horizon_months} months (approximate list prices).</li>}
            <li className="flex justify-between gap-2">
              <Link className="text-accent hover:underline" to="/predictions">
                Access predictions
              </Link>
              <span className="flex gap-1">
                {Object.keys(data.predictions).length ? (
                  Object.entries(data.predictions).map(([k, v]) => (
                    <Badge key={k} tone={k === "ok" ? "good" : k === "low" ? "warning" : "neutral"}>
                      {v} {k}
                    </Badge>
                  ))
                ) : (
                  <span className="text-xs text-muted">none</span>
                )}
              </span>
            </li>
          </ul>
        </Card>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Latest optimisation plan" actions={<Link className="text-xs text-accent hover:underline" to="/plans">Open plans</Link>}>
          {data.latest_plan ? (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-medium">Plan #{data.latest_plan.id}</span>
              <Badge>{data.latest_plan.strategy}</Badge>
              <StatusBadge status={data.latest_plan.status} />
              <span className="text-ink-2">{data.latest_plan.actions} actions</span>
              <span className="text-xs text-muted">{formatDate(data.latest_plan.created_at)}</span>
            </div>
          ) : (
            <p className="text-sm text-ink-2">No plan yet. Generate one on the Optimisation plans page.</p>
          )}
        </Card>
        <Card title="Recent scans">
          {data.recent_scans.length ? (
            <Table>
              <thead>
                <tr>
                  <Th>Scan</Th>
                  <Th>Status</Th>
                  <Th align="right">Files</Th>
                  <Th>Finished</Th>
                </tr>
              </thead>
              <tbody>
                {data.recent_scans.map((s) => (
                  <tr key={s.id}>
                    <Td>#{s.id}</Td>
                    <Td>
                      <StatusBadge status={s.status} />
                    </Td>
                    <Td align="right">{s.processed}</Td>
                    <Td className="text-xs text-ink-2">{formatDate(s.finished_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <p className="text-sm text-ink-2">No scans yet.</p>
          )}
        </Card>
      </div>
      {data.has_data && <LocationsPanel />}
      <p className="text-xs text-muted">{data.cost_basis}</p>
    </div>
  );
}
