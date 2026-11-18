import { ArrowUpRight, Building2, CalendarDays, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import { formatRelative } from "../lib/format";
import type { JobSummary } from "../lib/types";
import { Badge, EmptyState, JobTitleLink, Score, Skeleton, StatusBadge } from "./ui";
import { BriefcaseBusiness } from "lucide-react";

function JobMeta({ job }: { job: JobSummary }) {
  return (
    <div className="job-meta">
      <span><Building2 size={14} aria-hidden="true" />{job.company || "Unknown company"}</span>
      <span><MapPin size={14} aria-hidden="true" />{[job.location, job.country].filter(Boolean).join(" · ") || "Location unknown"}</span>
      <span><CalendarDays size={14} aria-hidden="true" />{formatRelative(job.first_seen)}</span>
    </div>
  );
}

export function JobTable({ jobs, loading = false }: { jobs: JobSummary[]; loading?: boolean }) {
  if (loading && jobs.length === 0) {
    return <div className="table-skeleton">{Array.from({ length: 7 }, (_, index) => <Skeleton key={index} />)}</div>;
  }
  if (jobs.length === 0) {
    return <EmptyState icon={BriefcaseBusiness} title="No jobs match these filters" description="Try widening the score, country, or status filters." />;
  }
  return (
    <div className="jobs-view" aria-busy={loading}>
      <div className="table-scroll">
        <table className="jobs-table">
          <thead><tr><th scope="col">Score</th><th scope="col">Role</th><th scope="col">Country</th><th scope="col">Status</th><th scope="col">Seen</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td><Score value={job.score} /></td>
                <td className="job-main-cell">
                  <JobTitleLink id={job.id}>{job.title}</JobTitleLink>
                  <JobMeta job={job} />
                  <div className="chip-row">
                    {job.features.role_family ? <Badge>{job.features.role_family}</Badge> : null}
                    {job.features.seniority ? <Badge>{job.features.seniority}</Badge> : null}
                    {(job.features.stack_match || []).slice(0, 3).map((item) => <Badge key={item}>{item}</Badge>)}
                  </div>
                </td>
                <td><span className="country-code">{job.country || "—"}</span></td>
                <td><div className="status-stack"><StatusBadge status={job.decision || (job.fit_excluded ? "excluded_fit" : job.status)} />{job.fit_exclusion_reasons.length ? <small title={job.fit_exclusion_reasons.join("; ")}>{job.fit_exclusion_reasons.join("; ")}</small> : job.exclude_reason ? <small title={job.exclude_reason}>{job.exclude_reason}</small> : null}</div></td>
                <td className="nowrap">{formatRelative(job.first_seen)}</td>
                <td><Link className="table-open" to={`/jobs/${encodeURIComponent(job.id)}`} aria-label={`Open ${job.title}`}><ArrowUpRight size={17} aria-hidden="true" /></Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="job-card-list">
        {jobs.map((job) => (
          <article className="job-card" key={job.id}>
            <div className="job-card__header"><Score value={job.score} /><StatusBadge status={job.decision || (job.fit_excluded ? "excluded_fit" : job.status)} /></div>
            <JobTitleLink id={job.id}>{job.title}</JobTitleLink>
            <JobMeta job={job} />
            <div className="chip-row">
              {job.features.role_family ? <Badge>{job.features.role_family}</Badge> : null}
              {(job.features.stack_match || []).slice(0, 2).map((item) => <Badge key={item}>{item}</Badge>)}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

export function CompactJobList({ jobs }: { jobs: JobSummary[] }) {
  return <div className="compact-job-list">{jobs.map((job) => (
    <Link to={`/jobs/${encodeURIComponent(job.id)}`} className="compact-job" key={job.id}>
      <Score value={job.score} />
      <div><strong>{job.title}</strong><span>{job.company || "Unknown company"} · {job.country || "?"}</span></div>
      <ArrowUpRight size={17} aria-hidden="true" />
    </Link>
  ))}</div>;
}
