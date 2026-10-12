import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, Field, Input, PageHeader, Spinner, Table, Td, Th } from "../components/ui";
import { api, post, qs } from "../lib/api";
import type { Comparison, SimilarityPair } from "../lib/types";

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [threshold, setThreshold] = useState(0.6);
  const a = params.get("a");
  const b = params.get("b");

  const pairs = useQuery({ queryKey: ["similarity", threshold], queryFn: () => api<SimilarityPair[]>(`/similarity${qs({ min_score: threshold })}`) });
  const comparison = useQuery({
    queryKey: ["compare", a, b],
    queryFn: () => api<Comparison>(`/compare${qs({ a, b })}`),
    enabled: Boolean(a && b),
  });
  const recompute = useMutation({ mutationFn: () => post("/similarity/recompute"), onSuccess: () => pairs.refetch() });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Semantic comparison"
        description="Document pairs whose embeddings are close. A high score means related content worth reviewing; it never proves the documents are interchangeable."
        actions={<Button onClick={() => recompute.mutate()} loading={recompute.isPending}>Recompute similarity</Button>}
      />
      <Card
        title="Similar document pairs"
        description="Score = cosine similarity of averaged chunk embeddings. Coverage = share of each document's chunks with a close match in the other. Exact duplicates are excluded."
        actions={
          <Field label={`Minimum score: ${threshold.toFixed(2)}`}>
            <Input type="range" min={0.5} max={0.99} step={0.01} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} className="h-6 px-0" aria-label="Minimum similarity" />
          </Field>
        }
      >
        {pairs.isLoading ? (
          <Spinner />
        ) : pairs.error ? (
          <ErrorState error={pairs.error} />
        ) : !pairs.data?.length ? (
          <EmptyState title="No pairs above this score" description="Lower the threshold or scan more documents." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Document A</Th>
                <Th>Document B</Th>
                <Th align="right">Score</Th>
                <Th align="right">Coverage A / B</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {pairs.data.map((p) => {
                const active = String(p.file_a.id) === a && String(p.file_b.id) === b;
                return (
                  <tr key={p.id} className={active ? "bg-accent/6" : undefined}>
                    <Td className="font-mono text-xs">{p.file_a.rel_path}</Td>
                    <Td className="font-mono text-xs">{p.file_b.rel_path}</Td>
                    <Td align="right">{p.score.toFixed(3)}</Td>
                    <Td align="right">{(p.coverage_a * 100).toFixed(0)}% / {(p.coverage_b * 100).toFixed(0)}%</Td>
                    <Td>
                      <Button size="sm" variant={active ? "primary" : "secondary"} onClick={() => setParams({ a: String(p.file_a.id), b: String(p.file_b.id) })}>
                        Compare
                      </Button>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {a && b && (
        <Card
          title="Detailed comparison"
          actions={
            comparison.data && (
              <Button variant="primary" onClick={() => navigate(`/consolidation?ids=${a},${b}`)}>
                Draft a consolidation
              </Button>
            )
          }
        >
          {comparison.isLoading ? (
            <Spinner label="Aligning sentences…" />
          ) : comparison.error ? (
            <ErrorState error={comparison.error} />
          ) : (
            comparison.data && <ComparisonView c={comparison.data} />
          )}
        </Card>
      )}
    </div>
  );
}

function ComparisonView({ c }: { c: Comparison }) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-2 text-xs">
        <Badge tone="accent">document similarity {c.document_similarity?.toFixed(3) ?? "n/a"}</Badge>
        <Badge>A shared {(c.shared_ratio_a * 100).toFixed(0)}%</Badge>
        <Badge>B shared {(c.shared_ratio_b * 100).toFixed(0)}%</Badge>
        <Badge>sentence threshold {c.sentence_threshold}</Badge>
        <Badge>{c.embedding_model}</Badge>
        {c.exact_duplicate && <Badge tone="warning">byte-identical files</Badge>}
      </div>
      {c.warnings.map((w) => <Alert key={w} tone="warning">{w}</Alert>)}
      <Alert>{c.disclaimer}</Alert>

      <section>
        <h3 className="mb-2 text-sm font-semibold">Potential conflicts ({c.conflicts.length})</h3>
        {c.conflicts.length ? (
          <Table>
            <thead>
              <tr>
                <Th>Field / kind</Th>
                <Th>{c.file_a.name}</Th>
                <Th>{c.file_b.name}</Th>
              </tr>
            </thead>
            <tbody>
              {c.conflicts.map((x, i) => (
                <tr key={i}>
                  <Td><Badge tone="critical">{x.field ?? "differing figures"}</Badge></Td>
                  <Td><div className="font-medium">{x.value_a}</div><div className="text-xs text-muted">{x.excerpt_a}</div></Td>
                  <Td><div className="font-medium">{x.value_b}</div><div className="text-xs text-muted">{x.excerpt_b}</div></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <p className="text-sm text-ink-2">No conflicting fields or figures detected (detection covers "Key: value" lines and related sentences with different numbers/dates).</p>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section>
          <h3 className="mb-2 text-sm font-semibold">Only in A — {c.file_a.name} ({c.unique_a.length})</h3>
          <ul className="space-y-1.5 text-sm">{c.unique_a.map((u, i) => <li key={i} className="rounded-md bg-surface-2 px-2 py-1">{u.text}</li>)}</ul>
        </section>
        <section>
          <h3 className="mb-2 text-sm font-semibold">Only in B — {c.file_b.name} ({c.unique_b.length})</h3>
          <ul className="space-y-1.5 text-sm">{c.unique_b.map((u, i) => <li key={i} className="rounded-md bg-surface-2 px-2 py-1">{u.text}</li>)}</ul>
        </section>
      </div>

      <section>
        <h3 className="mb-2 text-sm font-semibold">Shared content ({c.shared.length})</h3>
        <Table>
          <thead>
            <tr>
              <Th>A</Th>
              <Th>B</Th>
              <Th align="right">Similarity</Th>
            </tr>
          </thead>
          <tbody>
            {c.shared.map((s, i) => (
              <tr key={i}>
                <Td className="text-xs">{s.a}</Td>
                <Td className="text-xs">{s.exact ? <span className="text-muted">(identical)</span> : s.b}</Td>
                <Td align="right">{s.score.toFixed(3)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </section>
    </div>
  );
}
