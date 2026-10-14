import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Icon, type IconName } from "../components/icons";
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

type Section = { id: string; label: string; icon: IconName; adminOnly?: boolean; render: (data: SettingsData) => ReactNode };

const SECTIONS: Section[] = [
  {
    id: "marques",
    label: "Marques",
    icon: "library",
    render: () => (
      <>
        <Brands />
        <Exclusions />
        <DeleteBrands />
      </>
    ),
  },
  {
    id: "equipe",
    label: "Équipe",
    icon: "users",
    render: () => <Team />,
  },
  { id: "compte", label: "Mon compte", icon: "user", render: () => <Account /> },
  {
    id: "detection",
    label: "Détection",
    icon: "scan",
    adminOnly: true,
    render: (data) => <Detection data={data} />,
  },
  {
    id: "lecture",
    label: "Lecture des sites",
    icon: "eye",
    adminOnly: true,
    render: (data) => <Reading data={data} />,
  },
];

function SectionLink(props: { section: Section; active: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      aria-current={props.active ? "page" : undefined}
      onClick={props.onPick}
      className={cx(
        "flex h-9 items-center gap-2.5 rounded-lg px-3 text-left text-sm whitespace-nowrap",
        props.active ? "bg-sunk font-medium text-ink" : "text-muted hover:bg-sunk hover:text-ink",
      )}
    >
      <Icon name={props.section.icon} size={16} className={props.active ? "text-ink" : "text-faint"} />
      {props.section.label}
    </button>
  );
}

function Nav(props: { sections: Section[]; activeId: string; onPick: (id: string) => void }) {
  const everyone = props.sections.filter((section) => !section.adminOnly);
  const admins = props.sections.filter((section) => section.adminOnly);
  const link = (section: Section) => <SectionLink key={section.id} section={section} active={section.id === props.activeId} onPick={() => props.onPick(section.id)} />;
  return (
    <nav aria-label="Sections des réglages" className="flex flex-wrap gap-1 lg:sticky lg:top-6 lg:w-52 lg:shrink-0 lg:flex-col lg:flex-nowrap lg:self-start">
      {everyone.map(link)}
      {admins.length ? <p className="hidden px-3 pt-4 pb-1 text-xs font-medium tracking-wide text-faint uppercase lg:block">Administrateurs</p> : null}
      {admins.map(link)}
    </nav>
  );
}

export function Settings() {
  const { admin } = useOrg();
  const settings = useSettings();
  const [params, setParams] = useSearchParams();

  if (settings.isLoading) return <Skeleton className="h-96" />;
  if (settings.error || !settings.data) return <EmptyState title="Réglages indisponibles" body={errorMessage(settings.error)} />;

  const visible = SECTIONS.filter((section) => admin || !section.adminOnly);
  const active = visible.find((section) => section.id === params.get("section")) ?? visible[0];
  return (
    <>
      <PageHeader title="Réglages" />
      <div className="flex flex-col gap-6 lg:flex-row lg:gap-10">
        <Nav sections={visible} activeId={active.id} onPick={(id) => setParams({ section: id }, { replace: true })} />
        <div className="min-w-0 flex-1">
          <header className="mb-6">
            <h2 className="text-lg font-semibold">{active.label}</h2>
          </header>
          {active.render(settings.data)}
        </div>
      </div>
    </>
  );
}
