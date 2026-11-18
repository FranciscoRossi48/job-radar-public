import { useMutation, useQuery } from "@tanstack/react-query";
import {
  BrainCircuit,
  Check,
  ChevronDown,
  CircleAlert,
  FileCheck2,
  Plus,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { formatNumber } from "../lib/format";
import { queryClient } from "../lib/query";
import type { CvLanguage, CvPreview, CvProfileResult, ProfileDraft } from "../lib/types";
import { useToast } from "./ToastProvider";
import { ConfirmDialog } from "./ConfirmDialog";
import { Badge, Button, Card } from "./ui";

const levels: CvLanguage["level"][] = ["A1", "A2", "B1", "B2", "C1", "C2"];

function isCvResult(value: unknown): value is CvProfileResult {
  return Boolean(
    value &&
      typeof value === "object" &&
      "draft" in value &&
      typeof (value as { draft?: unknown }).draft === "object",
  );
}

export function CvProfileImporter() {
  const { notify } = useToast();
  const input = useRef<HTMLInputElement>(null);
  const handledRun = useRef<string | null>(null);
  const [preview, setPreview] = useState<CvPreview | null>(null);
  const [consent, setConsent] = useState(false);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [applyResult, setApplyResult] = useState<{ rescored: number; pending: number } | null>(null);
  const [confirmEvaluate, setConfirmEvaluate] = useState(false);
  const operation = useQuery({
    queryKey: ["operation-status"],
    queryFn: api.operationStatus,
    refetchInterval: (query) => (query.state.data?.running ? 1_000 : 5_000),
  });
  const upload = useMutation({
    mutationFn: api.previewCv,
    onSuccess: (result) => {
      setPreview(result);
      setDraft(null);
      setApplyResult(null);
      setConsent(false);
      notify("CV text extracted locally", "success");
    },
    onError: (error) => notify(error.message, "error"),
  });
  const generate = useMutation({
    mutationFn: () => api.generateCvProfile(preview?.token || "", consent),
    onSuccess: (snapshot) => {
      queryClient.setQueryData(["operation-status"], snapshot);
      notify("Profile generation started", "success");
    },
    onError: (error) => notify(error.message, "error"),
  });
  const apply = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("No profile draft to apply");
      return api.applyCvProfile({
        summary: draft.summary,
        current_title: draft.current_title,
        years_experience: draft.years_experience,
        domains: draft.domains,
        languages: draft.languages,
        residency_countries: draft.residency_countries,
        stack: draft.stack,
      });
    },
    onSuccess: (result) => {
      setApplyResult({ rescored: result.rescored, pending: result.pending });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["config-file", "profile.yaml"] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      notify(`CV profile applied; ${result.rescored} evaluations re-ranked`, "success");
    },
    onError: (error) => notify(error.message, "error"),
  });
  const evaluateAll = useMutation({
    mutationFn: () => api.operation("evaluate-all", { max_jobs: applyResult?.pending || 500 }),
    onSuccess: (snapshot) => {
      queryClient.setQueryData(["operation-status"], snapshot);
      notify("Full CV evaluation started", "success");
    },
    onError: (error) => notify(error.message, "error"),
  });

  useEffect(() => {
    const status = operation.data;
    if (
      status?.task === "cv-profile" &&
      !status.running &&
      status.id !== handledRun.current &&
      isCvResult(status.result)
    ) {
      handledRun.current = status.id;
      setDraft(status.result.draft);
      notify("CV profile draft is ready for review", "success");
    }
  }, [operation.data, notify]);

  const chooseFile = (file: File | undefined) => {
    if (file) upload.mutate(file);
  };
  const updateLanguage = (index: number, patch: Partial<CvLanguage>) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            languages: current.languages.map((language, itemIndex) =>
              itemIndex === index ? { ...language, ...patch } : language,
            ),
          }
        : current,
    );
  };

  const step = applyResult ? 3 : draft ? 2 : 1;
  const provider = operation.data?.task === "cv-profile" && operation.data.running;
  return (
    <Card className="cv-import-card">
      <div className="cv-import-header">
        <div className="section-title"><span><BrainCircuit size={19} aria-hidden="true" /></span><div><p className="eyebrow">CV-first matching</p><h2>Build your profile from your CV</h2><p>Import once, review every field, then re-rank the entire database.</p></div></div>
        <div className="cv-steps" aria-label={`Step ${step} of 3`}>
          {["Upload", "Review", "Apply"].map((label, index) => <div className={step >= index + 1 ? "cv-step cv-step--active" : "cv-step"} key={label}><span>{step > index + 1 ? <Check size={13} aria-hidden="true" /> : index + 1}</span>{label}</div>)}
        </div>
      </div>

      {!draft ? (
        <div className="cv-import-grid">
          <div>
            <input ref={input} className="file-input" type="file" aria-label="Upload CV file" accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" onChange={(event) => chooseFile(event.target.files?.[0])} />
            <button className="drop-zone" type="button" onClick={() => input.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files[0]); }}>
              <span><UploadCloud size={24} aria-hidden="true" /></span>
              <strong>{upload.isPending ? "Reading CV…" : "Drop your CV here"}</strong>
              <small>PDF, DOCX, TXT, or Markdown · up to 5 MB</small>
              <em>Choose file</em>
            </button>
          </div>
          <div className="privacy-panel">
            <div><ShieldCheck size={19} aria-hidden="true" /><h3>Privacy boundaries</h3></div>
            <ul><li>The original file is never written to disk.</li><li>Name, email, phone, and links are redacted locally.</li><li>Only extracted text is held in memory for 20 minutes.</li><li>Nothing reaches an LLM until you explicitly consent.</li></ul>
          </div>
        </div>
      ) : null}

      {preview && !draft ? (
        <div className="cv-preview">
          <div className="file-summary"><span><FileCheck2 size={20} aria-hidden="true" /></span><div><strong>{preview.filename}</strong><small>{preview.kind.toUpperCase()} · {preview.pages ? `${preview.pages} pages · ` : ""}{formatNumber(preview.characters)} characters</small></div><Badge tone="success">Extracted locally</Badge></div>
          <details><summary>Review redacted text preview <ChevronDown size={15} aria-hidden="true" /></summary><pre>{preview.preview}</pre></details>
          <label className="consent-check"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>I understand that the redacted CV text will be sent to my configured LLM provider to generate a profile draft.</span></label>
          <Button disabled={!consent || Boolean(operation.data?.running)} loading={generate.isPending || Boolean(provider)} onClick={() => generate.mutate()}><BrainCircuit size={17} aria-hidden="true" /> Generate profile draft</Button>
          {operation.data?.task === "cv-profile" && operation.data.error ? <p className="inline-error" role="alert">{operation.data.error}</p> : null}
        </div>
      ) : null}

      {draft ? (
        <div className="cv-review">
          <div className="cv-review-heading"><div><Badge tone="info">Review before applying</Badge><h3>Candidate profile draft</h3><p>Every value below becomes part of matching and scoring. Remove anything inaccurate.</p></div>{draft.years_experience !== null ? <span className="experience-pill">{draft.years_experience} years detected</span> : null}</div>
          {draft.warnings.length ? <div className="draft-warnings" role="status"><CircleAlert size={18} aria-hidden="true" /><div><strong>Check these details</strong><ul>{draft.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div></div> : null}
          <div className="draft-form">
            <label className="draft-summary"><span>Professional summary</span><textarea rows={6} value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} /></label>
            <div className="draft-fields">
              <label><span>Current role</span><input value={draft.current_title || ""} onChange={(event) => setDraft({ ...draft, current_title: event.target.value || null })} /></label>
              <label><span>Residency countries (ISO-2)</span><input value={draft.residency_countries.join(", ")} onChange={(event) => setDraft({ ...draft, residency_countries: event.target.value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean) })} /></label>
              <label><span>Domains</span><input value={draft.domains.join(", ")} onChange={(event) => setDraft({ ...draft, domains: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} /></label>
            </div>
            <fieldset className="language-editor"><legend>Languages</legend>{draft.languages.map((language, index) => <div key={`${language.code}-${index}`}><input aria-label={`Language ${index + 1} code`} value={language.code} maxLength={3} onChange={(event) => updateLanguage(index, { code: event.target.value.toLowerCase() })} /><select aria-label={`${language.code} level`} value={language.level} onChange={(event) => updateLanguage(index, { level: event.target.value as CvLanguage["level"] })}>{levels.map((level) => <option key={level}>{level}</option>)}</select><span title={language.evidence}>{language.evidence}</span><button type="button" className="icon-button" aria-label={`Remove ${language.code}`} onClick={() => setDraft({ ...draft, languages: draft.languages.filter((_, itemIndex) => itemIndex !== index) })}><Trash2 size={15} aria-hidden="true" /></button></div>)}<Button size="sm" variant="ghost" type="button" onClick={() => setDraft({ ...draft, languages: [...draft.languages, { code: "", level: "B1", evidence: "Added during review" }] })}><Plus size={15} aria-hidden="true" /> Add language</Button></fieldset>
            <label><span>Skills and technologies (one per line)</span><textarea className="stack-editor" rows={9} value={draft.stack.join("\n")} onChange={(event) => setDraft({ ...draft, stack: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} /></label>
          </div>
          <div className="cv-apply-bar"><div><strong>CV match rule enabled</strong><span>Roles with zero matching CV skills receive score 0 and are excluded.</span></div><Button loading={apply.isPending} disabled={!draft.summary.trim() || draft.stack.length === 0} onClick={() => apply.mutate()}><Check size={17} aria-hidden="true" /> Override profile & re-rank</Button></div>
        </div>
      ) : null}

      {applyResult ? (
        <div className="apply-result" role="status"><span><Check size={20} aria-hidden="true" /></span><div><strong>CV profile is active</strong><p>{applyResult.rescored} existing evaluations were re-ranked. {applyResult.pending} jobs still need evaluation.</p></div>{applyResult.pending > 0 ? <Button variant="secondary" onClick={() => setConfirmEvaluate(true)}>Evaluate & rank all pending</Button> : <Badge tone="success">Everything ranked</Badge>}</div>
      ) : null}

      <ConfirmDialog open={confirmEvaluate} title={`Evaluate up to ${applyResult?.pending || 0} pending jobs?`} description="This sends each condensed job description to your configured LLM, can take a long time, and may use substantial paid tokens. Provider quotas and local caps still apply." confirmLabel="Evaluate all" onClose={() => setConfirmEvaluate(false)} onConfirm={() => evaluateAll.mutate()} />
    </Card>
  );
}
