import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { jobLabel } from "../lib/format";
import { brandLink, useOrg, useOrganizations } from "../lib/org";
import { refreshAfter, useCancelJob, useInvalidate, useJobs, useOverview } from "../lib/queries";
import type { Job } from "../types";
import { useToast, type ToastInput } from "./feedback";
import { Icon, type IconName } from "./icons";
import { Logo } from "./Logo";
import { Button, cx } from "./ui";

export function Layout() {
  const { org, brand, link } = useOrg();
  const auth = useAuth();
  const orgs = useOrganizations();
  const overview = useOverview();
  const navigate = useNavigate();
  const location = useLocation();
  // Same page, other brand: /o/x/m/a/bibliotheque -> /o/x/m/b/bibliotheque
  const page = location.pathname.slice(link().length + 1) || "tableau-de-bord";
  const [menuOpen, setMenuOpen] = useState(false);
  const pending = overview.data?.dashboard.pending_review ?? 0;
  const expired = overview.data?.dashboard.expired_online ?? 0;

  const items: { to: string; label: string; icon: IconName; count?: number; tone?: "alert" | "neutral" | "muted" | "warn" }[] = [
    { to: link("tableau-de-bord"), label: "Tableau de bord", icon: "dashboard", count: expired, tone: "alert" },
    { to: link("a-traiter"), label: "À traiter", icon: "review", count: pending, tone: "neutral" },
    { to: link("bibliotheque"), label: "Bibliothèque", icon: "library", count: overview.data?.stats.reference_images, tone: "muted" },
    { to: link("images-du-site"), label: "Droits non vérifiés", icon: "alert", count: overview.data?.dashboard.unreferenced_online, tone: "warn" },
    { to: link("lectures"), label: "Sites et lectures", icon: "scan" },
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
                    item.tone === "warn" && "bg-urgent-soft font-medium text-urgent",
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
              onChange={(event) => navigate(`/o/${event.target.value}`)}
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
      {org.brands.length > 1 ? (
        <div className="relative -mt-3 flex h-11 items-center gap-2.5 rounded-xl border border-[#e2d9cc] bg-paper px-2.5">
          <span className="min-w-0 flex-1 leading-tight">
            <span className="block text-[11.5px] text-muted">Marque</span>
            <span className="block truncate text-[13.5px] font-medium">{brand.name}</span>
          </span>
          <Icon name="chevrons" size={14} className="text-muted" />
          <select
            aria-label="Changer de marque"
            className="absolute inset-0 cursor-pointer opacity-0"
            value={brand.slug}
            onChange={(event) => navigate(brandLink(org, event.target.value, page))}
          >
            {org.brands.map((item) => (
              <option key={item.id} value={item.slug}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
      ) : brand.name !== org.name ? (
        <p className="-mt-4 px-2.5 text-[12.5px] text-muted">{brand.name}</p>
      ) : null}
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

/**
 * Tasks of the brand as toasts: one per running task, one per group of
 * waiting tasks of the same kind, updated at every poll, then turned into
 * the outcome (done, stopped, failed) when the task ends.
 */
function JobToasts() {
  const { admin } = useOrg();
  const jobs = useJobs();
  const cancel = useCancelJob();
  const toast = useToast();
  const invalidate = useInvalidate();
  const seen = useRef<Map<string, Job["kind"]>>(new Map());
  // Lead job id -> its toast.
  const toastFor = useRef<Map<string, number>>(new Map());
  const [stopping, setStopping] = useState<Set<string>>(new Set());

  useEffect(() => {
    const data = jobs.data;
    if (!data) return;

    // Jobs that just left the active list: refresh what they changed, show how they ended.
    const activeIds = new Map(data.active.map((job) => [job.id, job.kind]));
    for (const [id, kind] of seen.current) {
      if (activeIds.has(id)) continue;
      invalidate(...refreshAfter[kind]);
      const existing = toastFor.current.get(id);
      toastFor.current.delete(id);
      if (data.last?.id === id) {
        const last = data.last;
        const outcome =
          last.status === "error"
            ? { tone: "error" as const, message: `${jobLabel[kind]} : échec`, description: last.message }
            : last.status === "cancelled"
              ? { tone: "info" as const, message: `${jobLabel[kind]} arrêtée`, description: last.message }
              : { tone: "success" as const, message: `${jobLabel[kind]} terminée`, description: last.message || undefined };
        if (existing !== undefined) toast.update(existing, { ...outcome, progress: undefined, action: undefined });
        else toast.show(outcome);
      } else if (existing !== undefined) {
        toast.dismiss(existing);
      }
    }
    seen.current = activeIds;

    // Running jobs one toast each; queued ones of the same kind share one.
    const rows: Job[][] = [];
    for (const job of data.active) {
      const same = job.status === "queued" ? rows.find((row) => row[0].status === "queued" && row[0].kind === job.kind) : undefined;
      if (same) same.push(job);
      else rows.push([job]);
    }
    const leads = new Set(rows.map((row) => row[0].id));
    for (const [id, toastId] of toastFor.current) {
      if (!leads.has(id)) {
        toast.dismiss(toastId);
        toastFor.current.delete(id);
      }
    }
    for (const row of rows) {
      const input = describe(row, data.active);
      const existing = toastFor.current.get(row[0].id);
      if (existing !== undefined) toast.update(existing, input);
      else toastFor.current.set(row[0].id, toast.show(input));
    }
    // invalidate/toast are stable; describe reads admin and stopping.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs.data, stopping, admin]);

  // Leaving the brand: its tasks' toasts go with it.
  useEffect(() => {
    const toasts = toastFor.current;
    return () => {
      for (const toastId of toasts.values()) toast.dismiss(toastId);
      toasts.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function describe(row: Job[], active: Job[]): ToastInput {
    const job = row[0];
    const queued = job.status === "queued" ? queuedText(job, active, admin) : null;
    const total = Number(job.progress.total || 0);
    const done = Number(job.progress.done || 0);
    return {
      tone: "loading",
      message: jobLabel[job.kind],
      description: queued
        ? [row.length > 1 ? groupLine(row.length, active) : capitalize(queued.line), queued.hint].filter(Boolean).join(". ")
        : job.message,
      progress: queued ? undefined : total > 0 ? Math.min(1, done / total) : null,
      action: admin
        ? {
            label: job.cancel_requested ? "Arrêt demandé…" : queued ? (row.length > 1 ? "Tout annuler" : "Annuler") : "Arrêter",
            disabled: job.cancel_requested || stopping.has(job.id),
            keep: true,
            onClick: () => void stop(row),
          }
        : undefined,
    };
  }

  async function stop(row: Job[]) {
    const lead = row[0];
    setStopping((current) => new Set(current).add(lead.id));
    try {
      await Promise.all(row.map((job) => cancel.mutateAsync(job.id)));
    } catch (error) {
      toast.show({ tone: "error", message: "La tâche n'a pas pu être arrêtée", description: errorMessage(error) });
    } finally {
      setStopping((current) => {
        const next = new Set(current);
        next.delete(lead.id);
        return next;
      });
    }
  }

  return null;
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Several queued requests of one kind: they'll be handled together, one after the other. */
function groupLine(count: number, active: Job[]): string {
  const running = active.find((other) => other.status === "running");
  return running ? `${count} demandes en attente, après « ${jobLabel[running.kind]} »` : `${count} demandes en attente`;
}

/** A queued job in plain words: behind another task, about to start, or waiting for a worker nobody started. */
function queuedText(job: Job, active: Job[], admin: boolean): { line: string; hint?: string } {
  const running = active.find((other) => other.status === "running");
  if (running) return { line: `en file d'attente, démarre après « ${jobLabel[running.kind]} »` };
  const waitedSeconds = (Date.now() - new Date(job.created_at).getTime()) / 1000;
  if (waitedSeconds < 60) return { line: "démarre dans un instant" };
  return {
    line: "en attente",
    hint: admin
      ? "Aucun worker ne traite les tâches pour l'instant : lancez « nyra worker » (ou vérifiez qu'il tourne). La tâche démarrera alors toute seule."
      : "Elle démarrera automatiquement dès que le serveur d'analyse sera disponible.",
  };
}

function initials(text: string): string {
  const words = text.split("@")[0].split(/[\s._-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}
