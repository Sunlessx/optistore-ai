import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip, Legend } from "../components/charts";
import { SERIES, axisProps, gridProps } from "../lib/chartTheme";
import { LocationSelect } from "../components/locations";
import { Alert, Badge, Button, Card, DemoLabel, EmptyState, ErrorState, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api, post, qs } from "../lib/api";
import { formatDate } from "../lib/format";
import type { Evaluation, FileRecord, Page, Prediction } from "../lib/types";

const MODEL_LABELS: Record<string, string> = {
  heuristic_baseline: "Heuristic baseline",
  logistic_regression: "Logistic regression",
  random_forest: "Random forest",
};
const METRICS = ["roc_auc", "precision", "recall", "f1", "accuracy"];

export function PredictionsPage() {
  const qc = useQueryClient();
  const [dataset, setDataset] = useState<"synthetic" | "observed">("synthetic");
  const [nFiles, setNFiles] = useState(1500);
  const [horizon, setHorizon] = useState(30);
  const [seed, setSeed] = useState(42);
  const evaluations = useQuery({ queryKey: ["evaluations"], queryFn: () => api<Evaluation[]>("/ml/evaluations") });
  const status = useQuery({ queryKey: ["ml-status"], queryFn: () => api<{ active_evaluation_id: number | null; active_dataset_kind: string | null; access_events_by_source: Record<string, number>; observed_requirements: { min_events: number; min_files: number } }>("/ml/status") });
  const train = useMutation({
    mutationFn: () => post<Evaluation>("/ml/train", { dataset, n_files: nFiles, horizon_days: horizon, seed }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evaluations"] });
      qc.invalidateQueries({ queryKey: ["ml-status"] });
    },
  });
  const latest = evaluations.data?.[0];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Access prediction"
        description="A scikit-learn classifier predicting whether a file will be accessed in the next N days, compared with a simple recency heuristic. Predictions inform recommendations; they never move or delete anything by themselves."
      />
      <Card title="Train & evaluate" description="Chronological split: models train on earlier snapshots and are tested on a later period whose labels never overlap training labels.">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Dataset">
            <Select value={dataset} onChange={(e) => setDataset(e.target.value as "synthetic" | "observed")}>
              <option value="synthetic">Synthetic (generated, labelled)</option>
              <option value="observed">Observed (imported access logs)</option>
            </Select>
          </Field>
          {dataset === "synthetic" && (
            <>
              <Field label="Simulated files"><Input type="number" min={200} max={20000} value={nFiles} onChange={(e) => setNFiles(Number(e.target.value))} className="w-28" /></Field>
              <Field label="Seed"><Input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} className="w-24" /></Field>
            </>
          )}
          <Field label="Horizon (days)"><Input type="number" min={7} max={180} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} className="w-24" /></Field>
          <Button variant="primary" onClick={() => train.mutate()} loading={train.isPending}>Train and evaluate</Button>
        </div>
        {dataset === "observed" && status.data && (
          <p className="mt-2 text-xs text-ink-2">
            Requires ≥ {status.data.observed_requirements.min_events} imported events across ≥ {status.data.observed_requirements.min_files} files and enough history for the horizon.
          </p>
        )}
        {train.error && <div className="mt-3"><ErrorState error={train.error} /></div>}
      </Card>

      {evaluations.isLoading ? (
        <Spinner />
      ) : latest ? (
        <EvaluationView evaluation={latest} />
      ) : (
        <EmptyState title="No model trained yet" description="Train on the synthetic dataset to see the methodology end to end." />
      )}

      <AccessHistory status={status.data?.access_events_by_source ?? {}} />
      <PredictionTable />

      {evaluations.data && evaluations.data.length > 1 && (
        <Card title="Evaluation history">
          <Table>
            <thead>
              <tr><Th>#</Th><Th>Dataset</Th><Th>Horizon</Th><Th>Selected</Th><Th align="right">ROC AUC</Th><Th align="right">Baseline AUC</Th><Th>Date</Th></tr>
            </thead>
            <tbody>
              {evaluations.data.map((e) => (
                <tr key={e.id}>
                  <Td>{e.id}</Td>
                  <Td>{e.dataset_kind === "synthetic" ? <DemoLabel>synthetic</DemoLabel> : <Badge tone="good">observed</Badge>}</Td>
                  <Td>{e.horizon_days} d</Td>
                  <Td>{MODEL_LABELS[e.selected_model]}</Td>
                  <Td align="right">{e.metrics[e.selected_model]?.roc_auc?.toFixed(3)}</Td>
                  <Td align="right">{e.metrics.heuristic_baseline?.roc_auc?.toFixed(3)}</Td>
                  <Td className="text-xs text-ink-2">{formatDate(e.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}

function EvaluationView({ evaluation: e }: { evaluation: Evaluation }) {
  const models = Object.keys(e.metrics);
  const chartData = METRICS.filter((m) => m !== "accuracy").map((metric) => ({
    metric: metric.replace("_", " ").toUpperCase(),
    ...Object.fromEntries(models.map((model) => [model, e.metrics[model][metric] ?? 0])),
  }));
  const importance = (e.params.rf_feature_importance ?? {}) as Record<string, number>;
  return (
    <Card
      title={`Evaluation #${e.id} — selected: ${MODEL_LABELS[e.selected_model]}`}
      description={`${e.params.n_train} training rows (cutoffs ${e.params.train_cutoffs?.join(", ")}) · ${e.params.n_test} test rows (cutoff ${e.params.test_cutoff}) · horizon ${e.horizon_days} days`}
      actions={e.dataset_kind === "synthetic" ? <DemoLabel>Synthetic data — not real observations</DemoLabel> : <Badge tone="good">Observed data</Badge>}
    >
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div>
          <Legend items={models.map((m, i) => ({ label: MODEL_LABELS[m] ?? m, color: SERIES[i] }))} />
          <div className="h-60">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} barGap={2}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="metric" {...axisProps} />
                <YAxis {...axisProps} domain={[0, 1]} width={36} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => v.toFixed(3)} />} />
                {models.map((m, i) => (
                  <Bar isAnimationActive={false} key={m} dataKey={m} name={MODEL_LABELS[m] ?? m} fill={SERIES[i]} radius={[4, 4, 0, 0]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Table className="mt-3">
            <thead>
              <tr><Th>Model</Th>{METRICS.map((m) => <Th key={m} align="right">{m}</Th>)}<Th align="right">brier</Th></tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model}>
                  <Td>{MODEL_LABELS[model] ?? model} {model === e.selected_model && <Badge tone="accent">selected</Badge>}</Td>
                  {METRICS.map((m) => <Td key={m} align="right">{e.metrics[model][m]?.toFixed(3) ?? "—"}</Td>)}
                  <Td align="right">{e.metrics[model].brier?.toFixed(3)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="mt-2 text-xs text-muted">
            Test positive rate {(e.params.test_positive_rate * 100).toFixed(1)}%. The heuristic predicts "accessed" when the file was accessed within the last horizon. Brier: lower is better.
          </p>
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold">Random-forest feature importance</h3>
          <ul className="space-y-1.5">
            {Object.entries(importance).map(([name, value]) => (
              <li key={name} className="text-xs">
                <div className="flex justify-between"><span className="font-mono">{name}</span><span className="tabular">{value.toFixed(3)}</span></div>
                <div className="mt-0.5 h-1.5 rounded-full bg-surface-2"><div className="h-full rounded-full" style={{ width: `${Math.min(100, value * 250)}%`, background: "var(--series-1)" }} /></div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

function AccessHistory({ status }: { status: Record<string, number> }) {
  const qc = useQueryClient();
  const [locationId, setLocationId] = useState<number>();
  const fileRef = useRef<HTMLInputElement>(null);
  const demo = useMutation({
    mutationFn: () => post<{ events_created: number; warning: string }>("/ml/demo-events", { location_id: locationId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ml-status"] }),
  });
  const importCsv = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<{ imported: number; unmatched_paths: number; invalid_rows: number }>(`/ml/access-events/import${qs({ location_id: locationId })}`, { method: "POST", body: form });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ml-status"] }),
  });
  const logRef = useRef<HTMLInputElement>(null);
  const importLog = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<{ imported: number; unmatched_objects: number; duplicates_skipped: number; non_read_or_invalid_lines: number }>("/ml/access-events/import-s3-log", { method: "POST", body: form });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ml-status"] }),
  });
  return (
    <Card title="Access history" description="Predictions for real files need access history. Filesystem access times are not trusted. Import real logs, or attach clearly-labelled synthetic history to try the workflow.">
      <div className="mb-3 flex flex-wrap gap-2">
        {Object.keys(status).length ? (
          Object.entries(status).map(([source, n]) => (
            <Badge key={source} tone={source === "synthetic_demo" ? "warning" : "good"}>{source}: {n} events</Badge>
          ))
        ) : (
          <span className="text-sm text-ink-2">No access events recorded.</span>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Location"><LocationSelect value={locationId} onChange={setLocationId} allowAll={false} /></Field>
        <Button onClick={() => fileRef.current?.click()} loading={importCsv.isPending}>Import CSV (path,timestamp)</Button>
        <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => e.target.files?.[0] && importCsv.mutate(e.target.files[0])} />
        <Button onClick={() => logRef.current?.click()} loading={importLog.isPending}>Import S3 server access log</Button>
        <input ref={logRef} type="file" className="hidden" onChange={(e) => e.target.files?.[0] && importLog.mutate(e.target.files[0])} />
        <Button onClick={() => demo.mutate()} loading={demo.isPending} disabled={!locationId}>Generate synthetic demo history</Button>
      </div>
      <p className="mt-2 text-xs text-muted">S3 server access logs are matched to scanned S3 objects by bucket and key; only successful GET requests count as accesses. Logs can also be read straight from the log bucket on the AWS S3 page.</p>
      {importLog.data && <div className="mt-3"><Alert tone="good">Imported {importLog.data.imported} accesses ({importLog.data.unmatched_objects} for objects not in the inventory, {importLog.data.duplicates_skipped} already imported, {importLog.data.non_read_or_invalid_lines} other lines ignored).</Alert></div>}
      {importLog.error && <div className="mt-3"><ErrorState error={importLog.error} /></div>}
      {importCsv.data && <div className="mt-3"><Alert tone="good">Imported {importCsv.data.imported} events ({importCsv.data.unmatched_paths} unmatched paths, {importCsv.data.invalid_rows} invalid rows).</Alert></div>}
      {importCsv.error && <div className="mt-3"><ErrorState error={importCsv.error} /></div>}
      {demo.data && <div className="mt-3"><Alert tone="warning">{demo.data.events_created} events created. {demo.data.warning}</Alert></div>}
      {demo.error && <div className="mt-3"><ErrorState error={demo.error} /></div>}
    </Card>
  );
}

function PredictionTable() {
  const files = useQuery({ queryKey: ["files-all"], queryFn: () => api<Page<FileRecord>>("/files?page_size=500") });
  const predict = useMutation({ mutationFn: () => post<Prediction[]>("/ml/predict", {}) });
  const names = new Map(files.data?.items.map((f) => [f.id, f.rel_path]));
  const rows = [...(predict.data ?? [])].sort((a, b) => (b.probability ?? -1) - (a.probability ?? -1));
  return (
    <Card title="Predictions for analysed files" actions={<Button variant="primary" onClick={() => predict.mutate()} loading={predict.isPending}>Predict now</Button>}>
      {predict.error && <ErrorState error={predict.error} />}
      {!predict.data ? (
        <p className="text-sm text-ink-2">Run predictions with the active model. Files without history are reported as unavailable rather than guessed.</p>
      ) : (
        <Table>
          <thead>
            <tr><Th>File</Th><Th align="right">P(access)</Th><Th>Reliability</Th><Th>Reason</Th></tr>
          </thead>
          <tbody>
            {rows.slice(0, 200).map((p) => (
              <tr key={p.file_id}>
                <Td className="font-mono text-xs">{names.get(p.file_id) ?? `#${p.file_id}`}</Td>
                <Td align="right">{p.probability === null ? "—" : `${(p.probability * 100).toFixed(0)}%`}</Td>
                <Td><Badge tone={p.reliability === "ok" ? "good" : p.reliability === "low" ? "warning" : "neutral"}>{p.reliability}</Badge></Td>
                <Td className="text-xs text-ink-2">{p.reason}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
