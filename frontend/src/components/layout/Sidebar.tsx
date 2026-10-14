import { NavLink, useLocation, useNavigate } from "react-router";
import { useAuth } from "../../lib/auth";
import { brandLink, useMe, useOrg } from "../../lib/org";
import { useOverview } from "../../lib/queries";
import type { Organization } from "../../types";
import { Dropdown } from "../Dropdown";
import { Icon, type IconName } from "../icons";
import { Logo } from "../Logo";
import { cx } from "../ui";

type NavItem = { to: string; label: string; icon: IconName; count?: number; tone?: "alert" | "neutral" | "muted" | "warn" };

/** The main entries (with their counters) and the rarely needed ones that sit at the bottom, next to the account. */
export function useNavItems(): { items: NavItem[]; footerItems: NavItem[] } {
  const { link, admin } = useOrg();
  const me = useMe();
  const overview = useOverview();
  const pending = overview.data?.dashboard.pending_review ?? 0;
  const expired = overview.data?.dashboard.expired_online ?? 0;
  const staffItems: NavItem[] = me.data?.staff ? [{ to: "/interne", label: "Back office Nyra", icon: "backoffice" }] : [];
  const adminFooterItems: NavItem[] = admin ? [{ to: link("statistiques"), label: "Stats pour les nerds", icon: "stats" }] : [];
  return {
    items: [
      { to: link("tableau-de-bord"), label: "Tableau de bord", icon: "dashboard", count: expired, tone: "alert" },
      { to: `${link("a-traiter")}?fenetre=3650`, label: "À traiter", icon: "review", count: pending, tone: "neutral" },
      { to: link("bibliotheque"), label: "Bibliothèque", icon: "library", count: overview.data?.stats.reference_images, tone: "muted" },
      { to: link("images-du-site"), label: "Droits non vérifiés", icon: "alert", count: overview.data?.dashboard.unreferenced_online, tone: "warn" },
      { to: link("lectures"), label: "Sites et lectures", icon: "scan" },
      ...staffItems,
    ],
    footerItems: [
      { to: link("rapports"), label: "Rapports", icon: "report" },
      ...adminFooterItems,
      { to: link("reglages"), label: "Réglages", icon: "settings" },
    ],
  };
}

function NavItems(props: { list: NavItem[]; onNavigate: () => void }) {
  return props.list.map((item) => (
    <NavLink
      key={item.to}
      to={item.to}
      onClick={props.onNavigate}
      className={({ isActive }) =>
        cx(
          "group flex h-10 items-center gap-2.5 rounded-md px-3 text-sm transition-colors",
          isActive ? "bg-yellow font-medium text-ink" : "text-ink-soft hover:bg-sunk hover:text-ink",
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon name={item.icon} className={isActive ? "text-ink" : "text-muted group-hover:text-ink-soft"} />
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
  ));
}

/** The brand on screen, the thing people switch most: its initials and name; with several brands, a list to switch (same page, other brand). */
function BrandSwitcher() {
  const { org, brand, link } = useOrg();
  const navigate = useNavigate();
  const location = useLocation();
  // Same page, other brand: /o/x/m/a/bibliotheque -> /o/x/m/b/bibliotheque
  const page = location.pathname.slice(link().length + 1) || "tableau-de-bord";
  const identity = (
    <span className="flex min-w-0 items-center gap-2.5">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-peach text-[13px] font-semibold text-bark-800" aria-hidden>
        {initials(brand.name)}
      </span>
      <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{brand.name}</span>
    </span>
  );
  if (org.brands.length <= 1) return <div className="flex h-12 items-center px-2.5">{identity}</div>;
  return (
    <Dropdown
      aria-label="Changer de marque"
      className="h-12 rounded-xl border border-line-strong bg-paper px-2.5 hover:bg-sunk"
      trigger={identity}
      value={brand.slug}
      options={org.brands.map((item) => ({ value: item.slug, label: item.name }))}
      onChange={(slug) => navigate(brandLink(org, slug, page))}
    />
  );
}

/** The organization and the role in it, as one quiet line under the email; with several organizations, a list (opening upwards) to switch. */
function OrgLine(props: { orgs: Organization[] | undefined }) {
  const { org } = useOrg();
  const navigate = useNavigate();
  const { orgs } = props;
  const line = `${org.name} · ${org.role === "admin" ? "Admin" : "Lecture"}`;
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
  return (
    <div className="flex items-center gap-2.5 border-t border-line-strong px-2.5 pt-3.5">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-peach text-xs font-semibold text-bark-800" aria-hidden>
        {initials(auth.email ?? "")}
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-[13px]" title={auth.email}>{auth.email}</span>
        <OrgLine orgs={props.orgs} />
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
      <div className="flex items-center gap-2.5 px-2.5">
        <Logo size={36} />
        <p className="font-display text-[26px] leading-none">Nyra</p>
      </div>
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

function initials(text: string): string {
  const words = text.split("@")[0].split(/[\s._-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}
