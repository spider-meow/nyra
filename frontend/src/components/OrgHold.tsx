import { Link } from "react-router";
import { brandLink, useOrg } from "../lib/org";
import { useJobs } from "../lib/queries";
import type { JobKind, OrgJob } from "../types";

const WHAT: Record<JobKind, string> = {
  crawl: "Une lecture", match: "Une comparaison", report: "Un rapport", locate: "Une recherche d'image", check: "Une vérification de pages", index: "Une indexation",
};

/** What makes the organization busy: one pass at a time, so launches by hand are refused until it ends. `other` is a job of another brand. */
export type Hold = { text: string; message: string; other?: OrgJob };

/** The job in the way of a launch by hand: one of this brand (but an indexing, which only queues), else one of another brand. */
export function useOrgHold(): Hold | undefined {
  const data = useJobs().data;
  const own = data?.active.find((job) => job.kind !== "index");
  if (own) return { text: `${WHAT[own.kind]} est déjà en cours ou en attente.`, message: own.message };
  const other = data?.organization?.[0];
  return other && { text: other.text, message: other.message, other };
}

/** For the disabled launch buttons: they point at the notice. */
export const holdProps = (hold: Hold | undefined) => ({ "aria-describedby": hold ? "org-hold" : undefined });

/** For an administrator: the fixed sentence is the announced part; who, since when and the progress change with time and are not. */
export function OrgHoldNotice({ hold }: { hold: Hold }) {
  const { org } = useOrg();
  const { other } = hold;
  const brand = other && org.brands.find((item) => item.id === other.brand_id);
  return (
    <div id="org-hold" className="text-xs text-muted">
      <p role="status">{other ? `Un passage est déjà en cours dans la marque ${other.brand_name}.` : hold.text}</p>
      <p>{other ? other.text : null} {hold.message}</p>
      {brand ? <Link className="text-ink underline" to={brandLink(org, brand.slug)}>Pour l'annuler, ouvrez la marque {brand.name}</Link> : null}
    </div>
  );
}
