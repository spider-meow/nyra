import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, Outlet } from "react-router";
import { useOrg, useOrganizations } from "../lib/org";
import { Icon } from "./icons";
import { JobToasts } from "./layout/JobToasts";
import { Sidebar, useNavItems } from "./layout/Sidebar";
import { setViewAs, type ViewAs } from "../lib/viewAs";
import { Button, Checkbox, Segmented } from "./ui";

/** The side column (a menu on a phone) and the page beside it. `sidebar` gets the function that closes the phone menu. */
export function Frame(props: { sidebar: (close: () => void) => ReactNode; title: string; children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const sidebar = props.sidebar(() => setMenuOpen(false));

  return (
    <div className="min-h-screen md:grid md:grid-cols-[256px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-screen border-r border-line bg-side md:block">{sidebar}</aside>
      <div className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-side px-4 py-2 md:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <p className="font-display text-[22px] leading-none">Nyra</p>
          <p className="truncate text-sm text-muted">· {props.title}</p>
        </div>
        <Button variant="ghost" aria-expanded={menuOpen} aria-label={menuOpen ? "Fermer le menu" : "Ouvrir le menu"} className="w-11 px-0" onClick={() => setMenuOpen((open) => !open)}>
          <Icon name={menuOpen ? "close" : "menu"} size={20} />
        </Button>
      </div>
      {menuOpen ? <div className="border-b border-line bg-side md:hidden">{sidebar}</div> : null}
      <main className="min-w-0 px-4 py-6 md:px-14 md:py-10">{props.children}</main>
    </div>
  );
}

type Seen = "staff" | ViewAs["role"];

/** Shown in a client's space opened by the Nyra team (not a member): whose space it is, the role to see it with, and the way back. */
function StaffBanner() {
  const { org, staffView, viewAs } = useOrg();
  const queries = useQueryClient();
  if (!staffView) return null;
  // What is on screen was asked under the previous role: everything is asked again under the new one.
  function choose(role: Seen, write: boolean) {
    setViewAs(role === "staff" ? null : { orgId: org.org_id, role, write });
    void queries.invalidateQueries();
  }
  const seen: Seen = viewAs?.role ?? "staff";
  return (
    <div role="status" className="-mx-4 -mt-6 mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-ink bg-yellow px-4 py-2.5 text-sm md:-mx-14 md:-mt-10 md:px-14">
      <Icon name="eye" size={16} />
      <p className="min-w-0 flex-1 basis-56">Vous consultez l'espace de <strong>{org.name}</strong> {seen === "staff" ? "en tant qu'équipe Nyra" : seen === "admin" ? "en tant qu'administrateur" : "en tant que client"}.</p>
      <Segmented
        label="Voir en tant que"
        value={seen}
        options={[{ value: "staff", label: "Équipe Nyra" }, { value: "admin", label: "Administrateur" }, { value: "client", label: "Client" }]}
        onChange={(role) => choose(role, false)}
      />
      {viewAs?.role === "client" ? (
        <Checkbox checked={viewAs.write} onChange={(write) => choose("client", write)} label="Autoriser les actions" hint={viewAs.write ? "Elles sont faites pour de vrai, en tant que client." : "Lecture seule."} />
      ) : null}
      <Link to="/back-office/organizations" className="font-medium underline">Retour au back office</Link>
    </div>
  );
}

export function Layout() {
  const { org, brand } = useOrg();
  const nav = useNavItems();
  const orgs = useOrganizations();

  return (
    <Frame title={org.brands.length > 1 ? brand.name : org.name} sidebar={(close) => <Sidebar nav={nav} orgs={orgs.data} onNavigate={close} />}>
      <StaffBanner />
      <JobToasts />
      <Outlet />
    </Frame>
  );
}
