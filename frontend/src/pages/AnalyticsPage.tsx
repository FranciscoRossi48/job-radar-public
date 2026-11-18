import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, CircleX, Coins, Cpu, Database, Gauge } from "lucide-react";
import { api } from "../lib/api";
import { formatCompact, formatDate, formatNumber } from "../lib/format";
import { TokenBarChart } from "../components/Charts";
import { Card, ErrorState, PageHeader, StatCard } from "../components/ui";

export default function AnalyticsPage() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  const usage = useQuery({ queryKey: ["usage", 30], queryFn: () => api.usage(30) });
  const runs = useQuery({ queryKey: ["runs", 30], queryFn: () => api.runs(30) });
  const error = dashboard.error || usage.error || runs.error;
  if (error) return <ErrorState message={error.message} retry={() => { void dashboard.refetch(); void usage.refetch(); void runs.refetch(); }} />;
  const usageRows = usage.data?.rows || [];
  const runItems = runs.data?.items || [];
  const successful = runItems.filter((run) => {
    const totals = run.totals as { failed_steps?: unknown[] } | undefined;
    return (totals?.failed_steps?.length || 0) === 0;
  }).length;
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Observability" title="Analytics & run history" description="See source coverage, scoring throughput, token usage, and pipeline reliability." />
      <div className="stat-grid">
        <StatCard label="LLM requests" value={formatNumber(usage.data?.totals.requests)} detail="Last 30 days" icon={Cpu} />
        <StatCard label="Input tokens" value={formatCompact(usage.data?.totals.tokens_in || 0)} detail={`${formatCompact(usage.data?.totals.tokens_out || 0)} output`} icon={Coins} tone="violet" />
        <StatCard label="Successful runs" value={`${successful}/${runItems.length}`} detail="Recent run history" icon={CheckCircle2} tone="green" />
        <StatCard label="Database jobs" value={formatNumber(dashboard.data?.metrics.total)} detail={`${formatNumber(dashboard.data?.metrics.evaluated)} evaluated`} icon={Database} tone="amber" />
      </div>

      <div className="analytics-grid">
        <Card className="chart-card analytics-grid__wide">
          <div className="card-heading"><div><p className="eyebrow">Scoring cost</p><h2>Token usage by day</h2></div></div>
          <div className="chart"><TokenBarChart data={[...usageRows].reverse()} /></div>
        </Card>

        <Card>
          <div className="card-heading"><div><p className="eyebrow">Distribution</p><h2>Scores</h2></div></div>
          <div className="score-distribution">{dashboard.data?.score_buckets.map((bucket) => { const max = Math.max(...(dashboard.data?.score_buckets.map((item) => item.count) || [1]), 1); return <div key={bucket.label}><div><span>{bucket.label}</span><strong>{bucket.count}</strong></div><div className="progress-track"><span style={{ width: `${(bucket.count / max) * 100}%` }} /></div></div>; })}</div>
        </Card>

        <Card>
          <div className="card-heading"><div><p className="eyebrow">Sources</p><h2>Coverage</h2></div></div>
          <div className="rank-list">{dashboard.data?.sources.slice(0, 9).map((source, index) => <div key={source.source}><span>{index + 1}</span><strong>{source.source}</strong><em>{source.total}</em></div>)}</div>
        </Card>
      </div>

      <Card>
        <div className="card-heading"><div><p className="eyebrow">Reliability</p><h2>Daily runs</h2></div><span className="muted-text">Newest first</span></div>
        <div className="run-list">
          {runItems.length ? runItems.map((run, index) => {
            const totals = (run.totals || {}) as Record<string, number | string[]>;
            const failures = (totals.failed_steps as string[] | undefined) || [];
            return <details key={String(run.started || index)} className="run-item"><summary><span className={`run-status ${failures.length ? "run-status--error" : ""}`}>{failures.length ? <CircleX size={17} aria-hidden="true" /> : <CheckCircle2 size={17} aria-hidden="true" />}</span><div><strong>{formatDate(String(run.started || ""), true)}</strong><small>{Number(run.seconds || 0).toFixed(0)} seconds</small></div><div className="run-kpis"><span><Activity size={14} aria-hidden="true" /> {String(totals.new ?? 0)} new</span><span><Gauge size={14} aria-hidden="true" /> {String(totals.evaluated ?? 0)} evaluated</span><span>{String(totals.shortlist ?? 0)} shortlisted</span></div></summary><pre>{JSON.stringify(run, null, 2)}</pre></details>;
          }) : <p className="muted-block">No daily run summaries yet.</p>}
        </div>
      </Card>

      <Card>
        <div className="card-heading"><div><p className="eyebrow">Requests</p><h2>LLM usage details</h2></div></div>
        <div className="table-scroll"><table><thead><tr><th>Day</th><th>Provider</th><th>Model</th><th>Requests</th><th>Success</th><th>Input</th><th>Output</th></tr></thead><tbody>{usageRows.map((row) => <tr key={`${row.day}-${row.provider}-${row.model}`}><td>{row.day}</td><td>{row.provider}</td><td className="code-cell">{row.model}</td><td>{formatNumber(row.requests)}</td><td>{formatNumber(row.ok)}</td><td>{formatNumber(row.tokens_in)}</td><td>{formatNumber(row.tokens_out)}</td></tr>)}</tbody></table></div>
      </Card>
    </div>
  );
}
