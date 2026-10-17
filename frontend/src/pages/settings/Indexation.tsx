import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { useToast } from "../../components/feedback";
import { CursorTip } from "../../components/CursorTip";
import { Button } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useJobs, useLibrary, useStartJob } from "../../lib/queries";

/** Indexing prepares the visuals for the comparison. It starts by itself after every upload; this catches up on the ones it missed. */
export function Indexation() {
  const toast = useToast();
  const start = useStartJob();
  const library = useLibrary().data;
  const pending = (library?.items ?? []).filter((item) => !item.indexed).length;
  const allDone = Boolean(library) && pending === 0;
  const running = (useJobs().data?.active ?? []).some((job) => job.kind === "index");
  const launch = () => start.mutate({ kind: "index" }, { onError: (error) => toast.show({ tone: "error", message: "L'indexation n'a pas démarré", description: errorMessage(error) }) });
  return (
    <SettingsBlock
      title="Maintenance"
      description="L'indexation prépare chaque visuel pour la comparaison avec les sites : empreintes, vignette et analyse. Elle se lance seule après chaque ajout ; ce bouton rattrape les visuels qui ne l'ont pas été."
    >
      <SettingsRow
        label={pending ? `${pending.toLocaleString("fr-FR")} visuels pas encore indexés` : "Tous les visuels sont indexés"}
        hint={running ? "Une indexation est en cours. Sa progression s'affiche en bas de l'écran." : "Elle peut prendre quelques minutes, selon le nombre de visuels."}
      >
        <CursorTip tip={allDone && !running ? "Tous les visuels sont déjà indexés : il n'y a rien à rattraper." : undefined}>
          <Button variant="primary" loading={start.isPending || running} disabled={allDone} onClick={launch}>Lancer l'indexation</Button>
        </CursorTip>
      </SettingsRow>
    </SettingsBlock>
  );
}
