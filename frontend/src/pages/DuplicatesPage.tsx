import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { LocationSelect } from "../components/locations";
import { Alert, Badge, Card, EmptyState, ErrorState, PageHeader, Spinner, StatTile, Table, Td, Th } from "../components/ui";
import { api, qs } from "../lib/api";
import { formatBytes, formatDate } from "../lib/format";
import type { DuplicateGroup } from "../lib/types";

export function DuplicatesPage() {
  const [locationId, setLocationId] = useState<number>();
  const summary = useQuery({ queryKey: ["dup-summary"], queryFn: () => api<{ groups: number; duplicate_files: number; redundant_copies: number; redundant_bytes: number }>("/duplicates/summary") });
  const groups = useQuery({ queryKey: ["duplicates", locationId], queryFn: () => api<DuplicateGroup[]>(`/duplicates${qs({ location_id: locationId })}`) });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Exact duplicates"
        description="Files whose SHA-256 content hash and size are identical, regardless of name or folder. This is byte-level identity; near-duplicates are handled separately under Semantic comparison."
        actions={<LocationSelect value={locationId} onChange={setLocationId} />}
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Duplicate groups" value={summary.data?.groups ?? "—"} />
        <StatTile label="Files involved" value={summary.data?.duplicate_files ?? "—"} />
        <StatTile label="Redundant copies" value={summary.data?.redundant_copies ?? "—"} />
        <StatTile label="Verified reclaimable storage" value={formatBytes(summary.data?.redundant_bytes)} hint="If all but one copy per group were removed" tone="good" />
      </div>
      <Alert>OptiStore never deletes files. The suggested copy to keep is a heuristic (shortest path, first seen); check references to each path before removing anything yourself.</Alert>
      {groups.isLoading ? (
        <Spinner />
      ) : groups.error ? (
        <ErrorState error={groups.error} onRetry={groups.refetch} />
      ) : !groups.data?.length ? (
        <EmptyState title="No exact duplicates found" description="Either the analysed files are all unique, or no location has been scanned yet." />
      ) : (
        groups.data.map((g) => (
          <Card
            key={g.id}
            title={<span className="font-mono text-xs">sha256 {g.sha256.slice(0, 16)}…</span>}
            description={`${g.file_count} identical copies of ${formatBytes(g.size_bytes)}`}
            actions={<Badge tone="good">{formatBytes(g.redundant_bytes)} redundant</Badge>}
          >
            <Table>
              <thead>
                <tr>
                  <Th>Path</Th>
                  <Th>Modified</Th>
                  <Th>Suggestion</Th>
                </tr>
              </thead>
              <tbody>
                {g.files.map((f) => (
                  <tr key={f.id}>
                    <Td className="font-mono text-xs">{f.rel_path}</Td>
                    <Td className="text-xs text-ink-2">{formatDate(f.modified_at)}</Td>
                    <Td>{f.id === g.suggested_keep_id ? <Badge tone="accent">keep</Badge> : <Badge>redundant copy</Badge>}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        ))
      )}
    </div>
  );
}
