import { NavLink, useLocation, useNavigate, useNavigation } from "react-router";
import { useAuth } from "../../lib/auth";
import { brandLink, useMe, useOrg } from "../../lib/org";
import { useOverview } from "../../lib/queries";
import type { Organization } from "../../types";
import { BrandMark, initials } from "../BrandMark";
import { Dropdown } from "../Dropdown";
import { Icon, type IconName } from "../icons";
import { cx } from "../ui";

export type NavItem = { to: string; label: string; icon: IconName; count?: number; tone?: "alert" | "neutral" | "muted" | "warn"; newTab?: boolean };

/** The main entries (with their counters) and the rarely needed ones that sit at the bottom, next to the account. */
export function useNavItems(): { items: NavItem[]; footerItems: NavItem[] } {
  const { link, admin } = useOrg();
  const me = useMe();
  const overview = useOverview();
  const pending = overview.data?.dashboard.pending_review ?? 0;
  const expired = overview.data?.dashboard.expired_online ?? 0;
  const staffItems: NavItem[] = me.data?.staff ? [{ to: "/back-office", label: "Back office Nyra", icon: "backoffice", newTab: true }] : [];
  const adminFooterItems: NavItem[] = admin ? [{ to: link("statistics"), label: "Stats pour les nerds", icon: "stats" }] : [];
  return {
    items: [
      { to: link("dashboard"), label: "Tableau de bord", icon: "dashboard", count: expired, tone: "alert" },
      { to: `${link("review")}?window=3650`, label: "À traiter", icon: "review", count: pending, tone: "neutral" },
      { to: link("visuals"), label: "Visuels à remplacer", icon: "report" },
      { to: link("library"), label: "Bibliothèque", icon: "library", count: overview.data?.stats.reference_images, tone: "muted" },
      { to: link("online-images"), label: "Images en ligne", icon: "alert", count: overview.data?.dashboard.unreferenced_online, tone: "warn" },
      { to: link("scans"), label: "Sites et lectures", icon: "scan" },
    ],
    footerItems: [
      { to: link("reports"), label: "Rapports", icon: "report" },
      ...adminFooterItems,
      { to: link("settings"), label: "Réglages", icon: "settings" },
      ...staffItems,
    ],
  };
}

const navItemClass = (isActive: boolean) =>
  cx(
    "group flex h-10 items-center gap-2.5 rounded-md px-3 text-sm transition-colors",
    isActive ? "bg-yellow font-medium text-ink" : "text-ink-soft hover:bg-sunk hover:text-ink",
  );

export function NavItems(props: { list: NavItem[]; onNavigate: () => void; end?: boolean }) {
  // The item clicked lights up at once; the one being left goes dark, so that two are never lit while the next page loads.
  const loading = useNavigation().state !== "idle";
  return props.list.map((item) =>
    item.newTab ? (
      // Another page of the product, opened beside this one: never "active", and the arrow says so.
      <a key={item.to} href={item.to} target="_blank" rel="noopener noreferrer" className={navItemClass(false)}>
        <Icon name={item.icon} className="text-muted group-hover:text-ink-soft" />
        <span className="flex-1">{item.label}</span>
        <Icon name="external" size={15} className="text-muted group-hover:text-ink-soft" />
        <span className="sr-only">(s'ouvre dans un nouvel onglet)</span>
      </a>
    ) : (
      <NavLink key={item.to} to={item.to} end={props.end} onClick={props.onNavigate} className={({ isActive, isPending }) => navItemClass(isPending || (isActive && !loading))}>
        {({ isActive, isPending }) => (
          <>
            <Icon name={item.icon} className={isPending || (isActive && !loading) ? "text-ink" : "text-muted group-hover:text-ink-soft"} />
            <span className="flex-1">{item.label}</span>
            {item.count ? (
              <span
                className={cx(
                  "inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11.5px] tabular",
                  item.tone === "alert" && "bg-expired-soft font-medium text-expired",
                  item.tone === "neutral" && "bg-ink font-medium text-paper",
                  item.tone === "muted" && "font-normal text-muted",
                  item.tone === "warn" && "bg-urgent-soft font-medium text-urgent",
                )}
              >
                {item.count}
              </span>
            ) : null}
          </>
        )}
      </NavLink>
    ),
  );
}

/** The brand on screen, at the top where the product's name used to be: its logo and name; with several brands, a list to switch (same page, other brand). */
function BrandSwitcher() {
  const { org, brand, link } = useOrg();
  const navigate = useNavigate();
  const location = useLocation();
  // Same page, other brand: /o/x/b/a/library -> /o/x/b/other/library
  const page = location.pathname.slice(link().length + 1) || "dashboard";
  const identity = (
    <span className="flex min-w-0 items-center gap-2.5">
      <BrandMark brand={brand} size={30} />
      <span className="min-w-0 flex-1 truncate font-display text-[16px] leading-none" title={brand.name}>{brand.name}</span>
    </span>
  );
  if (org.brands.length <= 1) return <div className="flex h-12 items-center px-2.5">{identity}</div>;
  return (
    <Dropdown
      aria-label="Changer de marque"
      className="h-12 rounded-md px-2.5 transition-colors hover:bg-sunk"
      trigger={identity}
      value={brand.slug}
      options={org.brands.map((item) => ({ value: item.slug, label: item.name }))}
      onChange={(slug) => navigate(brandLink(org, slug, page))}
    />
  );
}

/** The organization and the role in it, as one quiet line under the email; with several organizations, a list (opening upwards) to switch. */
function OrgLine(props: { orgs: Organization[] | undefined }) {
  const { org, staffView, viewAs } = useOrg();
  const navigate = useNavigate();
  const { orgs } = props;
  const role = staffView ? (viewAs ? `Vue ${viewAs.role === "admin" ? "administrateur" : "client"}` : "Équipe Nyra") : org.role === "admin" ? "Admin" : "Lecture";
  const line = `${org.name} · ${role}`;
  if (!orgs || orgs.length < 2) return <span className="block truncate text-xs text-muted" title={line}>{line}</span>;
  return (
    <Dropdown
      up
      aria-label="Changer d'organisation"
      className="text-xs text-muted hover:text-ink"
      trigger={line}
      value={org.slug}
      options={orgs.map((item) => ({ value: item.slug, label: item.name }))}
      onChange={(slug) => navigate(`/o/${slug}`)}
    />
  );
}

function Account(props: { orgs: Organization[] | undefined }) {
  const auth = useAuth();
  const { platformAdmin } = useOrg();
  return (
    <div className="flex items-center gap-2.5 border-t border-line-strong px-2.5 pt-3.5">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-peach text-xs font-semibold text-bark-800" aria-hidden>
        {initials(auth.email ?? "")}
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-[13px]" title={auth.email}>{auth.email}</span>
        <OrgLine orgs={props.orgs} />
        {platformAdmin ? <span className="block text-xs font-medium">platform admin</span> : null}
        <button type="button" className="text-xs text-muted hover:text-ink hover:underline" onClick={() => void auth.signOut()}>
          Se déconnecter
        </button>
      </span>
    </div>
  );
}

/**
 * The brand on screen, navigation, and the account block (with the organization). The queries are read by
 * the layout (always mounted) and passed down: this block is rendered twice
 * (side column and mobile menu) and opening the menu must not refetch anything.
 */
export function Sidebar(props: { nav: ReturnType<typeof useNavItems>; orgs: Organization[] | undefined; onNavigate: () => void }) {
  const { nav, onNavigate } = props;
  return (
    <div className="flex h-full flex-col gap-6 px-3.5 py-5">
      <p className="-mb-3 px-2.5 font-display text-xs leading-none text-muted">Nyra</p>
      <BrandSwitcher />
      <nav aria-label="Navigation principale" className="grid gap-0.5">
        <NavItems list={nav.items} onNavigate={onNavigate} />
      </nav>
      <nav aria-label="Compte et réglages" className="mt-auto -mb-3 grid gap-0.5">
        <NavItems list={nav.footerItems} onNavigate={onNavigate} />
      </nav>
      <Account orgs={props.orgs} />
    </div>
  );
}
