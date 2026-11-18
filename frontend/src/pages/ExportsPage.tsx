import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileOutput, FileSpreadsheet, History, ListChecks } from "lucide-react";
import { api } from "../lib/api";
import { formatBytes, formatDate } from "../lib/format";
import { queryClient } from "../lib/query";
import { useToast } from "../components/ToastProvider";
import { Button, Card, EmptyState, ErrorState, PageHeader } from "../components/ui";

export default function ExportsPage() {
  const { notify } = useToast();
  const files = useQuery({ queryKey: ["exports"], queryFn: api.exports });
  const status = useQuery({ queryKey: ["operation-status"], queryFn: api.operationStatus, refetchInterval: (query) => query.state.data?.running ? 1_000 : 5_000 });
  const create = useMutation({
    mutationFn: (mode: "new" | "all" | "day") => api.operation("export", { mode }),
    onSuccess: (snapshot) => {
      queryClient.setQueryData(["operation-status"], snapshot);
      notify("Export started", "success");
      window.setTimeout(() => void files.refetch(), 1_500);
    },
    onError: (error) => notify(error.message, "error"),
  });
  if (files.isError) return <ErrorState message={files.error.message} retry={() => void files.refetch()} />;
  const busy = status.data?.running || create.isPending;
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Portable results" title="Exports" description="Create or download CSVs that can be opened in Sheets, Excel, or any data tool." />
      <div className="export-actions">
        <Card><span className="operation-icon"><ListChecks size={20} aria-hidden="true" /></span><div><h2>New shortlist</h2><p>Only qualifying jobs not exported before.</p></div><Button size="sm" disabled={busy} onClick={() => create.mutate("new")}>Generate</Button></Card>
        <Card><span className="operation-icon operation-icon--violet"><History size={20} aria-hidden="true" /></span><div><h2>Full ranking</h2><p>Historical ranking, including decisions.</p></div><Button size="sm" variant="secondary" disabled={busy} onClick={() => create.mutate("all")}>Generate</Button></Card>
        <Card><span className="operation-icon operation-icon--green"><FileOutput size={20} aria-hidden="true" /></span><div><h2>Today's evaluations</h2><p>Every job evaluated today.</p></div><Button size="sm" variant="secondary" disabled={busy} onClick={() => create.mutate("day")}>Generate</Button></Card>
      </div>
      <Card>
        <div className="card-heading"><div><p className="eyebrow">Files</p><h2>Available downloads</h2></div><span className="result-count">{files.data?.items.length || 0} files</span></div>
        {files.data?.items.length ? <div className="file-list">{files.data.items.map((file) => <div className="file-row" key={file.name}><span className="file-icon"><FileSpreadsheet size={20} aria-hidden="true" /></span><div><strong>{file.name}</strong><span>{file.rows} rows · {formatBytes(file.size)} · {formatDate(file.modified_at, true)}</span></div><a href={`/api/exports/${encodeURIComponent(file.name)}`} className="button button--secondary button--sm" download><Download size={16} aria-hidden="true" /> Download</a></div>)}</div> : <EmptyState icon={FileSpreadsheet} title="No exports yet" description="Generate a shortlist or ranking to create your first CSV." />}
      </Card>
    </div>
  );
}
