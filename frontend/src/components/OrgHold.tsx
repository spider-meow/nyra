import { Link } from "react-router";
import { brandLink, useOrg } from "../lib/org";
import { useJobs } from "../lib/queries";
import type { OrgJob } from "../types";

/** The job of another brand that makes the organization busy (one pass at a time): launches by hand are refused until it ends. */
export function useOrgHold(): OrgJob | undefined {
  return useJobs().data?.organization[0];
}

/** Says who started it, in which brand and since when, with a link to that brand, where it can be stopped. */
export function OrgHoldNotice({ hold }: { hold: OrgJob }) {
  const { org } = useOrg();
  const brand = org.brands.find((item) => item.id === hold.brand_id);
  return (
    <p className="text-xs text-muted" role="status">
      {hold.text} {hold.message}{" "}
      {brand ? <Link className="text-ink underline" to={brandLink(org, brand.slug)}>Ouvrir la marque {brand.name}</Link> : null}
    </p>
  );
}
