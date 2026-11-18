import { LoaderCircle, type LucideIcon } from "lucide-react";
import { clsx } from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import { scoreTone } from "../lib/format";

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
}) {
  return (
    <button
      className={clsx("button", `button--${variant}`, `button--${size}`, className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <LoaderCircle className="spin" size={16} aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function IconButton({ label, className, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button className={clsx("icon-button", className)} aria-label={label} title={label} {...props}>
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={clsx("card", className)}>{children}</section>;
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
    </header>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={clsx("badge", `badge--${tone}`)}>{children}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    pending: "warning",
    evaluated: "info",
    excluded_prefilter: "danger",
    excluded_fit: "danger",
    duplicate: "neutral",
    stale: "neutral",
    applied: "success",
    discarded: "neutral",
  };
  return <Badge tone={tone[status] ?? "neutral"}>{status.replaceAll("_", " ")}</Badge>;
}

export function Score({ value, large = false }: { value: number | null; large?: boolean }) {
  return (
    <span className={clsx("score", `score--${scoreTone(value)}`, large && "score--large")} aria-label={value === null ? "Not scored" : `Score ${value} out of 100`}>
      {value === null ? "—" : Math.round(value)}
    </span>
  );
}

export function StatCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "blue",
}: {
  label: string;
  value: string | number;
  detail?: string;
  icon: LucideIcon;
  tone?: "blue" | "green" | "amber" | "violet";
}) {
  return (
    <Card className="stat-card">
      <div className={`stat-card__icon stat-card__icon--${tone}`}><Icon size={20} aria-hidden="true" /></div>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        {detail ? <small>{detail}</small> : null}
      </div>
    </Card>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span><Icon size={24} aria-hidden="true" /></span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-state" role="alert">
      <strong>Something went wrong</strong>
      <p>{message}</p>
      {retry ? <Button variant="secondary" onClick={retry}>Try again</Button> : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <span className={clsx("skeleton", className)} aria-hidden="true" />;
}

export function JobTitleLink({ id, children }: { id: string; children: ReactNode }) {
  return <Link className="job-title-link" to={`/jobs/${encodeURIComponent(id)}`}>{children}</Link>;
}
