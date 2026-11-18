import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Filter, RotateCcw, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type JobsQuery } from "../lib/api";
import { JobTable } from "./JobTable";
import { Button, Card, ErrorState, PageHeader } from "./ui";

interface ExplorerProps {
  kind?: "shortlist" | "internship" | "ranking";
  decision?: "applied" | "discarded";
  title: string;
  eyebrow?: string;
  description: string;
}

export function JobsExplorer({ kind, decision, title, eyebrow, description }: ExplorerProps) {
  const [url, setUrl] = useSearchParams();
  const [search, setSearch] = useState(url.get("q") || "");
  const page = Math.max(1, Number(url.get("page") || 1));
  const country = url.get("country") || "";
  const status = url.get("status") || "";
  const minScore = url.get("min_score") || "";
  const sort = url.get("sort") || (kind === "shortlist" || kind === "ranking" ? "score" : "first_seen");
  const direction = (url.get("direction") || "desc") as "asc" | "desc";

  const query: JobsQuery = {
    page,
    page_size: 30,
    q: url.get("q") || "",
    country: country ? [country] : undefined,
    status: status ? [status] : undefined,
    decision,
    min_score: minScore ? Number(minScore) : undefined,
    kind,
    sort,
    direction,
  };
  const jobs = useQuery({
    queryKey: ["jobs", query],
    queryFn: () => api.jobs(query),
    placeholderData: keepPreviousData,
  });

  const setParam = (name: string, value: string) => {
    setUrl((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(name, value); else next.delete(name);
      next.delete("page");
      return next;
    });
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setParam("q", search.trim());
  };
  const clear = () => {
    setSearch("");
    setUrl({});
  };
  const setPage = (nextPage: number) => {
    setUrl((current) => {
      const next = new URLSearchParams(current);
      if (nextPage === 1) next.delete("page"); else next.set("page", String(nextPage));
      return next;
    });
    document.querySelector("#main-content")?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const count = jobs.data?.total ?? 0;
  return (
    <div className="page-stack">
      <PageHeader eyebrow={eyebrow} title={title} description={description} actions={<span className="result-count">{count.toLocaleString()} results</span>} />
      <Card className="filter-card">
        <form className="filters" onSubmit={submit}>
          <div className="filter-search">
            <Search size={17} aria-hidden="true" />
            <label className="sr-only" htmlFor="job-search">Search roles</label>
            <input id="job-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Role, company, location, or skill" />
          </div>
          <label><span>Country</span><select value={country} onChange={(event) => setParam("country", event.target.value)}><option value="">All countries</option>{jobs.data?.facets.countries.map((facet) => <option key={facet.value} value={facet.value}>{facet.value} ({facet.count})</option>)}</select></label>
          {!kind && !decision ? <label><span>Status</span><select value={status} onChange={(event) => setParam("status", event.target.value)}><option value="">All statuses</option>{jobs.data?.facets.statuses.map((facet) => <option key={facet.value} value={facet.value}>{facet.value.replaceAll("_", " ")} ({facet.count})</option>)}</select></label> : null}
          {!kind && !decision ? <label><span>Minimum score</span><select value={minScore} onChange={(event) => setParam("min_score", event.target.value)}><option value="">Any score</option><option value="50">50+</option><option value="70">70+</option><option value="85">85+</option></select></label> : null}
          <label><span>Sort by</span><select value={sort} onChange={(event) => setParam("sort", event.target.value)}><option value="first_seen">Newest</option><option value="score">Score</option><option value="company">Company</option><option value="title">Title</option></select></label>
          <Button type="submit" size="sm"><Filter size={16} aria-hidden="true" /> Apply</Button>
          {url.size > 0 ? <Button type="button" size="sm" variant="ghost" onClick={clear}><RotateCcw size={16} aria-hidden="true" /> Reset</Button> : null}
        </form>
      </Card>
      {jobs.isError ? <ErrorState message={jobs.error.message} retry={() => void jobs.refetch()} /> : <JobTable jobs={jobs.data?.items || []} loading={jobs.isFetching} />}
      {jobs.data && jobs.data.pages > 1 ? (
        <nav className="pagination" aria-label="Job results pages">
          <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
          <span>Page <strong>{page}</strong> of {jobs.data.pages}</span>
          <Button variant="secondary" size="sm" disabled={page >= jobs.data.pages} onClick={() => setPage(page + 1)}>Next</Button>
        </nav>
      ) : null}
    </div>
  );
}
