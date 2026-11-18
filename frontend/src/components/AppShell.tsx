import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Archive,
  BarChart3,
  BriefcaseBusiness,
  Command,
  FileCheck2,
  Gauge,
  Menu,
  Moon,
  Radar,
  Search,
  Settings,
  Sparkles,
  Sun,
  X,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useTheme } from "./ThemeProvider";
import { IconButton } from "./ui";

const navItems = [
  { to: "/", label: "Overview", icon: Gauge, end: true },
  { to: "/jobs", label: "All jobs", icon: BriefcaseBusiness },
  { to: "/shortlist", label: "CV ranking", icon: Sparkles },
  { to: "/internships", label: "Internships", icon: FileCheck2 },
  { to: "/applications", label: "Decisions", icon: Archive },
  { to: "/operations", label: "Operations", icon: Command },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/exports", label: "Exports", icon: Activity },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
  const { theme, toggle } = useTheme();
  const operation = useQuery({
    queryKey: ["operation-status"],
    queryFn: api.operationStatus,
    refetchInterval: (query) => (query.state.data?.running ? 1_500 : 10_000),
  });

  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {
    const page = navItems.find((item) =>
      item.end ? location.pathname === item.to : location.pathname.startsWith(item.to),
    );
    document.title = `${page?.label || "Job"} · Job Radar`;
  }, [location.pathname]);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.key === "/" || ((event.metaKey || event.ctrlKey) && event.key === "k")) && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement)) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>("#global-search")?.focus();
      }
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [menuOpen]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    const value = search.trim();
    navigate(value ? `/jobs?q=${encodeURIComponent(value)}` : "/jobs");
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside id="primary-navigation" className={`sidebar ${menuOpen ? "sidebar--open" : ""}`} aria-label="Primary navigation">
        <div className="brand">
          <span className="brand__mark"><Radar size={22} aria-hidden="true" /></span>
          <div><strong>Job Radar</strong><small>Local command center</small></div>
          <IconButton className="sidebar__close" label="Close navigation" onClick={() => setMenuOpen(false)}><X size={20} aria-hidden="true" /></IconButton>
        </div>
        <nav className="nav-list">
          <p className="nav-label">Workspace</p>
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item ${isActive ? "nav-item--active" : ""}`}>
              <Icon size={18} aria-hidden="true" /><span>{label}</span>
              {label === "Operations" && operation.data?.running ? <span className="nav-running" aria-label="Operation running" /> : null}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer">
          <span className={`system-dot ${operation.isError ? "system-dot--error" : ""}`} aria-hidden="true" />
          <div><strong>{operation.data?.running ? `${operation.data.task} running` : "System ready"}</strong><small>{operation.data?.running ? "One task at a time" : "Local only · no server"}</small></div>
        </div>
      </aside>
      {menuOpen ? <button className="sidebar-scrim" onClick={() => setMenuOpen(false)} aria-label="Close navigation" /> : null}

      <div className="workspace">
        <header className="topbar">
          <IconButton className="menu-button" label="Open navigation" aria-expanded={menuOpen} aria-controls="primary-navigation" onClick={() => setMenuOpen(true)}><Menu size={20} aria-hidden="true" /></IconButton>
          <form className="global-search" role="search" onSubmit={submitSearch}>
            <Search size={18} aria-hidden="true" />
            <label className="sr-only" htmlFor="global-search">Search all jobs</label>
            <input id="global-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search titles, companies, skills…" />
            <kbd>⌘ K</kbd>
          </form>
          {operation.data?.running ? (
            <button className="running-pill" onClick={() => navigate("/operations")}>
              <span aria-hidden="true" /> {operation.data.task}
            </button>
          ) : null}
          <IconButton label={`Use ${theme === "light" ? "dark" : "light"} theme`} onClick={toggle}>
            {theme === "light" ? <Moon size={19} aria-hidden="true" /> : <Sun size={19} aria-hidden="true" />}
          </IconButton>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}><Outlet /></main>
      </div>
    </div>
  );
}
