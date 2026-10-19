import { useAuth } from "../../lib/auth";
import { initials } from "../BrandMark";
import { NavItems, type NavItem } from "./Sidebar";

const STAFF_ITEMS: NavItem[] = [
  { to: "/back-office", label: "Vue d'ensemble", icon: "backoffice" },
  { to: "/back-office/organizations", label: "Organisations", icon: "library" },
  { to: "/back-office/server", label: "Serveur", icon: "server" },
  { to: "/back-office/billing", label: "Facturation", icon: "report" },
  { to: "/back-office/accounts", label: "Comptes", icon: "users" },
  { to: "/back-office/platform-admins", label: "Platform admins", icon: "user" },
  { to: "/", label: "Espaces clients", icon: "arrow" },
];

/** The back office's side column: no organization or brand on screen, so no switcher; the account block is the same. */
export function StaffSidebar(props: { onNavigate: () => void }) {
  const auth = useAuth();
  return (
    <div className="flex h-full flex-col gap-6 px-3.5 py-5">
      <p className="-mb-3 px-2.5 font-display text-xs leading-none text-muted">Nyra</p>
      <p className="flex h-12 items-center px-2.5 font-display text-[24px] leading-none">Back office</p>
      <nav aria-label="Navigation du back office" className="grid gap-0.5">
        <NavItems list={STAFF_ITEMS} onNavigate={props.onNavigate} end />
      </nav>
      <div className="mt-auto flex items-center gap-2.5 border-t border-line-strong px-2.5 pt-3.5">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-peach text-xs font-semibold text-bark-800" aria-hidden>
          {initials(auth.email ?? "")}
        </span>
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-[13px]" title={auth.email}>{auth.email}</span>
          <span className="block text-xs font-medium">platform admin</span>
          <button type="button" className="text-xs text-muted hover:text-ink hover:underline" onClick={() => void auth.signOut()}>
            Se déconnecter
          </button>
        </span>
      </div>
    </div>
  );
}
