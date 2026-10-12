import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { LocationSelect } from "../components/locations";
import { Badge, Button, Card, EmptyState, ErrorState, Input, PageHeader, Select, Spinner, StatusBadge, Table, Td, Th } from "../components/ui";
import { api, post, qs } from "../lib/api";
import { formatBytes, formatDate, formatPct } from "../lib/format";
import type { FileDetail, FileRecord, Page } from "../lib/types";

type Sort = "path" | "name" | "size" | "modified" | "type";

function SortHead({ column, label, align, sort, order, onSort }: { column: Sort; label: string; align?: "left" | "right"; sort: Sort; order: "asc" | "desc"; onSort: (c: Sort) => void }) {
  return (
    <Th align={align}>
      <button className="inline-flex items-center gap-1 hover:text-ink" onClick={() => onSort(column)} aria-sort={sort === column ? (order === "asc" ? "ascending" : "descending") : undefined}>
        {label}
        {sort === column && (order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </Th>
  );
}

export function FilesPage() {
  const [locationId, setLocationId] = useState<number>();
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [dupOnly, setDupOnly] = useState(false);
  const [sort, setSort] = useState<Sort>("path");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<number | null>(null);
  const pageSize = 50;

  const files = useQuery({
    queryKey: ["files", locationId, category, status, q, dupOnly, sort, order, page],
    queryFn: () =>
      api<Page<FileRecord>>(`/files${qs({ location_id: locationId, category, status, q, duplicates_only: dupOnly || undefined, sort, order, page, page_size: pageSize })}`),
    placeholderData: keepPreviousData,
  });

  const toggleSort = (column: Sort) => {
    if (sort === column) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(column);
      setOrder(column === "size" || column === "modified" ? "desc" : "asc");
    }
    setPage(1);
  };
  const totalPages = files.data ? Math.max(1, Math.ceil(files.data.total / pageSize)) : 1;

  return (
    <div className="space-y-6">
      <PageHeader title="File explorer" description="Inventory of every analysed file with hash, type, status and extracted metadata. Click a row for details." />
      <Card>
        <div className="mb-4 flex flex-wrap gap-2">
          <LocationSelect value={locationId} onChange={(v) => { setLocationId(v); setPage(1); }} />
          <Select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }} aria-label="Category">
            <option value="">All categories</option>
            {["document", "data", "image", "video", "archive", "other"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </Select>
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
            <option value="">Present files</option>
            {["analyzed", "skipped", "error", "missing"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <Input placeholder="Filter by name or path" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="min-w-52 flex-1" />
          <label className="flex items-center gap-2 text-sm text-ink-2">
            <input type="checkbox" checked={dupOnly} onChange={(e) => { setDupOnly(e.target.checked); setPage(1); }} /> Exact duplicates only
          </label>
        </div>
        {files.isLoading ? (
          <Spinner />
        ) : files.error ? (
          <ErrorState error={files.error} onRetry={files.refetch} />
        ) : !files.data?.items.length ? (
          <EmptyState title="No files" description={<>Nothing matches these filters. Add and scan a location from the <Link className="text-accent" to="/">Overview</Link>.</>} />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <SortHead sort={sort} order={order} onSort={toggleSort} column="path" label="Path" />
                  <SortHead sort={sort} order={order} onSort={toggleSort} column="type" label="Type" />
                  <SortHead sort={sort} order={order} onSort={toggleSort} column="size" label="Size" align="right" />
                  <SortHead sort={sort} order={order} onSort={toggleSort} column="modified" label="Modified" />
                  <Th>SHA-256</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {files.data.items.map((f) => (
                  <tr key={f.id} onClick={() => setSelected(f.id)} className="cursor-pointer hover:bg-surface-2" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSelected(f.id)}>
                    <Td className="max-w-md">
                      <div className="truncate font-medium" title={f.rel_path}>{f.name}</div>
                      <div className="truncate font-mono text-xs text-muted">{f.rel_path}</div>
                    </Td>
                    <Td>
                      <Badge>{f.category}</Badge> <span className="text-xs text-muted">{f.extension}</span>
                      {f.duplicate_group_id && <Badge tone="accent" className="ml-1">duplicate</Badge>}
                    </Td>
                    <Td align="right">{formatBytes(f.size_bytes)}</Td>
                    <Td className="text-xs whitespace-nowrap text-ink-2">{formatDate(f.modified_at)}</Td>
                    <Td className="font-mono text-xs text-muted">{f.sha256 ? `${f.sha256.slice(0, 10)}…` : "—"}</Td>
                    <Td title={f.error ?? undefined}><StatusBadge status={f.status} /></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <div className="mt-3 flex items-center justify-between text-xs text-ink-2">
              <span>{files.data.total} files</span>
              <div className="flex items-center gap-2">
                <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
                <span>Page {page} / {totalPages}</span>
                <Button size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button>
              </div>
            </div>
          </>
        )}
      </Card>
      {selected !== null && <FileDrawer id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function FileDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const detail = useQuery({ queryKey: ["file", id], queryFn: () => api<FileDetail>(`/files/${id}`) });
  const bench = useMutation({ mutationFn: () => post(`/compression/files/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["file", id] }) });
  const f = detail.data;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="File details">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} aria-hidden />
      <div className="relative h-full w-full max-w-xl overflow-y-auto bg-surface p-5 shadow-2xl">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{f?.name ?? "Loading…"}</h2>
            <div className="truncate font-mono text-xs text-muted">{f?.rel_path}</div>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
        </div>
        {detail.isLoading && <Spinner />}
        {detail.error && <ErrorState error={detail.error} />}
        {f && (
          <div className="space-y-5 text-sm">
            <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1.5 text-xs">
              <dt className="text-ink-2">Location</dt><dd>{f.location_name}</dd>
              <dt className="text-ink-2">Status</dt><dd><StatusBadge status={f.status} /> {f.error && <span className="text-critical">{f.error}</span>}</dd>
              <dt className="text-ink-2">Size</dt><dd>{formatBytes(f.size_bytes)} ({f.size_bytes} bytes)</dd>
              <dt className="text-ink-2">MIME type</dt><dd>{f.mime_type}</dd>
              <dt className="text-ink-2">SHA-256</dt><dd className="font-mono break-all">{f.sha256 ?? "not computed"}</dd>
              <dt className="text-ink-2">Modified</dt><dd>{formatDate(f.modified_at)}</dd>
              <dt className="text-ink-2">Last access</dt><dd>{formatDate(f.accessed_at)} <span className="text-muted">(filesystem atime — not treated as reliable)</span></dd>
              {f.storage_class && <><dt className="text-ink-2">Storage class</dt><dd>{f.storage_class}</dd></>}
              <dt className="text-ink-2">Text indexed</dt><dd>{f.text_chars} chars · {f.chunk_count} chunks {f.embedding_model && <span className="text-muted">({f.embedding_model})</span>}</dd>
              <dt className="text-ink-2">Access events</dt><dd>{f.access_event_count}</dd>
            </dl>
            {Object.keys(f.metadata_json).length > 0 && (
              <div>
                <h3 className="mb-1 text-xs font-semibold">Extracted metadata</h3>
                <pre className="max-h-40 overflow-auto rounded-lg bg-surface-2 p-2 text-xs">{JSON.stringify(f.metadata_json, null, 2)}</pre>
              </div>
            )}
            {f.duplicates.length > 0 && (
              <div>
                <h3 className="mb-1 text-xs font-semibold">Identical copies (same SHA-256)</h3>
                <ul className="space-y-1 font-mono text-xs">{f.duplicates.map((d) => <li key={d.id}>{d.rel_path}</li>)}</ul>
              </div>
            )}
            {f.similar.length > 0 && (
              <div>
                <h3 className="mb-1 text-xs font-semibold">Semantically similar</h3>
                <ul className="space-y-1 text-xs">
                  {f.similar.map((s) => (
                    <li key={s.file_id} className="flex justify-between gap-2">
                      <Link className="truncate text-accent hover:underline" to={`/compare?a=${f.id}&b=${s.file_id}`}>{s.path}</Link>
                      <span className="tabular">{s.score.toFixed(3)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div>
              <div className="mb-1 flex items-center justify-between">
                <h3 className="text-xs font-semibold">Compression benchmark</h3>
                <Button size="sm" onClick={() => bench.mutate()} loading={bench.isPending} disabled={f.status === "missing"}>
                  {f.compression.length ? "Re-run" : "Benchmark"}
                </Button>
              </div>
              {bench.error && <ErrorState error={bench.error} />}
              {f.compression.length ? (
                <ul className="space-y-1 text-xs">
                  {f.compression.map((c) => (
                    <li key={c.id} className="flex flex-wrap items-center gap-2">
                      <span className="font-mono">{c.method}</span>
                      <Badge tone={c.lossless ? "good" : "warning"}>{c.lossless ? "lossless" : "lossy"}</Badge>
                      <StatusBadge status={c.status} />
                      {c.percent_saved !== null && <span className="tabular">{formatPct(c.percent_saved)} saved</span>}
                      {c.is_best && <Badge tone="accent">best</Badge>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted">Not benchmarked yet.</p>
              )}
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold">Access prediction</h3>
              {f.prediction ? (
                <p className="text-xs">
                  {f.prediction.probability !== null ? `${(f.prediction.probability * 100).toFixed(0)}% chance of access in ${f.prediction.horizon_days} days. ` : ""}
                  <StatusBadge status={f.prediction.reliability === "ok" ? "success" : f.prediction.reliability === "low" ? "skipped" : "info"} label={`reliability: ${f.prediction.reliability}`} />{" "}
                  <span className="text-ink-2">{f.prediction.reason}</span>
                </p>
              ) : (
                <p className="text-xs text-muted">No prediction computed.</p>
              )}
            </div>
            {f.text_preview && (
              <div>
                <h3 className="mb-1 text-xs font-semibold">Extracted text (preview, shown as plain text)</h3>
                <pre className="max-h-72 overflow-auto rounded-lg bg-surface-2 p-2 text-xs whitespace-pre-wrap">{f.text_preview}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
