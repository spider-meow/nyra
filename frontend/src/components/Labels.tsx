import { useState } from "react";
import { errorMessage } from "../lib/api";
import { useLabelMutations, type Decision } from "../lib/labels";
import { useToast } from "./feedback";
import { Chip, cx } from "./ui";
import type { ImageLabel, LabelInfo } from "../types";

/** The labels of an image. A dashed chip with a "?" is only the algorithm's proposal; a solid one was decided by a person. */
export function LabelChips(props: { labels: ImageLabel[] }) {
  if (!props.labels.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {props.labels.map((label) => (
        <span
          key={label.label_id}
          title={label.source === "algo" ? `Proposé par l'algorithme${label.score === null ? "" : ` (score ${label.score.toFixed(2)})`} : à confirmer` : "Choisi par une personne"}
          className={cx(
            "rounded-full px-2 py-0.5 text-[11px]",
            label.source === "algo" ? "border border-dashed border-line-strong text-muted" : "bg-side text-ink-soft",
            label.kind === "type" && "font-medium",
          )}
        >
          {label.name}{label.source === "algo" ? " ?" : ""}
        </span>
      ))}
    </span>
  );
}

/** Content labels as filter chips ("all" when none is chosen); `counts` is how many images carry each. */
export function ContentFilter(props: { labels: LabelInfo[]; counts: Record<string, number>; value: string; onChange: (id: string) => void }) {
  const content = props.labels.filter((label) => label.kind === "content");
  if (!content.length) return null;
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Filtrer par contenu">
      <span className="text-[12.5px] text-muted">Contenu</span>
      {content.map((label) => (
        <Chip key={label.id} active={props.value === label.id} count={props.counts[label.id] ?? 0} onClick={() => props.onChange(props.value === label.id ? "all" : label.id)}>
          {label.name}
        </Chip>
      ))}
    </div>
  );
}

/** "This is a carafe": the selection becomes examples of a content label (created when the name is new), or is refused as one. */
export function ContentTagger(props: { labels: LabelInfo[]; images: { siteImageIds?: string[]; referenceIds?: string[] }; dark?: boolean }) {
  const { tagContent } = useLabelMutations();
  const toast = useToast();
  const [name, setName] = useState("");
  const trimmed = name.trim();

  function apply(decision: Decision) {
    tagContent.mutate(
      { name: trimmed, decision, ...props.images },
      {
        onSuccess: () => {
          toast.show({ tone: "success", message: decision === "yes" ? `Étiquette « ${trimmed.toLowerCase()} » posée` : `Plus de « ${trimmed.toLowerCase()} »`, description: "Les autres images sont proposées d'après ces exemples (trois au moins)." });
          setName("");
        },
        onError: (error) => toast.show({ tone: "error", message: "L'étiquette n'a pas été enregistrée", description: errorMessage(error) }),
      },
    );
  }

  const field = props.dark ? "border-white/20 bg-white/10 text-white placeholder:text-white/40" : "border-line-strong bg-paper";
  const button = props.dark ? "bg-peach text-ink hover:bg-accent-hover" : "bg-ink text-paper hover:opacity-90";
  return (
    <span className="flex items-center gap-2">
      <label htmlFor="content-label" className={props.dark ? "text-white/70" : "text-muted"}>Contenu</label>
      <input
        id="content-label"
        list="content-labels"
        value={name}
        maxLength={40}
        placeholder="carafe, verres…"
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter" && trimmed && !tagContent.isPending) apply("yes"); }}
        className={cx("h-9 w-36 rounded-md border px-2.5", field)}
      />
      <datalist id="content-labels">
        {props.labels.filter((label) => label.kind === "content").map((label) => <option key={label.id} value={label.name} />)}
      </datalist>
      <button type="button" className={cx("h-9 rounded-md px-3.5 text-[13.5px] font-medium disabled:cursor-not-allowed disabled:opacity-50", button)} disabled={!trimmed || tagContent.isPending} onClick={() => apply("yes")}>
        Étiqueter
      </button>
      <button type="button" className={cx("h-9 rounded-md px-3 text-[13.5px] disabled:cursor-not-allowed disabled:opacity-50", props.dark ? "text-white hover:bg-white/10" : "hover:bg-side")} disabled={!trimmed || tagContent.isPending} onClick={() => apply("no")}>
        Pas ça
      </button>
    </span>
  );
}
