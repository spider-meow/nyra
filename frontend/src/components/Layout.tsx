import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { jobLabel } from "../lib/format";
import { brandLink, useOrg, useOrganizations } from "../lib/org";
import { refreshAfter, useCancelJob, useInvalidate, useJobs, useOverview } from "../lib/queries";
import type { Job } from "../types";
import { useToast, type ToastInput } from "./feedback";
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

  const items = [
    { to: link("tableau-de-bord"), label: "Tableau de bord", count: expired, tone: "alert" as const },
    { to: link("a-traiter"), label: "À traiter", count: pending, tone: "neutral" as const },
    { to: link("bibliotheque"), label: "Bibliothèque", count: overview.data?.stats.reference_images, tone: "muted" as const },
    { to: link("images-du-site"), label: "Droits non vérifiés", count: overview.data?.dashboard.unreferenced_online, tone: "warn" as const },
    { to: link("lectures"), label: "Sites et lectures" },
    { to: link("rapports"), label: "Rapports" },
    { to: link("reglages"), label: "Réglages" },
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
              "flex items-center justify-between rounded-lg px-3 py-2 text-sm",
              isActive ? "bg-paper font-medium text-ink shadow-[0_0_0_1px_var(--color-line)]" : "text-ink-soft hover:bg-canvas hover:text-ink",
            )
          }
        >
          <span>{item.label}</span>
          {item.count ? (
            <span
              className={cx(
                "rounded-full px-1.5 text-xs tabular",
                item.tone === "alert" && "bg-expired-soft font-medium text-expired",
                item.tone === "neutral" && "bg-ink text-white",
                item.tone === "muted" && "text-muted",
                item.tone === "warn" && "bg-urgent-soft font-medium text-urgent",
              )}
            >
              {item.count}
            </span>
          ) : null}
        </NavLink>
      ))}
    </nav>
  );

  const sidebar = (
    <div className="flex h-full flex-col gap-6 p-4">
      <div className="px-3 pt-1">
        <div className="flex items-center gap-2">
          <Logo size={22} />
          <p className="text-[15px] font-semibold tracking-tight">Nyra</p>
        </div>
        <p className="mt-1 text-xs text-muted">Droits à l'image sous surveillance</p>
      </div>
      {orgs.data && orgs.data.length > 1 ? (
        <select
          aria-label="Organisation"
          className="h-9 rounded-lg border border-line-strong bg-paper px-2 text-sm"
          value={org.slug}
          onChange={(event) => navigate(`/o/${event.target.value}`)}
        >
          {orgs.data.map((item) => (
            <option key={item.org_id} value={item.slug}>
              {item.name}
            </option>
          ))}
        </select>
      ) : (
        <p className="px-3 text-sm font-medium">{org.name}</p>
      )}
      {org.brands.length > 1 ? (
        <div className="-mt-3 grid gap-1">
          <label htmlFor="brand-select" className="px-3 text-xs text-muted">Marque</label>
          <select
            id="brand-select"
            className="h-9 rounded-lg border border-line-strong bg-paper px-2 text-sm"
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
        <p className="-mt-4 px-3 text-xs text-muted">{brand.name}</p>
      ) : null}
      {nav}
      <div className="mt-auto border-t border-line px-3 pt-4">
        <p className="truncate text-xs text-ink-soft" title={auth.email}>
          {auth.email}
        </p>
        <p className="text-xs text-muted">{org.role === "admin" ? "Administrateur" : "Lecture et validation"}</p>
        <button type="button" className="mt-2 text-xs text-muted underline-offset-2 hover:text-ink hover:underline" onClick={() => void auth.signOut()}>
          Se déconnecter
        </button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen md:grid md:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-screen border-r border-line bg-side md:block">{sidebar}</aside>
      <div className="flex items-center justify-between border-b border-line bg-side px-4 py-3 md:hidden">
        <div className="flex items-center gap-2 font-semibold">
          <Logo size={20} />
          <p>Nyra · {org.brands.length > 1 ? brand.name : org.name}</p>
        </div>
        <Button size="sm" variant="ghost" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
          {menuOpen ? "Fermer" : "Menu"}
        </Button>
      </div>
      {menuOpen ? <div className="border-b border-line bg-side md:hidden">{sidebar}</div> : null}
      <main className="min-w-0 px-4 py-6 md:px-10 md:py-8">
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
