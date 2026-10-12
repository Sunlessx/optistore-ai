import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip, Legend } from "../components/charts";
import { axisProps, gridProps } from "../lib/chartTheme";
import { LocationSelect } from "../components/locations";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, PageHeader, Spinner, StatTile, StatusBadge, Table, Td, Th } from "../components/ui";
import { api, post, qs } from "../lib/api";
import { formatBytes, formatPct } from "../lib/format";
import type { CompressionResult } from "../lib/types";

interface Summary {
  files_with_recommendation: number;
  lossless_recommendations: number;
  lossy_recommendations: number;
  original_bytes: number;
  output_bytes: number;
  bytes_saved: number;
  files_benchmarked: number;
}

export function CompressionPage() {
  const qc = useQueryClient();
  const [locationId, setLocationId] = useState<number>();
  const [bestOnly, setBestOnly] = useState(true);
  const summary = useQuery({ queryKey: ["compression-summary"], queryFn: () => api<Summary>("/compression/summary") });
  const results = useQuery({ queryKey: ["compression", bestOnly], queryFn: () => api<CompressionResult[]>(`/compression/results${qs({ best_only: bestOnly })}`) });
  const batch = useMutation({
    mutationFn: () => post<{ files_analyzed: number }>("/compression/batch", { location_id: locationId, limit: 100 }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compression"] });
      qc.invalidateQueries({ queryKey: ["compression-summary"] });
    },
  });
  const s = summary.data;
  const best = (results.data ?? []).filter((r) => r.is_best).sort((a, b) => b.original_bytes - (b.output_bytes ?? 0) - (a.original_bytes - (a.output_bytes ?? 0)));
  const chartData = best.slice(0, 8).map((r) => ({ name: r.file_name, original: r.original_bytes, compressed: r.output_bytes ?? 0 }));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Compression analysis"
        description="Each method is actually run on the file and measured. Lossless results are round-trip verified; lossy image results report PSNR. Originals are never overwritten — approved copies go to a separate outputs folder via an optimisation plan."
        actions={
          <>
            <LocationSelect value={locationId} onChange={setLocationId} />
            <Button variant="primary" onClick={() => batch.mutate()} loading={batch.isPending}>Benchmark up to 100 files</Button>
          </>
        }
      />
      {batch.data && <Alert tone="good">Benchmarked {batch.data.files_analyzed} files.</Alert>}
      {batch.error && <ErrorState error={batch.error} />}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Files benchmarked" value={s?.files_benchmarked ?? "—"} />
        <StatTile label="Worthwhile recommendations" value={s?.files_with_recommendation ?? "—"} hint={s ? `${s.lossless_recommendations} lossless · ${s.lossy_recommendations} lossy` : undefined} />
        <StatTile label="Measured bytes saved" value={formatBytes(s?.bytes_saved)} tone="good" hint={s?.original_bytes ? `${formatPct((100 * s.bytes_saved) / s.original_bytes)} of ${formatBytes(s.original_bytes)}` : undefined} />
        <StatTile label="Of which lossless" value={s ? `${s.lossless_recommendations}/${s.files_with_recommendation}` : "—"} hint="Lossy outputs need explicit risk acknowledgement" />
      </div>
      {chartData.length > 0 && (
        <Card title="Largest measured savings" description="Original vs best-method output size (bytes), top 8 files">
          <Legend items={[{ label: "Original", color: "var(--series-1)" }, { label: "Best compressed output", color: "var(--series-3)" }]} />
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ left: 8, right: 8 }} barGap={2}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="name" {...axisProps} interval={0} tickFormatter={(v: string) => (v.length > 14 ? `${v.slice(0, 13)}…` : v)} />
                <YAxis {...axisProps} tickFormatter={(v) => formatBytes(v, 1)} width={64} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => formatBytes(v)} />} />
                <Bar isAnimationActive={false} dataKey="original" name="Original" fill="var(--series-1)" radius={[4, 4, 0, 0]} />
                <Bar isAnimationActive={false} dataKey="compressed" name="Compressed" fill="var(--series-3)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}
      <Card
        title="Measurements"
        actions={
          <label className="flex items-center gap-2 text-sm text-ink-2">
            <input type="checkbox" checked={bestOnly} onChange={(e) => setBestOnly(e.target.checked)} /> Best method per file only
          </label>
        }
      >
        {results.isLoading ? (
          <Spinner />
        ) : results.error ? (
          <ErrorState error={results.error} />
        ) : !results.data?.length ? (
          <EmptyState title="No measurements yet" description="Run a benchmark, or open a file in the File explorer and benchmark it individually." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>File</Th>
                <Th>Method</Th>
                <Th>Type</Th>
                <Th align="right">Original</Th>
                <Th align="right">Output</Th>
                <Th align="right">Saved</Th>
                <Th align="right">Ratio</Th>
                <Th align="right">Time</Th>
                <Th>Quality / restore</Th>
              </tr>
            </thead>
            <tbody>
              {results.data.map((r) => (
                <tr key={r.id}>
                  <Td className="max-w-48"><div className="truncate font-medium" title={r.file_path}>{r.file_name}</div>{r.is_best && <Badge tone="accent">best</Badge>}</Td>
                  <Td className="font-mono text-xs">{r.method}</Td>
                  <Td>{r.status === "measured" ? <Badge tone={r.lossless ? "good" : "warning"}>{r.lossless ? "lossless" : "lossy"}</Badge> : <StatusBadge status={r.status} />}</Td>
                  <Td align="right">{formatBytes(r.original_bytes)}</Td>
                  <Td align="right">{formatBytes(r.output_bytes)}</Td>
                  <Td align="right">{formatPct(r.percent_saved)}</Td>
                  <Td align="right">{r.ratio ? `${r.ratio.toFixed(2)}×` : "—"}</Td>
                  <Td align="right">{r.duration_ms !== null ? `${r.duration_ms.toFixed(0)} ms` : "—"}</Td>
                  <Td className="max-w-sm text-xs text-ink-2">{r.quality_note}{r.restore_method && <div className="text-muted">Restore: {r.restore_method}</div>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
