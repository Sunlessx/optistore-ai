import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Alert, Badge, Button, Card, ErrorState, Field, Input, PageHeader, Select, Spinner } from "../components/ui";
import { api, getApiKey, put, setApiKey } from "../lib/api";
import type { EmbeddingStatus } from "../lib/types";

type Values = Record<string, number | string>;

interface SettingsResponse {
  values: Values;
  defaults: Values;
  bounds: Record<string, [number, number]>;
  deployment: {
    environment: string;
    database: string;
    allowed_scan_roots: string[];
    max_file_size_mb: number;
    embedding_backend: string;
    embedding_model: string;
    s3_write_enabled: boolean;
    api_key_required: boolean;
    pricing_regions: string[];
  };
  embedding: EmbeddingStatus;
  tools: Record<string, { available: boolean; version?: string | null }>;
  vector_backend: string;
}

const LABELS: Record<string, [string, string]> = {
  similarity_threshold: ["Document similarity threshold", "Pairs at or above this cosine score are shown as candidates."],
  chunk_match_threshold: ["Section match threshold", "Used for chunk coverage between two documents."],
  sentence_match_threshold: ["Sentence match threshold", "Sentences above this score count as shared in comparisons/consolidation."],
  min_document_chars: ["Minimum characters to index", "Shorter texts are not embedded (too little signal)."],
  cost_horizon_months: ["Cost horizon (months)", "Period over which storage costs are estimated."],
  prediction_horizon_days: ["Prediction horizon (days)", "Default horizon for access-prediction training."],
  reads_per_accessed_month: ["Reads per accessed month", "Expected reads when a file is predicted to be accessed."],
  compression_min_saving_percent: ["Minimum compression saving (%)", "Below this, compression is not recommended."],
};

export function SettingsPage() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsResponse>("/settings") });
  if (settings.isLoading) return <Spinner />;
  if (settings.error) return <ErrorState error={settings.error} onRetry={settings.refetch} />;
  return <SettingsForm d={settings.data!} />;
}

function SettingsForm({ d }: { d: SettingsResponse }) {
  const qc = useQueryClient();
  const [values, setValues] = useState<Values>(d.values);
  const [key, setKey] = useState(getApiKey());
  const [keySaved, setKeySaved] = useState(false);
  const save = useMutation({
    mutationFn: () => put<{ values: Values }>("/settings", { values }),
    onSuccess: () => qc.invalidateQueries(),
  });


  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Analysis settings are stored in the database. Deployment settings (paths, credentials, write permissions) come from the server environment and are read-only here." />
      <Card title="Analysis settings" actions={<Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>Save</Button>}>
        <div className="grid gap-4 md:grid-cols-2">
          {Object.entries(LABELS).map(([k, [label, hint]]) => (
            <Field key={k} label={label} hint={`${hint} Range ${d.bounds[k]?.join("–")}; default ${d.defaults[k]}.`}>
              <Input type="number" step="any" value={values[k] ?? ""} onChange={(e) => setValues({ ...values, [k]: e.target.value === "" ? "" : Number(e.target.value) })} />
            </Field>
          ))}
          <Field label="Default strategy" hint="Used by the dashboard's storage-class summary.">
            <Select value={String(values.default_strategy ?? "")} onChange={(e) => setValues({ ...values, default_strategy: e.target.value })}>
              <option value="conservative">Conservative</option>
              <option value="balanced">Balanced</option>
              <option value="aggressive">Aggressive</option>
            </Select>
          </Field>
          <Field label="Pricing region" hint="Regions available in the pricing assumptions file.">
            <Select value={String(values.pricing_region ?? "")} onChange={(e) => setValues({ ...values, pricing_region: e.target.value })}>
              {d.deployment.pricing_regions.map((r) => <option key={r}>{r}</option>)}
            </Select>
          </Field>
        </div>
        {save.error && <div className="mt-3"><ErrorState error={save.error} /></div>}
        {save.isSuccess && <div className="mt-3"><Alert tone="good">Settings saved. Recompute similarity or rerun simulations to apply new thresholds.</Alert></div>}
      </Card>
      <Card title="API key (this browser only)" description={d.deployment.api_key_required ? "The server requires an X-API-Key header." : "The server does not currently require an API key."}>
        <div className="flex flex-wrap gap-2">
          <Input type="password" value={key} onChange={(e) => { setKey(e.target.value); setKeySaved(false); }} placeholder="API key" className="min-w-64" autoComplete="off" />
          <Button onClick={() => { setApiKey(key); setKeySaved(true); qc.invalidateQueries(); }}>Save key</Button>
        </div>
        {keySaved && <p className="mt-2 text-xs text-good-ink">Saved in this browser's local storage.</p>}
      </Card>
      <Card title="Deployment (read-only)">
        <dl className="grid grid-cols-[200px_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-ink-2">Environment</dt><dd>{d.deployment.environment}</dd>
          <dt className="text-ink-2">Database</dt><dd>{d.deployment.database}</dd>
          <dt className="text-ink-2">Authorised scan roots</dt><dd className="font-mono text-xs">{d.deployment.allowed_scan_roots.map((r) => <div key={r}>{r}</div>)}</dd>
          <dt className="text-ink-2">Max file size</dt><dd>{d.deployment.max_file_size_mb} MB</dd>
          <dt className="text-ink-2">Embedding model</dt>
          <dd>
            {d.embedding.active_model ?? d.deployment.embedding_model}{" "}
            {d.embedding.active_backend === "hashing" ? <Badge tone="warning">lexical fallback</Badge> : d.embedding.active_backend ? <Badge tone="good">semantic</Badge> : <Badge>not loaded yet</Badge>}
            {d.embedding.error && <div className="text-xs text-critical">{d.embedding.error}</div>}
          </dd>
          <dt className="text-ink-2">Vector index</dt><dd>{d.vector_backend === "faiss" ? <Badge tone="good">FAISS</Badge> : <Badge>NumPy (exact)</Badge>}</dd>
          <dt className="text-ink-2">OCR (Tesseract)</dt><dd>{d.tools.tesseract?.available ? <><Badge tone="good">available</Badge> <span className="text-xs text-muted">{d.tools.tesseract.version}</span></> : <Badge tone="warning">not installed: scanned PDFs and image text are skipped</Badge>}</dd>
          <dt className="text-ink-2">Video (ffmpeg)</dt><dd>{d.tools.ffmpeg?.available ? <><Badge tone="good">available</Badge> <span className="text-xs text-muted">{d.tools.ffmpeg.version}</span></> : <Badge tone="warning">not installed: video savings are estimates only</Badge>}</dd>
          <dt className="text-ink-2">S3 writes</dt><dd>{d.deployment.s3_write_enabled ? <Badge tone="warning">enabled</Badge> : <Badge tone="good">disabled</Badge>}</dd>
        </dl>
      </Card>
    </div>
  );
}
