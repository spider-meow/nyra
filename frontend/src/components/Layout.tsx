import { useState } from "react";
import { Outlet } from "react-router";
import { useOrg, useOrganizations } from "../lib/org";
import { Icon } from "./icons";
import { JobToasts } from "./layout/JobToasts";
import { Sidebar, useNavItems } from "./layout/Sidebar";
import { Logo } from "./Logo";
import { Button } from "./ui";

export function Layout() {
  const { org, brand } = useOrg();
  const nav = useNavItems();
  const orgs = useOrganizations();
  const [menuOpen, setMenuOpen] = useState(false);
  const sidebar = <Sidebar nav={nav} orgs={orgs.data} onNavigate={() => setMenuOpen(false)} />;

  return (
    <div className="min-h-screen md:grid md:grid-cols-[256px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-screen border-r border-line bg-side md:block">{sidebar}</aside>
      <div className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-side px-4 py-2 md:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <Logo size={30} />
          <p className="font-display text-[22px] leading-none">Nyra</p>
          <p className="truncate text-sm text-muted">· {org.brands.length > 1 ? brand.name : org.name}</p>
        </div>
        <Button variant="ghost" aria-expanded={menuOpen} aria-label={menuOpen ? "Fermer le menu" : "Ouvrir le menu"} className="w-11 px-0" onClick={() => setMenuOpen((open) => !open)}>
          <Icon name={menuOpen ? "close" : "menu"} size={20} />
        </Button>
      </div>
      {menuOpen ? <div className="border-b border-line bg-side md:hidden">{sidebar}</div> : null}
      <main className="min-w-0 px-4 py-6 md:px-14 md:py-10">
        <div className="mx-auto max-w-6xl">
          <JobToasts />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
