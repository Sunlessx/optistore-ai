import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useLocations } from "../lib/hooks";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, Field, Input, PageHeader, Spinner, StatusBadge, Table, Td, Th } from "../components/ui";
import { api, post } from "../lib/api";
import { classLabel, formatBytes, formatDate } from "../lib/format";
import type { FileRecord, Location, Page } from "../lib/types";

interface AwsStatus {
  configured: boolean;
  connected: boolean;
  account?: string;
  identity_arn?: string;
  region: string;
  profile: string | null;
  write_enabled: boolean;
  allowed_buckets: string[];
  message: string;
}

export function AwsPage() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["aws-status"], queryFn: () => api<AwsStatus>("/aws/status") });
  const buckets = useQuery({
    queryKey: ["aws-buckets"],
    queryFn: () => api<{ name: string; created_at: string | null }[]>("/aws/buckets"),
    enabled: Boolean(status.data?.connected),
  });
  const locations = useLocations();
  const add = useMutation({
    mutationFn: (bucket: string) => post<Location>("/locations", { kind: "s3", bucket }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["locations"] }),
  });
  const s3Locations = locations.data?.filter((l) => l.kind === "s3") ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="AWS S3"
        description="Optional, read-only by default. Credentials come from the server's AWS profile, environment or IAM role and are never sent to the browser."
        actions={<Button onClick={() => status.refetch()} loading={status.isFetching}>Re-check connection</Button>}
      />
      <Card title="Connection">
        {status.isLoading ? (
          <Spinner />
        ) : status.error ? (
          <ErrorState error={status.error} />
        ) : (
          status.data && (
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={status.data.connected ? "success" : status.data.configured ? "failure" : "info"} label={status.data.connected ? "Connected" : status.data.configured ? "Connection failed" : "Not configured"} />
                <Badge>region {status.data.region}</Badge>
                {status.data.profile && <Badge>profile {status.data.profile}</Badge>}
                <Badge tone={status.data.write_enabled ? "warning" : "good"}>cloud writes {status.data.write_enabled ? "enabled (approval still required)" : "disabled"}</Badge>
              </div>
              <p className="text-ink-2">{status.data.message}</p>
              {status.data.identity_arn && <p className="font-mono text-xs text-muted">{status.data.identity_arn}</p>}
              {!status.data.connected && (
                <Alert title="How to connect">
                  Set <code>AWS_PROFILE</code> (or <code>AWS_ACCESS_KEY_ID</code>/<code>AWS_SECRET_ACCESS_KEY</code>) and <code>AWS_REGION</code> in the backend environment, optionally restrict buckets with <code>ALLOWED_S3_BUCKETS</code>, then restart the API. See README → AWS configuration for a least-privilege IAM policy. Everything else in OptiStore works without AWS.
                </Alert>
              )}
            </div>
          )
        )}
      </Card>
      {status.data?.connected && (
        <Card title="Buckets" description={status.data.allowed_buckets.length ? `Restricted to: ${status.data.allowed_buckets.join(", ")}` : "All buckets visible to these credentials"}>
          {buckets.isLoading ? (
            <Spinner />
          ) : buckets.error ? (
            <ErrorState error={buckets.error} />
          ) : !buckets.data?.length ? (
            <EmptyState title="No buckets visible" />
          ) : (
            <Table>
              <thead><tr><Th>Bucket</Th><Th>Created</Th><Th /></tr></thead>
              <tbody>
                {buckets.data.map((b) => {
                  const registered = s3Locations.some((l) => l.bucket === b.name && !l.prefix);
                  return (
                    <tr key={b.name}>
                      <Td className="font-mono text-xs">{b.name}</Td>
                      <Td className="text-xs text-ink-2">{formatDate(b.created_at)}</Td>
                      <Td>
                        {registered ? <Badge tone="good">registered</Badge> : <Button size="sm" onClick={() => add.mutate(b.name)} loading={add.isPending && add.variables === b.name}>Add as location</Button>}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
          {add.error && <div className="mt-3"><ErrorState error={add.error} /></div>}
          <p className="mt-3 text-xs text-ink-2">Scan registered buckets from the <Link className="text-accent" to="/">Overview</Link>. Objects ≤ S3_MAX_DOWNLOAD_MB are downloaded for hashing and text extraction; archived objects are analysed from metadata only.</p>
        </Card>
      )}
      {s3Locations.map((l) => <BucketInventory key={l.id} location={l} />)}
      {status.data?.connected && <AccessLogFetch />}
      <Card title="How cloud actions are controlled">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
          <li>Storage-class changes are proposed in an optimisation plan with cost evidence, risks and reversibility.</li>
          <li>Approval requires explicitly accepting the risks; execution additionally requires <code>S3_WRITE_ENABLED=true</code> on the server.</li>
          <li>Each change is validated against live object state (archived objects, objects &gt; 5 GB and no-op changes are refused) and verified afterwards.</li>
          <li>Every attempt, including dry runs and failures, is written to the operation history.</li>
        </ul>
      </Card>
    </div>
  );
}

function BucketInventory({ location }: { location: Location }) {
  const files = useQuery({ queryKey: ["s3-files", location.id], queryFn: () => api<Page<FileRecord>>(`/files?location_id=${location.id}&page_size=500`) });
  const byClass = new Map<string, { objects: number; bytes: number }>();
  for (const f of files.data?.items ?? []) {
    const key = f.storage_class ?? "STANDARD";
    const entry = byClass.get(key) ?? { objects: 0, bytes: 0 };
    entry.objects++;
    entry.bytes += f.size_bytes;
    byClass.set(key, entry);
  }
  return (
    <Card title={`s3://${location.bucket}/${location.prefix ?? ""}`} description={`${location.file_count} objects · ${formatBytes(location.total_bytes)}`}>
      {files.isLoading ? (
        <Spinner />
      ) : !files.data?.items.length ? (
        <p className="text-sm text-ink-2">Not scanned yet.</p>
      ) : (
        <Table>
          <thead><tr><Th>Current storage class</Th><Th align="right">Objects</Th><Th align="right">Bytes</Th></tr></thead>
          <tbody>
            {[...byClass.entries()].map(([cls, v]) => (
              <tr key={cls}><Td>{classLabel[cls] ?? cls}</Td><Td align="right">{v.objects}</Td><Td align="right">{formatBytes(v.bytes)}</Td></tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function AccessLogFetch() {
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("");
  const fetchLogs = useMutation({
    mutationFn: () => post<{ imported: number; log_objects_read: number; unmatched_objects: number; duplicates_skipped: number }>("/ml/access-events/fetch-s3-logs", { log_bucket: bucket, prefix }),
  });
  return (
    <Card title="Import access history from S3 server access logs" description="Reads log files that S3 server access logging writes to a log bucket. Successful GETs on scanned objects become observed access events for the prediction model.">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Log bucket"><Input value={bucket} onChange={(e) => setBucket(e.target.value)} placeholder="my-access-logs" /></Field>
        <Field label="Prefix"><Input value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="logs/my-bucket/" /></Field>
        <Button variant="primary" disabled={!bucket} loading={fetchLogs.isPending} onClick={() => fetchLogs.mutate()}>Fetch and import</Button>
      </div>
      {fetchLogs.data && <div className="mt-3"><Alert tone="good">Read {fetchLogs.data.log_objects_read} log files; imported {fetchLogs.data.imported} accesses ({fetchLogs.data.unmatched_objects} unmatched, {fetchLogs.data.duplicates_skipped} duplicates).</Alert></div>}
      {fetchLogs.error && <div className="mt-3"><ErrorState error={fetchLogs.error} /></div>}
    </Card>
  );
}
