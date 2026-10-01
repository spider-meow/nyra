import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../lib/api";
import { jobLabel } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { refreshAfter, useCancelJob, useInvalidate, useJobs } from "../../lib/queries";
import type { Job } from "../../types";
import { useToast, type ToastInput, type Toaster } from "../feedback";

/**
 * Tasks of the brand as toasts: one per running task, one per group of
 * waiting tasks of the same kind, updated at every poll, then turned into
 * the outcome (done, stopped, failed) when the task ends.
 */
export function JobToasts() {
  const { admin } = useOrg();
  const jobs = useJobs();
  const toast = useToast();
  const invalidate = useInvalidate();
  const seen = useRef<Map<string, Job["kind"]>>(new Map());
  // Lead job id -> its toast.
  const toastFor = useRef<Map<string, number>>(new Map());
  const { stopping, stop } = useStopJobs(toast);

  useEffect(() => {
    const data = jobs.data;
    if (!data) return;

    // Jobs that just left the active list: refresh what they changed, show how they ended.
    const activeIds = new Map(data.active.map((job) => [job.id, job.kind]));
    for (const [id, kind] of seen.current) {
      if (activeIds.has(id)) continue;
      invalidate(...refreshAfter[kind]);
      showOutcome(toast, toastFor.current, id, kind, data.last);
    }
    seen.current = activeIds;

    const rows = toastRows(data.active);
    const leads = new Set(rows.map((row) => row[0].id));
    for (const [id, toastId] of toastFor.current) {
      if (!leads.has(id)) {
        toast.dismiss(toastId);
        toastFor.current.delete(id);
      }
    }
    for (const row of rows) {
      const input = describe(row, data.active, admin, stopping, () => void stop(row));
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

  return null;
}

/** Which tasks are being stopped, and the request that stops a row (the whole group of queued tasks). */
function useStopJobs(toast: Toaster) {
  const cancel = useCancelJob();
  const [stopping, setStopping] = useState<Set<string>>(new Set());

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

  return { stopping, stop };
}

/** A task that just ended: its toast becomes the outcome (or goes away if it is not the last one). */
function showOutcome(toast: Toaster, toastFor: Map<string, number>, id: string, kind: Job["kind"], last: Job | null) {
  const existing = toastFor.get(id);
  toastFor.delete(id);
  if (last?.id !== id) {
    if (existing !== undefined) toast.dismiss(existing);
    return;
  }
  const outcome =
    last.status === "error"
      ? { tone: "error" as const, message: `${jobLabel[kind]} : échec`, description: last.message }
      : last.status === "cancelled"
        ? { tone: "info" as const, message: `${jobLabel[kind]} arrêtée`, description: last.message }
        : { tone: "success" as const, message: `${jobLabel[kind]} terminée`, description: last.message || undefined };
  if (existing !== undefined) toast.update(existing, { ...outcome, progress: undefined, action: undefined });
  else toast.show(outcome);
}

/** Running jobs one row each; queued ones of the same kind share one. */
function toastRows(active: Job[]): Job[][] {
  const rows: Job[][] = [];
  for (const job of active) {
    const same = job.status === "queued" ? rows.find((row) => row[0].status === "queued" && row[0].kind === job.kind) : undefined;
    if (same) same.push(job);
    else rows.push([job]);
  }
  return rows;
}

function describe(row: Job[], active: Job[], admin: boolean, stopping: Set<string>, onStop: () => void): ToastInput {
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
          onClick: onStop,
        }
      : undefined,
  };
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
