import { useConfirm, useToast } from "../../components/feedback";
import { cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useJobs, useOverview, useStartJob } from "../../lib/queries";
import type { Removal } from "../../types";

const TEXT: Record<Removal, { label: string; tone: string }> = {
  pending: { label: "À vérifier", tone: "bg-sunk text-muted" },
  unread: { label: "Page non relue", tone: "bg-soon-soft text-soon" },
  online: { label: "Toujours en ligne", tone: "bg-expired-soft text-expired" },
  gone: { label: "Confirmé : plus en ligne", tone: "bg-ok-soft text-ok" },
};

/** What the last read says about a photo marked removed. */
export function RemovalBadge(props: { removal: Removal }) {
  const { label, tone } = TEXT[props.removal];
  return <span className={cx("inline-flex rounded-md px-2 py-px text-xs font-medium whitespace-nowrap", tone)}>{label}</span>;
}

/** Reads every address of the brand again, from the start, after a confirmation: the way to learn what the sites show now. */
export function useFullRead() {
  const start = useStartJob();
  const confirm = useConfirm();
  const toast = useToast();
  // As many pages as allowed: a page left unread cannot confirm that a photo is gone.
  const maxPages = useOverview().data?.defaults.max_pages_limit;
  const reading = (useJobs().data?.active ?? []).some((job) => job.kind === "crawl");

  async function read() {
    const ok = await confirm({
      title: "Relire les sites ?",
      body: <p>Nyra relit tous les sites de la marque, depuis le début et jusqu'à autant de pages que possible, puis compare à nouveau. Cela peut prendre plusieurs minutes ; la progression s'affiche en bas de l'écran.</p>,
      confirm: "Relire les sites",
    });
    if (!ok) return;
    start.mutate({ kind: "crawl", body: { fresh: true, max_pages: maxPages } }, { onError: (error) => toast.show({ tone: "error", message: "La lecture n'a pas démarré", description: errorMessage(error) }) });
  }

  return { read: () => void read(), busy: start.isPending || reading };
}
