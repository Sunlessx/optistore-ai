import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Trash2 } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, Field, Input, PageHeader, Spinner, StatusBadge, Tabs, Textarea } from "../components/ui";
import { cn } from "../lib/cn";
import { ApiError, api, del, getApiKey, post, put, qs } from "../lib/api";
import { formatBytes, formatDate, formatPct } from "../lib/format";
import { Markdown } from "../lib/markdown";
import type { Consolidation, FileRecord, Page } from "../lib/types";

export function ConsolidationPage() {
  const [params] = useSearchParams();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const list = useQuery({ queryKey: ["consolidations"], queryFn: () => api<Consolidation[]>("/consolidations") });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/consolidations/${id}`),
    onSuccess: () => {
      setSelected(null);
      qc.invalidateQueries({ queryKey: ["consolidations"] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Document consolidation"
        description="Merge related documents into one reviewed draft: repeated statements are kept once with source tags, unique facts are kept, and conflicting facts are listed — never silently resolved. Originals are untouched."
      />
      <NewConsolidation preset={params.get("ids")} onCreated={(id) => setSelected(id)} />
      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        <Card title="Drafts">
          {list.isLoading ? (
            <Spinner />
          ) : list.error ? (
            <ErrorState error={list.error} />
          ) : !list.data?.length ? (
            <p className="text-sm text-ink-2">No drafts yet.</p>
          ) : (
            <ul className="-mx-2 space-y-1">
              {list.data.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => setSelected(c.id)}
                    className={cn("w-full rounded-lg px-2 py-2 text-left hover:bg-surface-2", selected === c.id && "bg-accent/8")}
                  >
                    <div className="truncate text-sm font-medium">{c.title}</div>
                    <div className="flex items-center gap-2 text-xs text-muted">
                      <StatusBadge status={c.status} /> {c.source_file_ids.length} sources · {formatDate(c.created_at)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {selected ? (
          <DraftEditor id={selected} onDelete={() => window.confirm("Delete this draft and its exported file? Source documents are not affected.") && remove.mutate(selected)} />
        ) : (
          <EmptyState title="Select or create a draft" description="Pick documents above, or start from a pair on the Semantic comparison page." />
        )}
      </div>
    </div>
  );
}

function NewConsolidation({ preset, onCreated }: { preset: string | null; onCreated: (id: number) => void }) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<number[]>(() => (preset ? preset.split(",").map(Number).filter(Boolean) : []));
  const [title, setTitle] = useState("Consolidated document");
  const docs = useQuery({
    queryKey: ["docs-for-consolidation", filter],
    queryFn: () => api<Page<FileRecord>>(`/files${qs({ category: "document", status: "analyzed", q: filter, page_size: 100 })}`),
  });
  const create = useMutation({
    mutationFn: () => post<Consolidation>("/consolidations", { file_ids: picked, title }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["consolidations"] });
      setPicked([]);
      onCreated(c.id);
    },
  });
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  return (
    <Card title="New consolidation" description="Choose 2–20 documents with extractable text. Source order defines the [S1], [S2]… tags.">
      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <div>
          <Input placeholder="Filter documents" value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-2 w-full" />
          <ul className="max-h-56 overflow-y-auto rounded-lg border border-line">
            {docs.data?.items.map((f) => (
              <li key={f.id}>
                <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-surface-2">
                  <input type="checkbox" checked={picked.includes(f.id)} onChange={() => toggle(f.id)} />
                  <span className="truncate font-mono text-xs">{f.rel_path}</span>
                  {picked.includes(f.id) && <Badge tone="accent" className="ml-auto">S{picked.indexOf(f.id) + 1}</Badge>}
                </label>
              </li>
            ))}
            {docs.data && !docs.data.items.length && <li className="px-3 py-2 text-xs text-muted">No analysed documents.</li>}
          </ul>
        </div>
        <div className="flex flex-col gap-3">
          <Field label="Title">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
          </Field>
          <Button variant="primary" disabled={picked.length < 2 || !title.trim()} loading={create.isPending} onClick={() => create.mutate()}>
            Generate draft from {picked.length} documents
          </Button>
          {create.error && <ErrorState error={create.error} />}
        </div>
      </div>
    </Card>
  );
}

function DraftEditor({ id, onDelete }: { id: number; onDelete: () => void }) {
  const draft = useQuery({ queryKey: ["consolidation", id], queryFn: () => api<Consolidation>(`/consolidations/${id}`) });
  if (draft.isLoading) return <Spinner />;
  if (draft.error) return <ErrorState error={draft.error} />;
  if (!draft.data) return null;
  // Remount when the saved content changes so the editor starts from the stored text.
  return <DraftForm key={`${draft.data.id}-${draft.data.updated_at}`} c={draft.data} onDelete={onDelete} />;
}

function DraftForm({ c, onDelete }: { c: Consolidation; onDelete: () => void }) {
  const qc = useQueryClient();
  const id = c.id;
  const [tab, setTab] = useState<"preview" | "edit" | "report">("preview");
  const [text, setText] = useState(c.edited_markdown ?? c.draft_markdown);
  const refresh = (updated: Consolidation) => {
    qc.setQueryData(["consolidation", id], updated);
    qc.invalidateQueries({ queryKey: ["consolidations"] });
  };
  const save = useMutation({ mutationFn: () => put<Consolidation>(`/consolidations/${id}`, { markdown: text }), onSuccess: refresh });
  const exportDoc = useMutation({ mutationFn: (format: "md" | "txt") => post<Consolidation>(`/consolidations/${id}/export`, { format }), onSuccess: refresh });
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const download = async () => {
    setDownloadError(null);
    const response = await fetch(`/api/consolidations/${id}/download`, { headers: getApiKey() ? { "X-API-Key": getApiKey() } : {} });
    if (!response.ok) {
      setDownloadError(new ApiError(response.status, "Download failed").message);
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = c?.export_path?.split(/[\\/]/).pop() ?? `consolidation-${id}.${c?.export_format ?? "md"}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const p = c.report.preservation;
  const dirty = text !== (c.edited_markdown ?? c.draft_markdown);
  const reduction = c.original_bytes ? 100 * (1 - c.output_bytes / c.original_bytes) : null;

  return (
    <Card
      title={c.title}
      description={`${c.source_file_ids.length} sources · ${formatBytes(c.original_bytes)} of source files → ${formatBytes(c.output_bytes)} ${c.export_path ? "exported (measured file size)" : "draft text"}`}
      actions={
        <>
          <Tabs value={tab} onChange={setTab} options={[{ value: "preview", label: "Preview" }, { value: "edit", label: "Edit" }, { value: "report", label: "Report" }]} />
          <Button size="sm" variant="ghost" onClick={onDelete} aria-label="Delete draft"><Trash2 className="size-3.5" /></Button>
        </>
      }
    >
      <div className="mb-4 flex flex-wrap gap-2">
        <StatusBadge status={c.status} />
        <Badge tone={c.report.conflict_count ? "critical" : "good"}>{c.report.conflict_count ?? 0} conflicts to review</Badge>
        {p && (
          <Badge tone={p.all_detected_tokens_present ? "good" : "warning"}>
            {p.all_detected_tokens_present ? "all detected figures/names present" : `${p.tokens_missing} detected tokens missing`}
          </Badge>
        )}
        {reduction !== null && <Badge>{formatPct(reduction)} smaller than sources</Badge>}
      </div>
      {tab === "preview" && (
        <div className="rounded-lg border border-line p-4">
          <Markdown source={text} />
        </div>
      )}
      {tab === "edit" && (
        <div className="space-y-2">
          <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={22} className="w-full" aria-label="Consolidated markdown" />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>Save review</Button>
            <Button variant="ghost" disabled={!dirty} onClick={() => setText(c.edited_markdown ?? c.draft_markdown)}>Discard changes</Button>
            <Button variant="ghost" onClick={() => setText(c.draft_markdown)}>Restore generated draft</Button>
            <span className="text-xs text-muted">Saving re-runs the preservation check on your edited text.</span>
          </div>
          {save.error && <ErrorState error={save.error} />}
        </div>
      )}
      {tab === "report" && (
        <div className="space-y-4 text-sm">
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ["Statements found", c.report.units_total],
              ["Kept", c.report.units_kept],
              ["Merged (repeated)", c.report.units_merged],
              ["Consensus facts", c.report.facts_consensus],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-lg bg-surface-2 p-2">
                <dt className="text-xs text-ink-2">{label}</dt>
                <dd className="text-lg font-semibold">{value ?? "—"}</dd>
              </div>
            ))}
          </dl>
          {p && (
            <Alert tone={p.all_detected_tokens_present ? "good" : "warning"} title="Preservation check">
              {p.tokens_checked} salient tokens checked ({p.method}); {p.tokens_missing} missing. {p.note}
              {Object.entries(p.missing_by_source).map(([src, toks]) => (
                <div key={src} className="mt-1 text-xs">
                  <strong>{src}</strong>: {toks.join(", ")}
                </div>
              ))}
            </Alert>
          )}
          <div>
            <h3 className="mb-1 font-semibold">Sources</h3>
            <ul className="font-mono text-xs">{c.report.sources?.map((s) => <li key={s.index}>[S{s.index}] {s.path}</li>)}</ul>
          </div>
        </div>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <span className="text-xs text-ink-2">Export to the server's outputs folder:</span>
        <Button size="sm" onClick={() => exportDoc.mutate("md")} loading={exportDoc.isPending && exportDoc.variables === "md"} disabled={dirty} title={dirty ? "Save your edits first" : undefined}>Markdown</Button>
        <Button size="sm" onClick={() => exportDoc.mutate("txt")} loading={exportDoc.isPending && exportDoc.variables === "txt"} disabled={dirty} title={dirty ? "Save your edits first" : undefined}>Plain text</Button>
        {c.export_path && (
          <Button size="sm" variant="primary" onClick={download}>
            <Download className="size-3.5" /> Download .{c.export_format}
          </Button>
        )}
        {exportDoc.error && <ErrorState error={exportDoc.error} />}
        {downloadError && <span className="text-xs text-critical">{downloadError}</span>}
      </div>
    </Card>
  );
}
