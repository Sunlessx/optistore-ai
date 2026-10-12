import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { LocationSelect } from "../components/locations";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, Field, PageHeader, Select, Spinner, StatTile, StatusBadge } from "../components/ui";
import { cn } from "../lib/cn";
import { api, post } from "../lib/api";
import { formatBytes, formatDate, formatUsd } from "../lib/format";
import type { Plan, PlanAction } from "../lib/types";

const TYPE_LABEL: Record<string, string> = {
  review_duplicate_group: "Duplicate review (manual)",
  create_consolidation_draft: "Consolidation draft",
  create_compressed_copy: "Compressed copy",
  s3_change_storage_class: "S3 storage class change",
  storage_class_simulation: "Simulation (info)",
};

export function PlansPage() {
  const qc = useQueryClient();
  const [strategy, setStrategy] = useState("balanced");
  const [locationId, setLocationId] = useState<number>();
  const [selected, setSelected] = useState<number | null>(null);
  const plans = useQuery({ queryKey: ["plans"], queryFn: () => api<Plan[]>("/plans") });
  const create = useMutation({
    mutationFn: () => post<Plan>("/plans", { strategy, location_id: locationId }),
    onSuccess: (plan) => {
      qc.invalidateQueries({ queryKey: ["plans"] });
      setSelected(plan.id);
    },
  });
  const activeId = selected ?? plans.data?.[0]?.id ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Optimisation plans"
        description="A plan combines every analysis into prioritised actions with evidence, expected benefit, risks and reversibility. Only predefined action types exist; nothing runs until you approve it, and every decision is logged."
      />
      <Card title="Generate a plan" description="Runs duplicate, similarity, compression (benchmarking files not yet measured), prediction and cost analyses. May take a moment on large inventories.">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Strategy">
            <Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="conservative">Conservative</option>
              <option value="balanced">Balanced</option>
              <option value="aggressive">Aggressive</option>
            </Select>
          </Field>
          <Field label="Scope"><LocationSelect value={locationId} onChange={setLocationId} /></Field>
          <Button variant="primary" onClick={() => create.mutate()} loading={create.isPending}>Generate plan</Button>
          {plans.data && plans.data.length > 0 && (
            <Field label="Open plan">
              <Select value={activeId ?? ""} onChange={(e) => setSelected(Number(e.target.value))}>
                {plans.data.map((p) => (
                  <option key={p.id} value={p.id}>#{p.id} · {p.strategy} · {p.status} · {formatDate(p.created_at)}</option>
                ))}
              </Select>
            </Field>
          )}
        </div>
        {create.error && <div className="mt-3"><ErrorState error={create.error} /></div>}
      </Card>
      {plans.isLoading ? <Spinner /> : activeId ? <PlanView id={activeId} /> : <EmptyState title="No plans yet" description="Scan a location, then generate a plan." />}
    </div>
  );
}

function PlanView({ id }: { id: number }) {
  const qc = useQueryClient();
  const plan = useQuery({ queryKey: ["plan", id], queryFn: () => api<Plan>(`/plans/${id}`) });
  const executeAll = useMutation({
    mutationFn: (dryRun: boolean) => post<Plan>(`/plans/${id}/execute`, { dry_run: dryRun }),
    onSuccess: (p) => {
      qc.setQueryData(["plan", id], p);
      qc.invalidateQueries({ queryKey: ["plans"] });
    },
  });
  if (plan.isLoading) return <Spinner />;
  if (plan.error) return <ErrorState error={plan.error} />;
  const p = plan.data!;
  const s = p.summary;
  const approved = p.actions?.filter((a) => a.status === "approved").length ?? 0;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Inventory in scope" value={formatBytes(s.inventory?.bytes)} hint={`${s.inventory?.files} files`} />
        <StatTile label="Verified duplicate bytes" value={formatBytes(s.savings?.verified_duplicate_bytes)} hint="SHA-256 identical copies" />
        <StatTile label="Measured compression saving" value={formatBytes(s.savings?.measured_compression_bytes)} hint="From actual benchmark runs" />
        <StatTile label="Estimated class-change saving" value={formatUsd(s.savings?.estimated_storage_class_saving_usd)} hint={`${s.storage_classes?.horizon_months} months, ${s.storage_classes?.region} (estimate)`} />
      </div>
      <Card
        title={`Plan #${p.id} — ${p.strategy}`}
        description={`${p.actions?.length ?? 0} actions · potential consolidation ${formatBytes(s.savings?.potential_consolidation_bytes)} (only if sources were later replaced) · predictions: ${Object.entries(s.predictions ?? {}).map(([k, v]) => `${v} ${k}`).join(", ") || "none"}`}
        actions={
          <>
            <StatusBadge status={p.status} />
            <Button size="sm" onClick={() => executeAll.mutate(true)} loading={executeAll.isPending && executeAll.variables === true} disabled={!approved}>Dry-run approved ({approved})</Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!approved}
              loading={executeAll.isPending && executeAll.variables === false}
              onClick={() => window.confirm(`Execute ${approved} approved action(s)?`) && executeAll.mutate(false)}
            >
              Execute approved
            </Button>
          </>
        }
      >
        {executeAll.error && <ErrorState error={executeAll.error} />}
        {!p.actions?.length ? (
          <EmptyState title="Nothing to optimise" description="No duplicates, worthwhile compression, consolidation candidates or class changes were found." />
        ) : (
          <ol className="space-y-3">
            {p.actions.map((a) => <ActionCard key={a.id} action={a} planId={p.id} />)}
          </ol>
        )}
      </Card>
    </div>
  );
}

function ActionCard({ action: a, planId }: { action: PlanAction; planId: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [ack, setAck] = useState(false);
  const needsAck = a.action_type === "s3_change_storage_class" || (a.action_type === "create_compressed_copy" && a.evidence.lossless === false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["plan", planId] });
  const decide = useMutation({ mutationFn: (approve: boolean) => post(`/plans/${planId}/actions/${a.id}/decision`, { approve, acknowledge_risks: ack }), onSuccess: refresh });
  const execute = useMutation({ mutationFn: (dryRun: boolean) => post(`/plans/${planId}/actions/${a.id}/execute`, { dry_run: dryRun }), onSuccess: refresh });
  const final = ["executed", "rejected", "acknowledged"].includes(a.status);

  return (
    <li className={cn("rounded-lg border border-line", a.status === "failed" && "border-critical/50")}>
      <div className="flex flex-wrap items-start gap-3 p-3">
        <span className="mt-0.5 w-6 text-xs text-muted tabular">#{a.priority}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{a.title}</span>
            <Badge>{TYPE_LABEL[a.action_type] ?? a.action_type}</Badge>
            <StatusBadge status={a.risk_level} label={`${a.risk_level} risk`} />
            <StatusBadge status={a.status} />
          </div>
          <p className="mt-1 text-sm text-ink-2">{a.reason}</p>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
            {a.expected_bytes_saved > 0 && <span>Bytes: {formatBytes(a.expected_bytes_saved)}</span>}
            <span>≈ {formatUsd(a.expected_monthly_savings_usd)}/month</span>
            {a.estimated_cost_usd > 0 && <span>One-time cost {formatUsd(a.estimated_cost_usd)}</span>}
            <span>{a.reversible ? "Reversible" : "Hard to reverse"}: {a.reversibility_note}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!final && a.status !== "approved" && (
            <>
              {needsAck && (
                <label className="flex items-center gap-1.5 text-xs text-ink-2">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I accept the listed risks
                </label>
              )}
              <Button size="sm" variant="primary" onClick={() => decide.mutate(true)} loading={decide.isPending && decide.variables} disabled={needsAck && !ack}>
                {a.executable ? "Approve" : "Mark reviewed"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => decide.mutate(false)}>Reject</Button>
            </>
          )}
          {a.status === "approved" && a.executable && (
            <>
              <Button size="sm" onClick={() => execute.mutate(true)} loading={execute.isPending && execute.variables === true}>Dry run</Button>
              <Button size="sm" variant="primary" onClick={() => execute.mutate(false)} loading={execute.isPending && execute.variables === false}>Execute</Button>
            </>
          )}
          <Button size="sm" variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />} Evidence
          </Button>
        </div>
      </div>
      {(decide.error || execute.error) && <div className="px-3 pb-3"><ErrorState error={decide.error ?? execute.error} /></div>}
      {Object.keys(a.result).length > 0 && (
        <div className="px-3 pb-3">
          <Alert tone={a.result.error ? "critical" : "good"} title={a.result.dry_run ? "Dry-run result" : a.result.error ? "Execution failed" : "Result"}>
            <pre className="text-xs whitespace-pre-wrap">{JSON.stringify(a.result, null, 2)}</pre>
          </Alert>
        </div>
      )}
      {open && (
        <div className="space-y-2 border-t border-line bg-surface-2/50 p-3 text-xs">
          {a.risks.length > 0 && (
            <div>
              <div className="font-semibold">Risks</div>
              <ul className="list-disc pl-5 text-ink-2">{a.risks.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
          )}
          <div className="font-semibold">Evidence</div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-ink-2">{JSON.stringify(a.evidence, null, 2)}</pre>
        </div>
      )}
    </li>
  );
}
