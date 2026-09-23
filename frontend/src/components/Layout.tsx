import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { jobLabel } from "../lib/format";
import { useOrg, useOrganizations } from "../lib/org";
import { refreshAfter, useCancelJob, useInvalidate, useJobs, useOverview } from "../lib/queries";
import type { Job } from "../types";
import { useToast } from "./feedback";
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

  const items = [
    { to: link("tableau-de-bord"), label: "Tableau de bord", count: expired, tone: "alert" as const },
    { to: link("a-traiter"), label: "À traiter", count: pending, tone: "neutral" as const },
    { to: link("bibliotheque"), label: "Bibliothèque", count: overview.data?.stats.reference_images, tone: "muted" as const },
    { to: link("lectures"), label: "Lectures du site" },
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
        <p className="text-[15px] font-semibold tracking-tight">Nyra</p>
        <p className="text-xs text-muted">Droits à l'image sous surveillance</p>
      </div>
      {orgs.data && orgs.data.length > 1 ? (
        <select
          aria-label="Organisation"
          className="h-9 rounded-lg border border-line-strong bg-paper px-2 text-sm"
          value={org.slug}
          onChange={(event) => navigate(`/o/${event.target.value}/tableau-de-bord`)}
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
        <p className="font-semibold">Nyra · {org.name}</p>
        <Button size="sm" variant="ghost" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
          {menuOpen ? "Fermer" : "Menu"}
        </Button>
      </div>
      {menuOpen ? <div className="border-b border-line bg-side md:hidden">{sidebar}</div> : null}
      <main className="min-w-0 px-4 py-6 md:px-10 md:py-8">
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
          <div key={job.id} className="flex items-center gap-4 rounded-xl border border-line bg-paper px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm">
                <span className="font-medium">{jobLabel[job.kind]}</span>
                <span className="text-muted"> · {job.status === "queued" ? "en attente d'un worker" : job.message}</span>
              </p>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-canvas" role="progressbar" aria-valuenow={percent ?? undefined} aria-valuemin={0} aria-valuemax={100}>
                {percent === null ? (
                  <div className={cx("h-full w-1/4 rounded-full", job.status === "queued" ? "bg-line-strong" : "progress-indeterminate bg-ink")} />
                ) : (
                  <div className="h-full rounded-full bg-ink transition-[width]" style={{ width: `${Math.max(3, percent)}%` }} />
                )}
              </div>
            </div>
            {percent !== null ? <span className="text-xs text-muted tabular">{percent} %</span> : null}
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
