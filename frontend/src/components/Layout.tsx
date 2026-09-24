import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { jobLabel } from "../lib/format";
import { useOrg, useOrganizations } from "../lib/org";
import { refreshAfter, useCancelJob, useInvalidate, useJobs, useOverview } from "../lib/queries";
import type { Job } from "../types";
import { useToast } from "./feedback";
import { Icon, type IconName } from "./icons";
import { Logo } from "./Logo";
import { Button, cx } from "./ui";

export function Layout() {
  const { org, link } = useOrg();
  const auth = useAuth();
  const orgs = useOrganizations();
  const overview = useOverview();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const pending = overview.data?.dashboard.pending_review ?? 0;
  const expired = overview.data?.dashboard.expired_online ?? 0;

  const items: { to: string; label: string; icon: IconName; count?: number; tone?: "alert" | "neutral" | "muted" }[] = [
    { to: link("tableau-de-bord"), label: "Tableau de bord", icon: "dashboard", count: expired, tone: "alert" },
    { to: link("a-traiter"), label: "À traiter", icon: "review", count: pending, tone: "neutral" },
    { to: link("bibliotheque"), label: "Bibliothèque", icon: "library", count: overview.data?.stats.reference_images, tone: "muted" },
    { to: link("lectures"), label: "Lectures du site", icon: "scan" },
    { to: link("rapports"), label: "Rapports", icon: "report" },
    { to: link("reglages"), label: "Réglages", icon: "settings" },
  ];

  const nav = (
    <nav aria-label="Navigation principale" className="grid gap-0.5">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          onClick={() => setMenuOpen(false)}
          className={({ isActive }) =>
            cx(
              "group flex h-10 items-center gap-2.5 rounded-[10px] px-3 text-sm transition-colors",
              isActive ? "bg-paper font-medium text-ink shadow-lift" : "text-ink-soft hover:bg-paper/60 hover:text-ink",
            )
          }
        >
          {({ isActive }) => (
            <>
              <Icon name={item.icon} className={isActive ? "text-bark" : "text-muted group-hover:text-ink-soft"} />
              <span className="flex-1">{item.label}</span>
              {item.count ? (
                <span
                  className={cx(
                    "inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11.5px] tabular",
                    item.tone === "alert" && "bg-expired-soft font-medium text-expired",
                    item.tone === "neutral" && "bg-ink font-medium text-paper",
                    item.tone === "muted" && "font-normal text-muted",
                  )}
                >
                  {item.count}
                </span>
              ) : null}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );

  const sidebar = (
    <div className="flex h-full flex-col gap-6 px-3.5 py-5">
      <div className="flex items-center gap-2.5 px-2.5">
        <Logo size={26} />
        <p className="font-display text-[26px] leading-none">Nyra</p>
      </div>
      <div className="relative flex h-12 items-center gap-2.5 rounded-xl border border-[#e2d9cc] bg-sunk px-2.5">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-bark text-xs font-semibold text-paper" aria-hidden>
          {initials(org.name)}
        </span>
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-[13.5px] font-medium">{org.name}</span>
          <span className="block text-[11.5px] text-muted">{org.role === "admin" ? "Administrateur" : "Lecture et validation"}</span>
        </span>
        {orgs.data && orgs.data.length > 1 ? (
          <>
            <Icon name="chevrons" size={14} className="text-muted" />
            <select
              aria-label="Changer d'organisation"
              className="absolute inset-0 cursor-pointer opacity-0"
              value={org.slug}
              onChange={(event) => navigate(`/o/${event.target.value}/tableau-de-bord`)}
            >
              {orgs.data.map((item) => (
                <option key={item.org_id} value={item.slug}>
                  {item.name}
                </option>
              ))}
            </select>
          </>
        ) : null}
      </div>
      {nav}
      <div className="mt-auto flex items-center gap-2.5 border-t border-[#e2d9cc] px-2.5 pt-3.5">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-peach text-xs font-semibold text-bark-800" aria-hidden>
          {initials(auth.email ?? "")}
        </span>
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-[13px]" title={auth.email}>{auth.email}</span>
          <button type="button" className="text-xs text-muted hover:text-ink hover:underline" onClick={() => void auth.signOut()}>
            Se déconnecter
          </button>
        </span>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen md:grid md:grid-cols-[256px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-screen border-r border-line bg-side md:block">{sidebar}</aside>
      <div className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-side px-4 py-2 md:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <Logo size={22} />
          <p className="font-display text-[22px] leading-none">Nyra</p>
          <p className="truncate text-sm text-muted">· {org.name}</p>
        </div>
        <Button variant="ghost" aria-expanded={menuOpen} aria-label={menuOpen ? "Fermer le menu" : "Ouvrir le menu"} className="w-11 px-0" onClick={() => setMenuOpen((open) => !open)}>
          <Icon name={menuOpen ? "close" : "menu"} size={20} />
        </Button>
      </div>
      {menuOpen ? <div className="border-b border-line bg-side md:hidden">{sidebar}</div> : null}
      <main className="min-w-0 px-4 py-6 md:px-14 md:py-10">
        <div className="mx-auto max-w-6xl">
          <JobBar />
          <Outlet />
        </div>
      </main>
    </div>
  );
}

function JobBar() {
  const { admin } = useOrg();
  const jobs = useJobs();
  const cancel = useCancelJob();
  const toast = useToast();
  const invalidate = useInvalidate();
  const seen = useRef<Map<string, Job["kind"]>>(new Map());

  // Notice jobs that just left the active list, and refresh what they changed.
  useEffect(() => {
    const data = jobs.data;
    if (!data) return;
    const active = new Map(data.active.map((job) => [job.id, job.kind]));
    for (const [id, kind] of seen.current) {
      if (active.has(id)) continue;
      invalidate(...refreshAfter[kind]);
      if (data.last?.id === id) {
        const last = data.last;
        toast(last.status === "error" ? `${jobLabel[kind]} : ${last.message}` : last.message || `${jobLabel[kind]} terminée.`, last.status === "error" ? "error" : "success");
      }
    }
    seen.current = active;
    // invalidate/toast are stable enough; only react to new data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs.data]);

  const active = jobs.data?.active ?? [];
  if (!active.length) return null;
  return (
    <div className="mb-6 grid gap-2">
      {active.map((job) => {
        const total = Number(job.progress.total || 0);
        const done = Number(job.progress.done || 0);
        const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
        return (
          <div key={job.id} className="flex items-center gap-4 rounded-2xl border border-line bg-paper px-4 py-3.5">
            <span className="hidden h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-peach-soft text-bark-700 sm:grid">
              <Icon name={job.kind === "report" ? "report" : job.kind === "index" ? "library" : "scan"} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm">
                <span className="font-medium">{jobLabel[job.kind]}</span>
                <span className="text-muted"> · {job.status === "queued" ? "en attente d'un worker" : job.message}</span>
              </p>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-side" role="progressbar" aria-valuenow={percent ?? undefined} aria-valuemin={0} aria-valuemax={100}>
                {percent === null ? (
                  <div className={cx("h-full w-1/4 rounded-full", job.status === "queued" ? "bg-line-strong" : "progress-indeterminate bg-bark")} />
                ) : (
                  <div className="h-full rounded-full bg-bark transition-[width]" style={{ width: `${Math.max(3, percent)}%` }} />
                )}
              </div>
            </div>
            {percent !== null ? <span className="text-[13px] text-ink-soft tabular">{percent} %</span> : null}
            {admin ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={job.cancel_requested || cancel.isPending}
                onClick={() => cancel.mutate(job.id, { onError: (error) => toast(errorMessage(error), "error") })}
              >
                {job.cancel_requested ? "Arrêt…" : job.status === "queued" ? "Annuler" : "Arrêter"}
              </Button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function initials(text: string): string {
  const words = text.split("@")[0].split(/[\s._-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}
