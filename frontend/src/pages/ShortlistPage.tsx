import { JobsExplorer } from "../components/JobsExplorer";

export default function ShortlistPage() {
  return <JobsExplorer kind="ranking" eyebrow="CV-first ranking" title="Best to least match" description="Every evaluated role ranked against your active CV profile. Zero-overlap roles are excluded at the bottom." />;
}
