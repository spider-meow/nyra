import { useState } from "react";
import { useToast } from "../../components/feedback";
import { Input, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { usePackshotFeedback } from "../../lib/siteImages";
import type { SiteImage } from "../../types";

const measure = (value: number | null) => (value === null ? "—" : value.toLocaleString("fr-FR", { maximumFractionDigits: 2 }));

/** For the Nyra team only (AXE-668): the detector's two measures on the card, and "c'est / ce n'est pas un packshot", kept with them to calibrate the detection. Nothing here changes it. */
export function PackshotFeedback({ item }: { item: SiteImage }) {
  const { give, takeBack } = usePackshotFeedback();
  const toast = useToast();
  const [note, setNote] = useState(item.packshot_feedback?.note ?? "");
  const { packshot_measures: measures, packshot_feedback: given } = item;
  if (!measures) return null;
  const onError = (error: unknown) => toast.show({ tone: "error", message: "Le retour n'a pas été enregistré", description: errorMessage(error) });
  // The verdict already given is pressed: pressing it again takes it back.
  const verdict = (yes: boolean) => (given?.is_packshot === yes ? takeBack.mutate(item.id, { onError }) : give.mutate({ id: item.id, is_packshot: yes, note }, { onError }));
  return (
    <div className="mt-1 grid gap-1.5 border-t border-line pt-2 text-xs">
      <p className="text-muted">produit {measure(measures.product)} · bord {measure(measures.border)} · {measures.detected ? "détectée" : "non détectée"}</p>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Retour sur la détection">
        {([true, false] as const).map((yes) => (
          <button
            key={String(yes)}
            type="button"
            aria-pressed={given?.is_packshot === yes}
            disabled={give.isPending || takeBack.isPending}
            onClick={() => verdict(yes)}
            className={cx("rounded-full border px-2 py-0.5 disabled:opacity-60", given?.is_packshot === yes ? "border-ink bg-ink text-white" : "border-line text-ink-soft hover:border-ink-soft")}
          >
            {yes ? "C'est un packshot" : "Pas un packshot"}
          </button>
        ))}
      </div>
      {given ? (
        <Input
          aria-label="Note sur ce retour"
          placeholder="Note (facultative)"
          maxLength={200}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          onBlur={() => note.trim() !== given.note && give.mutate({ id: item.id, is_packshot: given.is_packshot, note }, { onError })}
        />
      ) : null}
    </div>
  );
}
