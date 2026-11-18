import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, CircleAlert, Code2, Database, FileCode2, KeyRound, Save, Search, ShieldCheck, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { formatBytes, formatDate } from "../lib/format";
import { queryClient } from "../lib/query";
import { useToast } from "../components/ToastProvider";
import { Badge, Button, Card, ErrorState, PageHeader, Skeleton } from "../components/ui";
import { CvProfileImporter } from "../components/CvProfileImporter";

export default function SettingsPage() {
  const { notify } = useToast();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [selected, setSelected] = useState("profile.yaml");
  const config = useQuery({ queryKey: ["config-file", selected], queryFn: () => api.configFile(selected) });
  const [content, setContent] = useState<string | null>(null);
  const [validation, setValidation] = useState<{ state: "idle" | "valid" | "invalid"; message: string }>({ state: "idle", message: "" });
  const validationRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setContent(null);
    setValidation({ state: "idle", message: "" });
  }, [selected]);
  const editorValue = content ?? config.data?.content ?? "";
  const dirty = Boolean(config.data && editorValue !== config.data.content);
  const save = useMutation({
    mutationFn: () => api.saveConfigFile(selected, editorValue),
    onSuccess: (result) => {
      queryClient.setQueryData(["config-file", selected], result);
      setContent(null);
      setValidation({ state: "valid", message: `${selected} is valid and saved` });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      notify(result.saved ? `Saved ${selected}` : "No changes to save", "success");
    },
    onError: (error) => {
      setValidation({ state: "invalid", message: error.message });
      window.setTimeout(() => validationRef.current?.focus(), 0);
      notify(error.message, "error");
    },
  });
  const validate = useMutation({
    mutationFn: () => api.validateConfigFile(selected, editorValue),
    onSuccess: (result) => setValidation({ state: "valid", message: result.message }),
    onError: (error) => {
      setValidation({ state: "invalid", message: error.message });
      window.setTimeout(() => validationRef.current?.focus(), 0);
    },
  });
  useEffect(() => {
    const saveShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s" && dirty) {
        event.preventDefault();
        save.mutate();
      }
    };
    window.addEventListener("keydown", saveShortcut);
    return () => window.removeEventListener("keydown", saveShortcut);
  }, [dirty, save]);

  if (settings.isError) return <ErrorState message={settings.error.message} retry={() => void settings.refetch()} />;
  if (!settings.data) return <div className="page-stack"><Skeleton className="skeleton--title" /><Skeleton className="skeleton--panel" /></div>;
  const data = settings.data;
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Local configuration" title="Settings" description="Review your profile and safely edit every YAML configuration file. Secrets are never shown." />
      <CvProfileImporter />
      <div className="settings-grid">
        <Card className="profile-summary"><div className="section-title"><span><UserRound size={18} aria-hidden="true" /></span><div><h2>Candidate profile</h2><p>{data.profile.current_title || "Role not specified"}{data.profile.years_experience !== null ? ` · ${data.profile.years_experience} years` : ""}</p></div></div><p>{data.profile.summary}</p>{data.profile.domains.length ? <div className="settings-section"><strong>Domains</strong><div className="chip-row chip-row--roomy">{data.profile.domains.map((domain) => <Badge tone="success" key={domain}>{domain}</Badge>)}</div></div> : null}<div className="settings-section"><strong>Languages</strong><div className="chip-row chip-row--roomy">{Object.entries(data.profile.languages).map(([language, level]) => <Badge tone="info" key={language}>{language.toUpperCase()} · {level}</Badge>)}</div></div><div className="settings-section"><strong>Core stack</strong><div className="chip-row chip-row--roomy">{data.profile.stack.slice(0, 12).map((skill) => <Badge key={skill}>{skill}</Badge>)}</div></div></Card>
        <Card><div className="section-title"><span><Search size={18} aria-hidden="true" /></span><div><h2>Search scope</h2><p>{data.search.hours_old}-hour lookback</p></div></div><div className="settings-kv"><div><span>Countries</span><strong>{data.search.active_countries.join(", ")}</strong></div><div><span>Sources</span><strong>{data.search.sources.join(", ")}</strong></div><div><span>Fixed queries</span><strong>{data.search.queries.length}</strong></div><div><span>Rotating queries</span><strong>{data.search.rotating_queries.length}</strong></div></div></Card>
        <Card><div className="section-title"><span><KeyRound size={18} aria-hidden="true" /></span><div><h2>Evaluator</h2><p>Credentials are read from .env</p></div></div><div className="provider-card"><div><span>{data.llm.provider}</span><strong>{data.llm.models.join(", ")}</strong></div>{data.llm.key_configured ? <Badge tone="success"><Check size={13} aria-hidden="true" /> Key configured</Badge> : <Badge tone="danger"><CircleAlert size={13} aria-hidden="true" /> {data.llm.key_name} missing</Badge>}</div><div className="settings-kv"><div><span>Threshold</span><strong>{data.scoring.threshold}</strong></div><div><span>Base score</span><strong>{data.scoring.base}</strong></div><div><span>Zero CV overlap</span><strong>{data.scoring.no_cv_stack_match ? "Exclude" : "Allow"}</strong></div></div></Card>
        <Card><div className="section-title"><span><Database size={18} aria-hidden="true" /></span><div><h2>Local storage</h2><p>Nothing is uploaded by this UI</p></div></div><div className="settings-kv"><div><span>Database</span><strong>{formatBytes(data.database.size)}</strong></div><div><span>Last changed</span><strong>{formatDate(data.database.modified_at, true)}</strong></div></div><code className="path-block">{data.database.path}</code></Card>
      </div>

      <Card className="config-editor-card">
        <div className="card-heading"><div><p className="eyebrow">Advanced</p><h2>Configuration editor</h2></div><div className="editor-status">{dirty ? <Badge tone="warning">Unsaved changes</Badge> : <Badge tone="success"><ShieldCheck size={13} aria-hidden="true" /> Validated on save</Badge>}</div></div>
        <div className="config-editor">
          <nav aria-label="Configuration files">{data.config_files.map((file) => <button className={selected === file ? "config-file config-file--active" : "config-file"} key={file} onClick={() => { if (!dirty || confirm("Discard unsaved changes?")) setSelected(file); }}><FileCode2 size={16} aria-hidden="true" />{file}</button>)}</nav>
          <div className="editor-pane">
            <div className="editor-toolbar"><div><Code2 size={16} aria-hidden="true" /><strong>{selected}</strong>{config.data ? <span>Edited {formatDate(config.data.modified_at, true)}</span> : null}</div><Button size="sm" disabled={!dirty || validation.state === "invalid"} loading={save.isPending} onClick={() => save.mutate()}><Save size={16} aria-hidden="true" /> Save <kbd>⌘S</kbd></Button></div>
            <div ref={validationRef} id="config-validation" className={`editor-validation editor-validation--${validation.state}`} role={validation.state === "invalid" ? "alert" : validation.state === "valid" ? "status" : undefined} tabIndex={validation.state === "invalid" ? -1 : undefined}>{validation.state === "valid" ? <Check size={15} aria-hidden="true" /> : <CircleAlert size={15} aria-hidden="true" />}{validation.message || "Configuration validation status"}</div>
            {config.isError ? <ErrorState message={config.error.message} retry={() => void config.refetch()} /> : <textarea className="yaml-editor" aria-label={`Edit ${selected}`} aria-invalid={validation.state === "invalid"} aria-describedby={validation.state !== "idle" ? "config-validation" : "config-editor-help"} spellCheck={false} value={editorValue} onChange={(event) => { setContent(event.target.value); setValidation({ state: "idle", message: "" }); }} onBlur={() => { if (dirty) validate.mutate(); }} />}
            <p className="editor-help" id="config-editor-help">YAML is checked when you leave the editor and validated again before saving. The previous file is copied to <code>config/.backups/</code>; invalid changes never reach disk.</p>
          </div>
        </div>
      </Card>
    </div>
  );
}
