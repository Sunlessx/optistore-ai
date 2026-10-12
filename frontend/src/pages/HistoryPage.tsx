import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, Select, Spinner, StatusBadge, Table, Td, Th } from "../components/ui";
import { api, qs } from "../lib/api";
import { formatDate } from "../lib/format";
import type { Operation, Page } from "../lib/types";

export function HistoryPage() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<number | null>(null);
  const ops = useQuery({
    queryKey: ["operations", status, page],
    queryFn: () => api<Page<Operation>>(`/operations${qs({ status, page, page_size: 50 })}`),
    placeholderData: keepPreviousData,
  });
  const pages = ops.data ? Math.max(1, Math.ceil(ops.data.total / 50)) : 1;
  return (
    <div className="space-y-6">
      <PageHeader title="Operation history" description="Append-only audit log of scans, analyses, approvals, dry runs, executions and settings changes." />
      <Card
        actions={
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status filter">
            <option value="">All statuses</option>
            {["success", "failure", "dry_run", "info"].map((s) => <option key={s}>{s}</option>)}
          </Select>
        }
        title={`${ops.data?.total ?? 0} entries`}
      >
        {ops.isLoading ? (
          <Spinner />
        ) : ops.error ? (
          <ErrorState error={ops.error} />
        ) : !ops.data?.items.length ? (
          <EmptyState title="No operations recorded yet" />
        ) : (
          <>
            <Table>
              <thead>
                <tr><Th>Time</Th><Th>Operation</Th><Th>Target</Th><Th>Status</Th><Th>Actor</Th><Th /></tr>
              </thead>
              <tbody>
                {ops.data.items.map((o) => (
                  <Fragment key={o.id}>
                    <tr>
                      <Td className="text-xs whitespace-nowrap text-ink-2">{formatDate(o.timestamp)}</Td>
                      <Td className="font-mono text-xs">{o.operation}</Td>
                      <Td className="max-w-xs truncate font-mono text-xs text-muted" title={o.target ?? ""}>{o.target ?? "—"}</Td>
                      <Td><StatusBadge status={o.status} /> {o.dry_run && <Badge>dry run</Badge>}</Td>
                      <Td className="text-xs">{o.actor}</Td>
                      <Td><Button size="sm" variant="ghost" onClick={() => setExpanded(expanded === o.id ? null : o.id)}>{expanded === o.id ? "Hide" : "Details"}</Button></Td>
                    </tr>
                    {expanded === o.id && (
                      <tr>
                        <td colSpan={6} className="border-b border-line bg-surface-2/50 p-3">
                          <pre className="max-h-64 overflow-auto text-xs whitespace-pre-wrap">{JSON.stringify(o.details, null, 2)}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </Table>
            <div className="mt-3 flex items-center justify-end gap-2 text-xs text-ink-2">
              <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <span>Page {page} / {pages}</span>
              <Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
