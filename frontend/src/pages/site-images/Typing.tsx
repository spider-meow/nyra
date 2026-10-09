import { useCallback, useEffect, useState } from "react";
import { Modal, useToast } from "../../components/feedback";
import { Button, Chip, EmptyState } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useLabelMutations, useLabelSample, useLabels } from "../../lib/labels";
import { useInvalidate } from "../../lib/queries";
import type { ImageLabel, LabelInfo, LabelQuality, SiteImage } from "../../types";

/** The type of an image (logo, packshot…): a person's choice, else the algorithm's proposal. */
export function typeOf(labels: ImageLabel[]): ImageLabel | undefined {
  return labels.find((label) => label.kind === "type");
}

/** Filter chips by type: all, one of the types, or the images that have none yet. */
export function TypeFilter(props: { types: LabelInfo[]; counts: Record<string, number>; value: string; onChange: (value: string) => void }) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Filtrer par type d'image">
      <span className="text-[12.5px] text-muted">Type</span>
      <Chip active={props.value === "all"} onClick={() => props.onChange("all")}>Tous</Chip>
      {props.types.map((type) => (
        <Chip key={type.id} active={props.value === type.name} count={props.counts[type.name] ?? 0} onClick={() => props.onChange(props.value === type.name ? "all" : type.name)}>
          {type.name}
        </Chip>
      ))}
      <Chip active={props.value === "none"} count={props.counts.none ?? 0} onClick={() => props.onChange(props.value === "none" ? "all" : "none")}>Sans type</Chip>
    </div>
  );
}

/** "These are logos": the selected images become examples of that type (they leave the type they had). */
export function TypeButtons(props: { types: LabelInfo[]; items: SiteImage[]; onDone: () => void }) {
  const { decide } = useLabelMutations();
  const toast = useToast();
  const siteImageIds = props.items.flatMap((item) => item.ids);
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-white/70">Type</span>
      {props.types.map((type) => (
        <button
          key={type.id}
          type="button"
          disabled={decide.isPending}
          className="h-8 rounded-full bg-white/10 px-3 text-[13px] text-white hover:bg-white/20 disabled:opacity-50"
          onClick={() =>
            decide.mutate(
              { labelId: type.id, decision: "yes", siteImageIds },
              {
                onSuccess: props.onDone,
                onError: (error) => toast.show({ tone: "error", message: "Le type n'a pas été enregistré", description: errorMessage(error) }),
              },
            )
          }
        >
          {type.name}
        </button>
      ))}
    </span>
  );
}

const percent = (value: number | null) => (value === null ? "·" : `${Math.round(value * 100)} %`);

/** How the examples of each type predict themselves: the check that the types are told apart, and what is missing. */
function QualityTable(props: { types: LabelInfo[]; quality: Record<string, LabelQuality>; minExamples: number }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs text-muted">
        <tr><th className="py-1 font-medium">Type</th><th className="py-1 font-medium">Exemples</th><th className="py-1 font-medium">Précision</th><th className="py-1 font-medium">Rappel</th></tr>
      </thead>
      <tbody className="divide-y divide-line">
        {props.types.map((type) => {
          const found = props.quality[type.name];
          return (
            <tr key={type.id}>
              <td className="py-1.5 font-medium">{type.name}</td>
              <td className="py-1.5 tabular">{type.examples}{type.examples < props.minExamples ? <span className="text-urgent"> · encore {props.minExamples - type.examples}</span> : null}</td>
              <td className="py-1.5 tabular">{percent(found?.precision ?? null)}</td>
              <td className="py-1.5 tabular">{percent(found?.recall ?? null)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

type Step = { ids: string[]; labelId: string | null };

/**
 * Type images one at a time with the keys 1 to 4 (S skips, Backspace goes back). The images are unlike each
 * other and unlike the ones already typed, so a few dozen answers teach the most; the table says how well the
 * answers predict themselves.
 */
export function LabelingMode(props: { open: boolean; onClose: () => void }) {
  const [round, setRound] = useState(0);
  const [history, setHistory] = useState<Step[]>([]);
  const sample = useLabelSample(props.open, round);
  const labels = useLabels();
  const { decide } = useLabelMutations(true);
  const invalidate = useInvalidate();
  const toast = useToast();
  const items = sample.data?.items ?? [];
  const types = (labels.data?.labels ?? []).filter((label) => label.kind === "type");
  const current = items[history.length];

  const send = useCallback(
    (labelId: string, decision: "yes" | "clear", ids: string[]) =>
      decide.mutate({ labelId, decision, siteImageIds: ids }, { onError: (error) => toast.show({ tone: "error", message: "La réponse n'a pas été enregistrée", description: errorMessage(error) }) }),
    [decide, toast],
  );
  const answer = useCallback(
    (position: number) => {
      const type = types[position];
      if (!current || !type) return;
      send(type.id, "yes", current.ids);
      setHistory((steps) => [...steps, { ids: current.ids, labelId: type.id }]);
    },
    [current, types, send],
  );
  const back = useCallback(() => {
    if (decide.isPending) return; // the answer to undo must have arrived first
    const last = history[history.length - 1];
    if (!last) return;
    if (last.labelId) send(last.labelId, "clear", last.ids);
    setHistory(history.slice(0, -1));
  }, [history, send, decide.isPending]);

  useEffect(() => {
    if (!props.open) return;
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if (event.repeat || typing || event.metaKey || event.ctrlKey || event.altKey) return;
      if (/^[1-4]$/.test(event.key)) answer(Number(event.key) - 1);
      else if (event.key.toLowerCase() === "s" && current) setHistory((steps) => [...steps, { ids: current.ids, labelId: null }]);
      else if (event.key === "Backspace") back();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [props.open, answer, back, current]);

  function refresh() {
    invalidate("labels", "label-assignments");
  }

  function close() {
    refresh();
    props.onClose();
  }

  function more() {
    refresh();
    setHistory([]);
    setRound((value) => value + 1);
  }

  return (
    <Modal open={props.open} onClose={close} title="Étiqueter à la main" wide footer={<Button onClick={close}>Fermer</Button>}>
      {sample.isLoading ? (
        <p className="py-10 text-center text-sm text-muted">Choix des images…</p>
      ) : sample.error ? (
        <EmptyState title="Impossible de charger les images" body={errorMessage(sample.error)} />
      ) : !items.length ? (
        <EmptyState title="Plus rien à étiqueter" body="Toutes les images lues et indexées ont déjà un type, ou aucune n'est encore indexée." />
      ) : current ? (
        <div className="grid gap-4">
          <p className="text-sm text-muted">Image {history.length + 1} sur {items.length} · touches 1 à 4, S pour passer, Retour arrière pour revenir</p>
          <div className="flex justify-center rounded-xl bg-side">
            <img src={current.image || current.thumb} alt="" className="max-h-[48vh] object-contain" />
          </div>
          <p className="truncate text-center text-xs text-muted" title={current.url}>{current.url}{current.width && current.height ? ` · ${current.width} × ${current.height}` : ""}</p>
          <div className="flex flex-wrap justify-center gap-2">
            {types.map((type, position) => (
              <Button key={type.id} variant="primary" onClick={() => answer(position)}>{position + 1} · {type.name}</Button>
            ))}
            <Button onClick={() => current && setHistory((steps) => [...steps, { ids: current.ids, labelId: null }])}>S · Passer</Button>
            <Button onClick={back} disabled={!history.length || decide.isPending}>Retour</Button>
          </div>
        </div>
      ) : (
        <div className="grid gap-4">
          <p className="text-sm">Série terminée. Plus les exemples sont nombreux (au moins {labels.data?.min_examples ?? 3} par type), mieux les autres images sont proposées.</p>
          <QualityTable types={types} quality={labels.data?.quality ?? {}} minExamples={labels.data?.min_examples ?? 3} />
          <p className="text-xs text-muted">Estimation : chaque exemple est classé d'après les autres. Sur des images que vous n'avez pas choisies, le résultat sera un peu moins bon.</p>
          <div><Button variant="primary" onClick={more}>Encore des images</Button></div>
        </div>
      )}
    </Modal>
  );
}
