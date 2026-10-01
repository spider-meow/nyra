import { useState } from "react";
import { PageHeader } from "../components/ui";
import { useOrg } from "../lib/org";
import { useSites } from "../lib/queries";
import { ScanHistory } from "./scans/History";
import { RunCard } from "./scans/RunCard";
import { SitesCard } from "./scans/SitesCard";

export function Scans() {
  const { brand } = useOrg();
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

  return (
    <>
      <PageHeader
        title="Sites et lectures"
        description={`Les adresses de ${brand.name}, un site par marché par exemple. Nyra les parcourt comme un visiteur (sitemaps puis liens internes, bandeaux cookies et contrôle d'âge compris), récupère chaque image et la compare à la bibliothèque de la marque.`}
      />
      <SitesCard sites={sites} skipped={skipped} onToggle={toggle} />
      <RunCard list={list} selected={selected} />
      <h2 className="mb-3 font-semibold">Historique</h2>
      <ScanHistory />
    </>
  );
}
