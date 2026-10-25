import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Icon, type IconName } from "../components/icons";
import { cx, EmptyState, PageHeader, Skeleton } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useOrg } from "../lib/org";
import { useSettings } from "../lib/queries";
import type { Settings as SettingsData } from "../types";
import { Account } from "./settings/Account";
import { Audit } from "./settings/Audit";
import { Collections } from "./settings/Collections";
import { DangerZone } from "./settings/DangerZone";
import { Detection } from "./settings/Detection";
import { Exclusions } from "./settings/Exclusions";
import { Indexation } from "./settings/Indexation";
import { Logos } from "./settings/Logos";
import { Notifications } from "./settings/Notifications";
import { Profile } from "./settings/Profile";
import { Reading } from "./settings/Reading";
import { Schedule } from "./settings/Schedule";
import { Team } from "./settings/Team";
import { Usage } from "./settings/Usage";

/** What a section is about: the brand on screen, the whole organization, or the signed-in person. */
type Group = "marque" | "organisation" | "moi";

type Section = { id: string; label: string; icon: IconName; group: Group; adminOnly?: boolean; render: (data: SettingsData) => ReactNode };

/** The brand's page: its identity, then what is set aside, then the rare actions. */
function General() {
  const { admin } = useOrg();
  return (
    <>
      <Profile />
      {admin ? <Logos /> : null}
      <Exclusions />
      <Schedule />
      {admin ? <Indexation /> : null}
      <DangerZone />
    </>
  );
}

const SECTIONS: Section[] = [
  { id: "account", label: "Profil et sécurité", icon: "user", group: "moi", render: () => <Account /> },
  { id: "notifications", label: "Notifications", icon: "bell", group: "moi", render: () => <Notifications /> },
  { id: "general", label: "Général", icon: "idBadge", group: "marque", render: () => <General /> },
  { id: "collections", label: "Collections", icon: "folders", group: "marque", adminOnly: true, render: () => <Collections /> },
  { id: "team", label: "Équipe", icon: "users", group: "organisation", render: () => <Team /> },
  { id: "audit", label: "Journal d'activité", icon: "eye", group: "organisation", adminOnly: true, render: () => <Audit /> },
  { id: "detection", label: "Détection", icon: "radar", group: "organisation", adminOnly: true, render: (data) => <Detection data={data} /> },
  { id: "usage", label: "Usage", icon: "stats", group: "organisation", adminOnly: true, render: () => <Usage /> },
  { id: "reading", label: "Lecture des sites", icon: "worldSearch", group: "organisation", adminOnly: true, render: (data) => <Reading data={data} /> },
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
  const { org, brand } = useOrg();
  const titles: Record<Group, string> = { moi: "Mon compte", marque: brand.name, organisation: org.name };
  return (
    <nav aria-label="Sections des réglages" className="flex flex-wrap gap-1 lg:sticky lg:top-6 lg:w-52 lg:shrink-0 lg:flex-col lg:flex-nowrap lg:self-start">
      {(["moi", "marque", "organisation"] as Group[])
        .filter((group) => props.sections.some((section) => section.group === group))
        .map((group, index) => (
          <div key={group} className="contents">
            <p className={cx("hidden truncate px-3 pb-1 text-xs font-medium tracking-wide text-faint uppercase lg:block", index > 0 && "pt-4")} title={titles[group]}>{titles[group]}</p>
            {props.sections
              .filter((section) => section.group === group)
              .map((section) => (
                <SectionLink key={section.id} section={section} active={section.id === props.activeId} onPick={() => props.onPick(section.id)} />
              ))}
          </div>
        ))}
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
  const active = visible.find((section) => section.id === params.get("section")) ?? visible.find((section) => section.id === "general") ?? visible[0];
  return (
    <>
      <PageHeader title="Réglages" />
      <div className="flex flex-col gap-6 lg:flex-row lg:gap-10">
        <Nav sections={visible} activeId={active.id} onPick={(id) => setParams({ section: id }, { replace: true })} />
        <div className="min-w-0 max-w-3xl flex-1">
          <h2 className="mb-6 text-xl font-semibold tracking-tight">{active.label}</h2>
          {active.render(settings.data)}
        </div>
      </div>
    </>
  );
}
