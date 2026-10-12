import { useMutation, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, Input, PageHeader, Select, Spinner } from "../components/ui";
import { api, post, qs } from "../lib/api";

interface SearchResult {
  file_id: number;
  name: string;
  path: string;
  category: string;
  score: number;
  semantic_score: number;
  keyword_score: number;
  title_score: number;
  excerpt: string;
  chunk_index: number;
}

const EXAMPLES = [
  "Find the files discussing the project budget",
  "Show me documents related to customer complaints",
  "Find the contract renewal conditions",
  "Locate the October meeting notes",
];

export function SearchPage() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const results = useQuery({
    queryKey: ["search", query, category],
    queryFn: () => api<{ results: SearchResult[]; embedding_backend: string | null; embedding_model: string | null }>(`/search${qs({ q: query, category, limit: 15 })}`),
    enabled: query.length >= 2,
  });
  const reindex = useMutation({
    mutationFn: (force: boolean) => post<{ indexed_files: number; chunks: number; similarity_pairs: number }>(`/search/reindex${qs({ force })}`),
    onSuccess: () => results.refetch(),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setQuery(input.trim());
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Semantic search"
        description="Natural-language search over extracted document text. Runs locally: no document content is sent to external services."
        actions={
          <>
            <Button onClick={() => reindex.mutate(false)} loading={reindex.isPending && !reindex.variables}>Index new / changed</Button>
            <Button onClick={() => reindex.mutate(true)} loading={reindex.isPending && reindex.variables === true}>Full reindex</Button>
          </>
        }
      />
      {reindex.data && <Alert tone="good">Indexed {reindex.data.indexed_files} files ({reindex.data.chunks} chunks); {reindex.data.similarity_pairs} similarity pairs recomputed.</Alert>}
      {reindex.error && <ErrorState error={reindex.error} />}
      <Card>
        <form onSubmit={submit} className="flex flex-wrap gap-2">
          <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="e.g. Find the contract renewal conditions" className="min-w-64 flex-1" aria-label="Search query" />
          <Select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
            <option value="">All types</option>
            <option value="document">Documents</option>
            <option value="data">Data (CSV/JSON)</option>
          </Select>
          <Button variant="primary" type="submit" disabled={input.trim().length < 2}>
            <Search className="size-4" /> Search
          </Button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button key={ex} className="rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:bg-surface-2" onClick={() => { setInput(ex); setQuery(ex); }}>
              {ex}
            </button>
          ))}
        </div>
      </Card>
      {query && (
        <Card
          title={`Results for “${query}”`}
          description="score = 0.55 × semantic + 0.20 × keyword (TF-IDF) + 0.25 × file-name match; best matching section per file."
          actions={results.data?.embedding_backend === "hashing" && <Badge tone="warning">lexical fallback embeddings</Badge>}
        >
          {results.isLoading ? (
            <Spinner label="Searching…" />
          ) : results.error ? (
            <ErrorState error={results.error} />
          ) : !results.data?.results.length ? (
            <EmptyState title="No relevant documents" description="Try different wording, or index documents by scanning a location." />
          ) : (
            <ol className="space-y-3">
              {results.data.results.map((r, i) => (
                <li key={r.file_id} className="rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted">{i + 1}.</span>
                    <span className="font-medium">{r.name}</span>
                    <Badge>{r.category}</Badge>
                    <span className="ml-auto text-xs text-ink-2 tabular" title={`semantic ${r.semantic_score} · keyword ${r.keyword_score} · title ${r.title_score}`}>
                      relevance {r.score.toFixed(3)}
                    </span>
                  </div>
                  <div className="font-mono text-xs text-muted">{r.path} · section {r.chunk_index + 1}</div>
                  <p className="mt-2 text-sm text-ink-2">{r.excerpt}</p>
                  <div className="mt-1 flex gap-3 text-[11px] text-muted tabular">
                    <span>semantic {r.semantic_score.toFixed(3)}</span>
                    <span>keyword {r.keyword_score.toFixed(3)}</span>
                    <span>file name {r.title_score.toFixed(2)}</span>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      )}
    </div>
  );
}
