import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, FolderOpen, Play, Square, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, del, post, qs } from "../lib/api";
import { formatBytes } from "../lib/format";
import { useLocations } from "../lib/hooks";
import type { Location, ScanJob } from "../lib/types";
import { Alert, Button, Card, EmptyState, ErrorState, Field, Input, Progress, Select, StatusBadge } from "./ui";

export function LocationSelect({ value, onChange, allowAll = true }: { value: number | undefined; onChange: (id: number | undefined) => void; allowAll?: boolean }) {
  const { data } = useLocations();
  // Without an "All" option the first location is the effective selection; report it upward.
  const effective = value ?? (allowAll ? undefined : data?.[0]?.id);
  useEffect(() => {
    if (!allowAll && value === undefined && effective !== undefined) onChange(effective);
  }, [allowAll, value, effective, onChange]);
  return (
    <Select value={effective ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)} aria-label="Location">
      {allowAll && <option value="">All locations</option>}
      {data?.map((l) => (
        <option key={l.id} value={l.id}>
          {l.name}
        </option>
      ))}
    </Select>
  );
}

interface Browse {
  roots: string[];
  current: string | null;
  parent: string | null;
  directories: { name: string; path: string }[];
}

function DirectoryBrowser({ onPick }: { onPick: (path: string) => void }) {
  const [path, setPath] = useState<string | null>(null);
  const browse = useQuery({ queryKey: ["browse", path], queryFn: () => api<Browse>(`/locations/browse${qs({ path })}`) });
  if (browse.isError) return <ErrorState error={browse.error} />;
  const data = browse.data;
  return (
    <div className="rounded-lg border border-line">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2 text-xs text-ink-2">
        <span className="truncate font-mono">{data?.current ?? "Authorised roots"}</span>
        <div className="flex gap-1">
          {data?.current && (
            <Button size="sm" variant="ghost" onClick={() => setPath(data.parent)}>
              Up
            </Button>
          )}
          {data?.current && (
            <Button size="sm" variant="primary" onClick={() => onPick(data.current!)}>
              Use this folder
            </Button>
          )}
        </div>
      </div>
      <ul className="max-h-56 overflow-y-auto py-1">
        {data?.directories.map((d) => (
          <li key={d.path}>
            <button className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-surface-2" onClick={() => setPath(d.path)}>
              <FolderOpen className="size-4 text-muted" aria-hidden />
              <span className="truncate">{d.name}</span>
              <ChevronRight className="ml-auto size-4 text-muted" aria-hidden />
            </button>
          </li>
        ))}
        {data && data.directories.length === 0 && <li className="px-3 py-2 text-xs text-muted">No sub-folders.</li>}
      </ul>
    </div>
  );
}

export function ScanProgress({ jobId, onDone }: { jobId: number; onDone?: () => void }) {
  const qc = useQueryClient();
  const job = useQuery({
    queryKey: ["scan", jobId],
    queryFn: () => api<ScanJob>(`/scans/${jobId}`),
    refetchInterval: (q) => (q.state.data && ["completed", "failed", "cancelled"].includes(q.state.data.status) ? false : 1000),
  });
  const cancel = useMutation({ mutationFn: () => post(`/scans/${jobId}/cancel`), onSuccess: () => job.refetch() });
  const status = job.data?.status;
  useEffect(() => {
    if (status && ["completed", "failed", "cancelled"].includes(status)) {
      qc.invalidateQueries();
      onDone?.();
    }
  }, [status, qc, onDone]);
  if (!job.data) return null;
  const j = job.data;
  const active = j.status === "pending" || j.status === "running";
  return (
    <div className="space-y-2 rounded-lg border border-line p-3">
      <div className="flex items-center justify-between gap-2 text-sm">
        <div className="flex items-center gap-2">
          <StatusBadge status={j.status} />
          <span className="text-ink-2">{j.phase}</span>
        </div>
        {active && (
          <Button size="sm" variant="ghost" onClick={() => cancel.mutate()} loading={cancel.isPending}>
            <Square className="size-3" /> Cancel
          </Button>
        )}
      </div>
      <Progress value={j.processed_files} max={Math.max(j.total_files, 1)} />
      <div className="text-xs text-ink-2">
        {j.processed_files} / {j.total_files} files · {j.error_count} errors · {j.skipped_count} skipped
        {j.message && <span className="block pt-1">{j.message}</span>}
      </div>
    </div>
  );
}

/** Register authorised locations and run scans. */
export function LocationsPanel() {
  const qc = useQueryClient();
  const locations = useLocations();
  const [kind, setKind] = useState<"local" | "s3">("local");
  const [path, setPath] = useState("");
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("");
  const [showBrowser, setShowBrowser] = useState(false);
  const [activeJob, setActiveJob] = useState<number | null>(null);

  const create = useMutation({
    mutationFn: () => post<Location>("/locations", kind === "local" ? { kind, path } : { kind, bucket, prefix: prefix || null }),
    onSuccess: () => {
      setPath("");
      setBucket("");
      setPrefix("");
      qc.invalidateQueries({ queryKey: ["locations"] });
    },
  });
  const scan = useMutation({
    mutationFn: (locationId: number) => post<ScanJob>("/scans", { location_id: locationId }),
    onSuccess: (job) => setActiveJob(job.id),
  });
  const remove = useMutation({ mutationFn: (id: number) => del(`/locations/${id}`), onSuccess: () => qc.invalidateQueries() });

  return (
    <Card title="Storage locations" description="Only folders inside the server's ALLOWED_SCAN_ROOTS (and permitted S3 buckets) can be analysed. Analysis is read-only.">
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-[140px_1fr_auto] md:items-end">
          <Field label="Type">
            <Select value={kind} onChange={(e) => setKind(e.target.value as "local" | "s3")}>
              <option value="local">Local folder</option>
              <option value="s3">S3 bucket</option>
            </Select>
          </Field>
          {kind === "local" ? (
            <Field label="Folder path">
              <div className="flex gap-2">
                <Input className="flex-1 font-mono text-xs" placeholder="C:\\data\\shared" value={path} onChange={(e) => setPath(e.target.value)} />
                <Button onClick={() => setShowBrowser(!showBrowser)}>Browse</Button>
              </div>
            </Field>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <Field label="Bucket">
                <Input value={bucket} onChange={(e) => setBucket(e.target.value)} placeholder="my-bucket" />
              </Field>
              <Field label="Prefix (optional)">
                <Input value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="reports/" />
              </Field>
            </div>
          )}
          <Button variant="primary" onClick={() => create.mutate()} loading={create.isPending} disabled={kind === "local" ? !path : !bucket}>
            Add location
          </Button>
        </div>
        {showBrowser && kind === "local" && (
          <DirectoryBrowser
            onPick={(p) => {
              setPath(p);
              setShowBrowser(false);
            }}
          />
        )}
        {create.isError && <ErrorState error={create.error} />}
        {scan.isError && <ErrorState error={scan.error} />}
        {activeJob && <ScanProgress jobId={activeJob} />}
        {locations.isLoading ? null : locations.data?.length ? (
          <ul className="divide-y divide-line rounded-lg border border-line">
            {locations.data.map((l) => (
              <li key={l.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{l.name}</div>
                  <div className="truncate font-mono text-xs text-muted">{l.kind === "local" ? l.path : `s3://${l.bucket}/${l.prefix ?? ""}`}</div>
                </div>
                <div className="text-xs text-ink-2">
                  {l.file_count} files · {formatBytes(l.total_bytes)}
                </div>
                {l.last_scan_status && <StatusBadge status={l.last_scan_status} />}
                <Button size="sm" variant="primary" onClick={() => scan.mutate(l.id)} loading={scan.isPending && scan.variables === l.id}>
                  <Play className="size-3" /> {l.last_scan_status ? "Rescan" : "Scan"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${l.name}`}
                  title="Remove from OptiStore (files are not deleted)"
                  onClick={() => window.confirm(`Remove "${l.name}" and its analysis records? Files on disk / S3 are NOT deleted.`) && remove.mutate(l.id)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No locations yet" description="Add the bundled sample_data folder (use Browse) to explore the demo, or any folder you have authorised." />
        )}
        {kind === "s3" && <Alert>S3 analysis needs AWS credentials on the server. See the AWS S3 page for connection status.</Alert>}
      </div>
    </Card>
  );
}
