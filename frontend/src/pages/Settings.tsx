import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { cx, EmptyState, PageHeader, Skeleton } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useOrg } from "../lib/org";
import { useSettings } from "../lib/queries";
import type { Settings as SettingsData } from "../types";
import { Account } from "./settings/Account";
import { Brands, DeleteBrands } from "./settings/Brands";
import { Detection } from "./settings/Detection";
import { Exclusions } from "./settings/Exclusions";
import { Reading } from "./settings/Reading";
import { Team } from "./settings/Team";

type Section = { id: string; label: string; adminOnly?: boolean; render: (data: SettingsData) => ReactNode };

const stack = (...children: ReactNode[]) => <div className="grid gap-4">{children}</div>;

const SECTIONS: Section[] = [
  { id: "marques", label: "Marques", render: () => stack(<Brands />, <Exclusions />) },
  { id: "equipe", label: "Équipe", render: () => <Team /> },
  { id: "compte", label: "Mon compte", render: () => <Account /> },
  { id: "detection", label: "Détection", adminOnly: true, render: (data) => <Detection data={data} /> },
  { id: "lecture", label: "Lecture des sites", adminOnly: true, render: (data) => <Reading data={data} /> },
  { id: "donnees", label: "Données", adminOnly: true, render: () => <DeleteBrands /> },
];

function SectionLink(props: { section: Section; active: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      aria-current={props.active ? "page" : undefined}
      onClick={props.onPick}
      className={cx(
        "h-9 rounded-lg px-3 text-left text-sm whitespace-nowrap",
        props.active ? "bg-yellow font-medium text-ink" : "text-muted hover:text-ink",
      )}
    >
      {props.section.label}
    </button>
  );
}

function Nav(props: { sections: Section[]; activeId: string; onPick: (id: string) => void }) {
  const everyone = props.sections.filter((section) => !section.adminOnly);
  const admins = props.sections.filter((section) => section.adminOnly);
  const link = (section: Section) => <SectionLink key={section.id} section={section} active={section.id === props.activeId} onPick={() => props.onPick(section.id)} />;
  return (
    <nav aria-label="Sections des réglages" className="flex gap-1 overflow-x-auto lg:sticky lg:top-6 lg:w-52 lg:shrink-0 lg:flex-col lg:self-start">
      {everyone.map(link)}
      {admins.length ? <p className="hidden px-3 pt-4 pb-1 text-xs font-medium tracking-wide text-faint uppercase lg:block">Administrateurs</p> : null}
      {admins.map(link)}
    </nav>
  );
}

export function Settings() {
  const { admin, org } = useOrg();
  const settings = useSettings();
  const [params, setParams] = useSearchParams();

  if (settings.isLoading) return <Skeleton className="h-96" />;
  if (settings.error || !settings.data) return <EmptyState title="Réglages indisponibles" body={errorMessage(settings.error)} />;

  const visible = SECTIONS.filter((section) => admin || !section.adminOnly);
  const active = visible.find((section) => section.id === params.get("section")) ?? visible[0];
  return (
    <>
      <PageHeader title="Réglages" description={`Propres à ${org.name}${org.brands.length > 1 ? ", communs à toutes ses marques" : ""}.`} />
      <div className="flex flex-col gap-6 lg:flex-row lg:gap-10">
        <Nav sections={visible} activeId={active.id} onPick={(id) => setParams({ section: id }, { replace: true })} />
        <div className="min-w-0 flex-1">{active.render(settings.data)}</div>
      </div>
    </>
  );
}
