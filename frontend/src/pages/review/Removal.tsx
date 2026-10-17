import { useConfirm, useToast } from "../../components/feedback";
import { Button, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useJobs, useOverview, useStartJob } from "../../lib/queries";
import { plural } from "../../lib/format";
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

/** Reads every address of the brand again, from the start: the way to learn whether the photos marked removed are gone. */
export function VerifyRemovals(props: { unchecked: number }) {
  const start = useStartJob();
  const confirm = useConfirm();
  const toast = useToast();
  // As many pages as allowed: a page left unread cannot confirm that a photo is gone.
  const maxPages = useOverview().data?.defaults.max_pages_limit;
  const reading = (useJobs().data?.active ?? []).some((job) => job.kind === "crawl");

  async function verify() {
    const ok = await confirm({
      title: "Relire les sites ?",
      body: <p>Nyra relit tous les sites de la marque, depuis le début et jusqu'à autant de pages que possible, puis compare à nouveau. Cela peut prendre plusieurs minutes ; la progression s'affiche en bas de l'écran.</p>,
      confirm: "Vérifier les retraits",
    });
    if (!ok) return;
    start.mutate({ kind: "crawl", body: { fresh: true, max_pages: maxPages } }, { onError: (error) => toast.show({ tone: "error", message: "La lecture n'a pas démarré", description: errorMessage(error) }) });
  }

  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-sunk px-4 py-3">
      <p className="text-sm text-muted">{plural(props.unchecked, "retrait", "retraits")} à vérifier. Une lecture complète des sites dira s'ils sont bien retirés.</p>
      <Button size="sm" variant="primary" loading={start.isPending || reading} onClick={() => void verify()}>Vérifier les retraits</Button>
    </div>
  );
}
