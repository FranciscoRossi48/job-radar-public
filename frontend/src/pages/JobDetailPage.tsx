import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  Ban,
  BriefcaseBusiness,
  Building2,
  CalendarDays,
  Check,
  CircleAlert,
  Clock3,
  ExternalLink,
  FileText,
  Languages,
  MapPin,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import { queryClient } from "../lib/query";
import type { ScoreBreakdown } from "../lib/types";
import { useToast } from "../components/ToastProvider";
import { Badge, Button, Card, ErrorState, Score, Skeleton, StatusBadge } from "../components/ui";

export default function JobDetailPage() {
  const { id = "" } = useParams();
  const { notify } = useToast();
  const [note, setNote] = useState<string | null>(null);
  const job = useQuery({ queryKey: ["job", id], queryFn: () => api.job(id), enabled: Boolean(id) });
  const decide = useMutation({
    mutationFn: ({ decision }: { decision: "applied" | "discarded" | null }) =>
      api.decide(id, decision, note ?? job.data?.decision_note ?? ""),
    onSuccess: (updated) => {
      queryClient.setQueryData(["job", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      notify(updated.decision ? `Marked ${updated.decision}` : "Decision cleared", "success");
    },
    onError: (error) => notify(error.message, "error"),
  });

  if (job.isError) return <ErrorState message={job.error.message} retry={() => void job.refetch()} />;
  if (!job.data) return <DetailSkeleton />;
  const item = job.data;
  const evaluation = item.current_evaluation;
  const features = evaluation?.features || {};
  const positive = (evaluation?.score_breakdown || []).filter((part) => (part.points || 0) > 0);
  const negative = (evaluation?.score_breakdown || []).filter((part) => (part.points || 0) < 0 || part.exclude);
  const fitExcluded = negative.some((part) => Boolean(part.exclude));

  return (
    <div className="page-stack detail-page">
      <div className="detail-back"><Link to="/jobs"><ArrowLeft size={17} aria-hidden="true" /> Back to jobs</Link></div>
      <header className="detail-hero">
        <div className="detail-hero__main">
          <div className="detail-brand"><BriefcaseBusiness size={24} aria-hidden="true" /></div>
          <div>
            <div className="detail-badges"><StatusBadge status={item.decision || (fitExcluded ? "excluded_fit" : item.status)} />{item.country ? <Badge tone="info">{item.country}</Badge> : null}{features.seniority ? <Badge>{features.seniority}</Badge> : null}</div>
            <h1>{item.title}</h1>
            <div className="detail-meta"><span><Building2 size={16} aria-hidden="true" />{item.company || "Unknown company"}</span><span><MapPin size={16} aria-hidden="true" />{item.location || "Location unknown"}</span><span><CalendarDays size={16} aria-hidden="true" />Seen {formatDate(item.first_seen)}</span></div>
          </div>
        </div>
        <div className="detail-hero__actions">
          <div className="score-panel"><Score value={evaluation?.score_computed ?? null} large /><span>match score</span></div>
          <a className="button button--secondary button--md" href={item.url} target="_blank" rel="noreferrer"><ExternalLink size={17} aria-hidden="true" /> Open original</a>
        </div>
      </header>

      <div className="detail-layout">
        <div className="detail-main page-stack">
          {evaluation ? (
            <Card className="fit-card">
              <div className="section-title"><span><Sparkles size={18} aria-hidden="true" /></span><div><h2>Why this role fits</h2><p>{evaluation.evaluator} · {evaluation.model || "manual"} · {formatDate(evaluation.created_at, true)}</p></div></div>
              <p className="rationale">{evaluation.rationale || "No rationale was provided."}</p>
              <div className="breakdown-columns">
                <Breakdown title="Score boosters" items={positive} positive />
                <Breakdown title="Trade-offs" items={negative} />
              </div>
            </Card>
          ) : (
            <Card className="unevaluated"><Clock3 size={22} aria-hidden="true" /><div><h2>Waiting for evaluation</h2><p>This role is pending. Run an evaluation batch from Operations to calculate its fit.</p></div><Link className="button button--secondary button--sm" to="/operations">Open operations</Link></Card>
          )}

          <Card>
            <div className="section-title"><span><FileText size={18} aria-hidden="true" /></span><div><h2>Job description</h2><p>{item.desc_lang ? `Detected language: ${item.desc_lang.toUpperCase()}` : "Language not detected"}</p></div></div>
            <div className="job-description">{item.description_raw || item.desc_condensed || "No description was captured for this listing."}</div>
          </Card>

          {item.evaluations.length > 1 ? <Card><div className="section-title"><span><Clock3 size={18} aria-hidden="true" /></span><div><h2>Evaluation history</h2><p>{item.evaluations.length} saved evaluations</p></div></div><div className="evaluation-history">{item.evaluations.map((entry) => <div key={entry.id}><Score value={entry.score_computed} /><div><strong>{entry.evaluator} · {entry.model || "manual"}</strong><span>{entry.rationale || "No rationale"}</span></div><time>{formatDate(entry.created_at, true)}</time></div>)}</div></Card> : null}
        </div>

        <aside className="detail-sidebar page-stack">
          <Card className="decision-card">
            <h2>Your decision</h2>
            <p>Track this role locally. This never contacts the employer.</p>
            <label htmlFor="decision-note">Private note</label>
            <textarea id="decision-note" value={note ?? item.decision_note ?? ""} onChange={(event) => setNote(event.target.value)} placeholder="Application date, recruiter, next step…" rows={4} />
            <div className="decision-actions">
              <Button loading={decide.isPending} onClick={() => decide.mutate({ decision: "applied" })}><Check size={17} aria-hidden="true" /> Applied</Button>
              <Button loading={decide.isPending} variant="danger" onClick={() => decide.mutate({ decision: "discarded" })}><Ban size={17} aria-hidden="true" /> Discard</Button>
              {item.decision ? <Button loading={decide.isPending} variant="ghost" onClick={() => decide.mutate({ decision: null })}><RotateCcw size={17} aria-hidden="true" /> Clear</Button> : null}
            </div>
          </Card>

          <Card>
            <h2>Role signals</h2>
            <dl className="signal-list"><div><dt>Role family</dt><dd>{features.role_family || "Unknown"}</dd></div><div><dt>Seniority</dt><dd>{features.seniority || "Unknown"}</dd></div><div><dt>Work mode</dt><dd>{features.onsite_only ? "On-site only" : "Flexible / unspecified"}</dd></div><div><dt>Source</dt><dd>{item.source}</dd></div><div><dt>Posted</dt><dd>{formatDate(item.posted_at)}</dd></div></dl>
          </Card>

          {(features.stack_match || []).length > 0 ? <Card><h2>Stack match</h2><div className="chip-row chip-row--roomy">{features.stack_match?.map((skill) => <Badge tone="success" key={skill}>{skill}</Badge>)}</div></Card> : null}
          {(features.lang_required || []).length > 0 ? <Card><div className="mini-heading"><Languages size={17} aria-hidden="true" /><h2>Languages</h2></div><div className="language-list">{features.lang_required?.map((language, index) => <div key={`${language.lang}-${index}`}><strong>{language.lang.toUpperCase()}</strong><span>{language.level}{language.cefr ? ` · ${language.cefr}` : ""}</span></div>)}</div></Card> : null}
          {(features.red_flags || []).length > 0 ? <Card className="red-flags"><div className="mini-heading"><CircleAlert size={17} aria-hidden="true" /><h2>Red flags</h2></div><ul>{features.red_flags?.map((flag) => <li key={flag}>{flag}</li>)}</ul></Card> : null}
          {item.exclude_reason ? <Card className="red-flags"><h2>Prefilter reason</h2><p>{item.exclude_reason}</p></Card> : null}
          {item.duplicate ? <Card><h2>Duplicate of</h2><Link className="inline-link" to={`/jobs/${encodeURIComponent(item.duplicate.id)}`}>{item.duplicate.title} <ArrowUpRight size={15} aria-hidden="true" /></Link></Card> : null}
        </aside>
      </div>
    </div>
  );
}

function Breakdown({ title, items, positive = false }: { title: string; items: ScoreBreakdown[]; positive?: boolean }) {
  return <div className={`breakdown breakdown--${positive ? "positive" : "negative"}`}><h3>{title}</h3>{items.length ? items.map((item, index) => <div key={`${item.rule || item.exclude}-${index}`}><span>{item.rule || item.exclude}</span>{item.points !== undefined ? <strong>{item.points > 0 ? "+" : ""}{item.points}</strong> : null}</div>) : <p>None detected</p>}</div>;
}

function DetailSkeleton() {
  return <div className="page-stack"><Skeleton className="skeleton--title" /><Skeleton className="skeleton--hero" /><div className="detail-layout"><Skeleton className="skeleton--panel" /><Skeleton className="skeleton--panel" /></div></div>;
}
