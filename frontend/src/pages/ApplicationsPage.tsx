import { Archive, CircleCheckBig } from "lucide-react";
import { useState } from "react";
import { JobsExplorer } from "../components/JobsExplorer";

export default function ApplicationsPage() {
  const [tab, setTab] = useState<"applied" | "discarded">("applied");
  return (
    <div>
      <div className="segmented" role="tablist" aria-label="Decision type">
        <button role="tab" aria-selected={tab === "applied"} onClick={() => setTab("applied")}><CircleCheckBig size={17} aria-hidden="true" /> Applied</button>
        <button role="tab" aria-selected={tab === "discarded"} onClick={() => setTab("discarded")}><Archive size={17} aria-hidden="true" /> Discarded</button>
      </div>
      <JobsExplorer decision={tab} eyebrow="Decisions" title={tab === "applied" ? "Applications" : "Discarded roles"} description={tab === "applied" ? "Roles you chose to pursue, with notes and current links." : "Roles you intentionally set aside. Decisions can be changed from job details."} />
    </div>
  );
}
