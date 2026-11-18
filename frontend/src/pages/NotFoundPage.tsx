import { Radar } from "lucide-react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/ui";

export default function NotFoundPage() {
  return <EmptyState icon={Radar} title="Page not found" description="This part of the radar is outside the current scan area." action={<Link className="button button--primary button--md" to="/">Back to overview</Link>} />;
}
