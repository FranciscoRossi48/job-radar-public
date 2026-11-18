import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CircleCheckBig, Clock3, Radar, Sparkles, Zap } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { formatNumber } from "../lib/format";
import { ActivityAreaChart } from "../components/Charts";
import { CompactJobList } from "../components/JobTable";
import { Card, ErrorState, PageHeader, Skeleton, StatCard } from "../components/ui";

export default function OverviewPage() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  if (dashboard.isError) return <ErrorState message={dashboard.error.message} retry={() => void dashboard.refetch()} />;
  if (!dashboard.data) return <OverviewSkeleton />;
  const data = dashboard.data;
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Pipeline pulse" title="Your job search command center" description="Your local job search, ranked and ready for action." actions={<Link className="button button--primary button--md" to="/operations"><Zap size={17} aria-hidden="true" /> Run radar</Link>} />

      <div className="stat-grid">
        <StatCard label="New today" value={formatNumber(data.metrics.new_today)} detail={`${formatNumber(data.metrics.total)} in the database`} icon={Radar} />
        <StatCard label="Awaiting evaluation" value={formatNumber(data.metrics.pending)} detail="Ready for the LLM" icon={Clock3} tone="amber" />
        <StatCard label="Strong matches" value={formatNumber(data.metrics.shortlisted)} detail={`Score ${data.metrics.threshold}+`} icon={Sparkles} tone="violet" />
        <StatCard label="Applications" value={formatNumber(data.metrics.applied)} detail="Decisions marked applied" icon={CircleCheckBig} tone="green" />
      </div>

      <div className="overview-grid">
        <Card className="chart-card overview-grid__wide">
          <div className="card-heading"><div><p className="eyebrow">Discovery</p><h2>New jobs over 14 days</h2></div><Link to="/analytics">View analytics <ArrowRight size={15} aria-hidden="true" /></Link></div>
          <div className="chart"><ActivityAreaChart data={data.activity} /></div>
        </Card>

        <Card className="pipeline-card">
          <div className="card-heading"><div><p className="eyebrow">Conversion</p><h2>Search pipeline</h2></div></div>
          <div className="pipeline-list">
            {data.pipeline.map((step, index) => {
              const previous = index === 0 ? step.value : data.pipeline[index - 1]?.value || 1;
              const percentage = index === 0 ? 100 : Math.min(100, Math.round((step.value / previous) * 100));
              return <div className="pipeline-step" key={step.label}><div><span>{step.label}</span><strong>{formatNumber(step.value)}</strong></div><div className="progress-track"><span style={{ width: `${Math.max(step.value ? 5 : 0, percentage)}%` }} /></div>{index > 0 ? <small>{percentage}% from previous</small> : <small>All records</small>}</div>;
            })}
          </div>
        </Card>

        <Card>
          <div className="card-heading"><div><p className="eyebrow">Priority</p><h2>Top matches</h2></div><Link to="/shortlist">Full shortlist <ArrowRight size={15} aria-hidden="true" /></Link></div>
          <CompactJobList jobs={data.top_matches} />
          {data.top_matches.length === 0 ? <p className="muted-block">No jobs have crossed your score threshold yet.</p> : null}
        </Card>

        <Card>
          <div className="card-heading"><div><p className="eyebrow">Fresh arrivals</p><h2>Recently discovered</h2></div><Link to="/jobs">Browse all <ArrowRight size={15} aria-hidden="true" /></Link></div>
          <CompactJobList jobs={data.recent.slice(0, 5)} />
        </Card>
      </div>

      <Card className="country-strip">
        <div className="card-heading"><div><p className="eyebrow">Coverage</p><h2>Country radar</h2></div></div>
        <div className="country-grid">{data.countries.map((item) => <Link to={`/jobs?country=${item.country}`} key={item.country} className="country-item"><span>{item.country}</span><strong>{item.total}</strong><small>{item.pending} pending</small></Link>)}</div>
      </Card>
    </div>
  );
}

function OverviewSkeleton() {
  return <div className="page-stack"><Skeleton className="skeleton--title" /><div className="stat-grid">{Array.from({ length: 4 }, (_, index) => <Skeleton className="skeleton--card" key={index} />)}</div><div className="overview-grid"><Skeleton className="skeleton--panel" /><Skeleton className="skeleton--panel" /></div></div>;
}
