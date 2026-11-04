import { useState } from "react";
import { Navigate } from "react-router";
import { PageHeader, Skeleton } from "../components/ui";
import { useMe, useOrg } from "../lib/org";
import { useSites } from "../lib/queries";
import { ScanHistory } from "./scans/History";
import { RunCard, RunCardSkeleton } from "./scans/RunCard";
import { SitesCard } from "./scans/SitesCard";

/** The launcher, the addresses and the history of the reads: the Nyra team's. Anyone else asking for the address lands on the dashboard (AXE-683). */
export function Scans() {
  const { platformAdmin, link } = useOrg();
  const me = useMe();
  if (me.isPending) return <><PageHeader title="Sites et lectures" /><Skeleton className="h-40" /></>; // on a first load the team is not known yet: deciding now would send the team away too
  return platformAdmin ? <TeamScans /> : <Navigate to={link("dashboard")} replace />;
}

function TeamScans() {
  const sites = useSites();
  // Every address is read unless unticked.
  const [skipped, setSkipped] = useState<Set<string>>(new Set());

  const list = sites.data?.sites ?? [];
  const selected = list.filter((site) => !skipped.has(site.id));

  function toggle(id: string, on: boolean) {
    const next = new Set(skipped);
    if (on) next.delete(id);
    else next.add(id);
    setSkipped(next);
  }

  const toggleAll = (on: boolean) => setSkipped(on ? new Set() : new Set(list.map((site) => site.id)));

  return (
    <>
      <PageHeader
        title="Sites et lectures"
      />
      {sites.isLoading ? <RunCardSkeleton /> : <RunCard list={list} selected={selected} />}
      <SitesCard sites={sites} skipped={skipped} onToggle={toggle} onToggleAll={toggleAll} />
      <h2 className="mb-3 font-semibold">Historique</h2>
      <ScanHistory />
    </>
  );
}
