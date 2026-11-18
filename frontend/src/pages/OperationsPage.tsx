import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArchiveRestore,
  Bot,
  CheckCircle2,
  DatabaseBackup,
  FileOutput,
  Filter,
  Gauge,
  Play,
  Radar,
  RefreshCw,
  ScanSearch,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import { queryClient } from "../lib/query";
import { useToast } from "../components/ToastProvider";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button, Card, ErrorState, PageHeader } from "../components/ui";

type OperationName = "daily" | "ingest" | "evaluate" | "evaluate-all" | "reprefilter" | "rescore" | "dedup" | "backup" | "export";
type Confirmation = { name: OperationName; body: object; title: string; description: string; label: string };

export default function OperationsPage() {
  const { notify } = useToast();
  const [evalLimit, setEvalLimit] = useState("25");
  const [skipIngest, setSkipIngest] = useState(false);
  const [skipEvaluate, setSkipEvaluate] = useState(false);
  const [ingestCountries, setIngestCountries] = useState<string[]>([]);
  const [ingestQuery, setIngestQuery] = useState("");
  const [ingestLimit, setIngestLimit] = useState("25");
  const [exportMode, setExportMode] = useState<"new" | "all" | "day">("new");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const status = useQuery({
    queryKey: ["operation-status"],
    queryFn: api.operationStatus,
    refetchInterval: (query) => (query.state.data?.running ? 1_000 : 4_000),
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const mutation = useMutation({
    mutationFn: ({ name, body }: { name: OperationName; body?: object }) => api.operation(name, body),
    onSuccess: (snapshot) => {
      queryClient.setQueryData(["operation-status"], snapshot);
      notify(`${snapshot.task} started`, "success");
    },
    onError: (error) => notify(error.message, "error"),
  });
  const running = status.data?.running || mutation.isPending;
  const run = (name: OperationName, body?: object) => mutation.mutate({ name, body });
  const countries = settings.data?.search.active_countries || [];
  const log = useMemo(() => status.data?.log || [], [status.data?.log]);

  if (status.isError) return <ErrorState message={status.error.message} retry={() => void status.refetch()} />;
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Control plane" title="Operations" description="Run the complete pipeline or one safe step at a time. Only one operation can run concurrently." actions={status.data?.running ? <span className="running-pill running-pill--large"><span aria-hidden="true" /> {status.data.task} in progress</span> : <span className="ready-pill"><CheckCircle2 size={16} aria-hidden="true" /> Ready</span>} />

      <div className="operations-layout">
        <div className="page-stack">
          <Card className="primary-operation">
            <div className="operation-heading"><span className="operation-icon"><Radar size={23} aria-hidden="true" /></span><div><p className="eyebrow">Recommended</p><h2>Run the daily radar</h2><p>Ingest new roles, evaluate a batch, export the shortlist, back up, and notify.</p></div></div>
            <div className="operation-options">
              <label><input type="checkbox" checked={skipIngest} onChange={(event) => setSkipIngest(event.target.checked)} /> Skip ingest</label>
              <label><input type="checkbox" checked={skipEvaluate} onChange={(event) => setSkipEvaluate(event.target.checked)} /> Skip evaluation</label>
              <label><span>Evaluation limit</span><input type="number" min="1" max="500" value={evalLimit} onChange={(event) => setEvalLimit(event.target.value)} /></label>
            </div>
            <Button disabled={running} onClick={() => setConfirmation({ name: "daily", body: { skip_ingest: skipIngest, skip_evaluate: skipEvaluate, eval_limit: evalLimit ? Number(evalLimit) : null }, title: "Start the full daily pipeline?", description: skipEvaluate ? "This will search every configured source and update local exports." : `This will search every configured source and may evaluate up to ${evalLimit || 25} jobs using paid LLM tokens.`, label: "Start full run" })}><Play size={17} aria-hidden="true" /> Start full run</Button>
          </Card>

          <div className="operation-grid">
            <Card className="operation-card">
              <div className="operation-heading"><span className="operation-icon operation-icon--green"><ScanSearch size={20} aria-hidden="true" /></span><div><h2>Ingest jobs</h2><p>Search sources without calling the LLM.</p></div></div>
              <fieldset><legend>Countries</legend><div className="country-picks">{countries.map((country) => <label key={country}><input type="checkbox" checked={ingestCountries.includes(country)} onChange={(event) => setIngestCountries((current) => event.target.checked ? [...current, country] : current.filter((item) => item !== country))} /> {country}</label>)}</div></fieldset>
              <label><span>Override query (optional)</span><input value={ingestQuery} onChange={(event) => setIngestQuery(event.target.value)} placeholder="e.g. data engineering internship" /></label>
              <label><span>Results per call</span><input type="number" min="1" max="500" value={ingestLimit} onChange={(event) => setIngestLimit(event.target.value)} /></label>
              <Button variant="secondary" disabled={running} onClick={() => run("ingest", { countries: ingestCountries, queries: ingestQuery.trim() ? [ingestQuery.trim()] : [], limit: Number(ingestLimit) || null, sources: [] })}><ScanSearch size={16} aria-hidden="true" /> Start ingest</Button>
            </Card>

            <Card className="operation-card">
              <div className="operation-heading"><span className="operation-icon operation-icon--violet"><Bot size={20} aria-hidden="true" /></span><div><h2>Evaluate pending</h2><p>Score the newest eligible jobs with {settings.data?.llm.provider || "the configured LLM"}.</p></div></div>
              <label><span>Maximum jobs</span><input type="number" min="1" max="500" value={evalLimit} onChange={(event) => setEvalLimit(event.target.value)} /></label>
              <p className="operation-note"><Gauge size={15} aria-hidden="true" /> This can consume paid tokens. The provider limit still applies.</p>
              <div className="operation-card__actions"><Button variant="secondary" disabled={running} onClick={() => setConfirmation({ name: "evaluate", body: { limit: Number(evalLimit) || 25 }, title: "Evaluate pending jobs?", description: `This may send up to ${Number(evalLimit) || 25} condensed job descriptions to ${settings.data?.llm.provider || "your LLM provider"} and consume paid tokens.`, label: "Evaluate batch" })}><Bot size={16} aria-hidden="true" /> Evaluate batch</Button><Button variant="ghost" disabled={running} onClick={() => setConfirmation({ name: "evaluate-all", body: { max_jobs: 500 }, title: "Evaluate and rank every pending job?", description: `This repeatedly runs provider-sized batches until the queue is empty or 500 jobs are attempted. It may take a long time and consume substantial paid tokens. Zero CV-overlap roles will be excluded with score 0.`, label: "Evaluate all" })}>Evaluate & rank all</Button></div>
            </Card>

            <Card className="operation-card">
              <div className="operation-heading"><span className="operation-icon operation-icon--amber"><FileOutput size={20} aria-hidden="true" /></span><div><h2>Create export</h2><p>Generate a CSV using your current score threshold.</p></div></div>
              <label><span>Export scope</span><select value={exportMode} onChange={(event) => setExportMode(event.target.value as typeof exportMode)}><option value="new">New shortlist entries</option><option value="all">Full historical ranking</option><option value="day">Evaluated today</option></select></label>
              <Button variant="secondary" disabled={running} onClick={() => run("export", { mode: exportMode })}><FileOutput size={16} aria-hidden="true" /> Generate CSV</Button>
            </Card>
          </div>

          <Card>
            <div className="card-heading"><div><p className="eyebrow">Maintenance</p><h2>Deterministic tools</h2></div></div>
            <div className="maintenance-grid">
              <MaintenanceButton icon={Filter} label="Reprefilter" detail="Apply current title and language rules" disabled={running} onClick={() => run("reprefilter")} />
              <MaintenanceButton icon={RefreshCw} label="Rescore" detail="Recalculate scores without an LLM call" disabled={running} onClick={() => run("rescore")} />
              <MaintenanceButton icon={Wrench} label="Deduplicate" detail="Backfill hashes and merge duplicate ads" disabled={running} onClick={() => run("dedup")} />
              <MaintenanceButton icon={DatabaseBackup} label="Back up DB" detail="Copy a compressed backup to mirrors" disabled={running} onClick={() => run("backup")} />
            </div>
          </Card>
        </div>

        <aside className="run-console">
          <Card>
            <div className="console-heading"><div><TerminalSquare size={18} aria-hidden="true" /><h2>Live output</h2></div>{status.data?.started_at ? <time>{formatDate(status.data.started_at, true)}</time> : null}</div>
            <div className="terminal" role="log" aria-live="polite" aria-label="Operation output">
              {log.length ? log.map((line, index) => <div key={`${line}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span>{line}</div>) : <p>No operation output yet. Start a task to stream logs here.</p>}
            </div>
            {status.data?.error ? <div className="console-error" role="alert">{status.data.error}</div> : null}
            {status.data?.result && !status.data.running ? <details className="result-details"><summary>Last result</summary><pre>{JSON.stringify(status.data.result, null, 2)}</pre></details> : null}
          </Card>
          {status.data?.history?.length ? <Card><div className="card-heading"><div><p className="eyebrow">This session</p><h2>Recent tasks</h2></div></div><div className="task-history">{status.data.history.slice(0, 5).map((item) => <div key={item.id || item.started_at || item.task}><span className={item.error ? "task-dot task-dot--error" : "task-dot"} /><div><strong>{item.task}</strong><small>{formatDate(item.finished_at, true)}</small></div></div>)}</div></Card> : null}
        </aside>
      </div>
      <ConfirmDialog open={Boolean(confirmation)} title={confirmation?.title || "Confirm operation"} description={confirmation?.description || ""} confirmLabel={confirmation?.label || "Continue"} onClose={() => setConfirmation(null)} onConfirm={() => { if (confirmation) run(confirmation.name, confirmation.body); }} />
    </div>
  );
}

function MaintenanceButton({ icon: Icon, label, detail, disabled, onClick }: { icon: typeof ArchiveRestore; label: string; detail: string; disabled: boolean; onClick: () => void }) {
  return <button className="maintenance-button" disabled={disabled} onClick={onClick}><span><Icon size={18} aria-hidden="true" /></span><div><strong>{label}</strong><small>{detail}</small></div></button>;
}
