import { useToast } from "../../components/feedback";
import { cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { usePackshotFeedback } from "../../lib/siteImages";
import type { SiteImage } from "../../types";

const measure = (value: number | null) => (value === null ? "—" : value.toLocaleString("fr-FR", { maximumFractionDigits: 2 }));

/** The Nyra team's answers by key, with the card under the focus: P (packshot), N (not a packshot), Backspace (back to the detection). */
export const PACKSHOT_KEYS: Record<string, boolean | null> = { p: true, n: false, Backspace: null };

/**
 * For the Nyra team only (AXE-668, AXE-721): "Packshot ?" with two round buttons, ✓ and ✕, the detector's measures in a tooltip, and
 * an "Annuler" in the message that follows an answer. The answer wins over the detection (AXE-680): pressing the filled button again goes back to the detection.
 */
export function usePackshotDecision(item: SiteImage) {
  const { give, takeBack } = usePackshotFeedback();
  const toast = useToast();
  const given = item.packshot_feedback;
  const onError = (error: unknown) => toast.show({ tone: "error", message: "Le retour n'a pas été enregistré", description: errorMessage(error) });
  const restore = (before: boolean | null) =>
    before === null ? takeBack.mutate(item.id, { onError }) : give.mutate({ id: item.id, is_packshot: before, note: given?.note ?? "" }, { onError });
  /** `answer`: true / false, or null to go back to the detection. Nothing happens when it is the answer already given. */
  function decide(answer: boolean | null) {
    const before = given ? given.is_packshot : null;
    if (answer === before) return;
    const done = {
      onError,
      onSuccess: () =>
        toast.show({
          tone: "success",
          duration: 8000,
          message: answer === null ? "Retour à la détection" : answer ? "Classée comme packshot · déplacée dans Ignorées" : "Sorti de Packshots · remis dans À qualifier",
          action: { label: "Annuler", onClick: () => restore(before) },
        }),
    };
    if (answer === null) takeBack.mutate(item.id, done);
    else give.mutate({ id: item.id, is_packshot: answer, note: given?.note ?? "" }, done);
  }
  return { decide, pending: give.isPending || takeBack.isPending, answer: given ? given.is_packshot : null };
}

export function PackshotFeedback({ item }: { item: SiteImage }) {
  const { decide, pending, answer } = usePackshotDecision(item);
  const measures = item.packshot_measures;
  if (!measures) return null;
  const tip = `Ressemble à un produit ${measure(measures.product)} · fond uni ${measure(measures.border)} · ${measures.detected ? "détectée" : "non détectée"}`;
  return (
    <div className="mt-1 flex items-center gap-2 border-t border-line pt-2 text-[13px] text-ink-soft" role="group" aria-label="Packshot ?">
      <span className="flex-1">{answer === null ? "Packshot ?" : "Décidé par l'équipe"}</span>
      <span title={tip} aria-label={`Mesures de la détection : ${tip}`} role="img" className="grid h-6 w-6 place-items-center rounded-full border border-line-strong text-xs font-semibold">i</span>
      {([true, false] as const).map((yes) => (
        <button
          key={String(yes)}
          type="button"
          aria-pressed={answer === yes}
          aria-label={yes ? "C'est un packshot" : "Ce n'est pas un packshot"}
          disabled={pending}
          onClick={() => decide(answer === yes ? null : yes)}
          className={cx(
            "grid h-8 w-8 place-items-center rounded-full border-[1.5px] disabled:opacity-60",
            answer === yes ? (yes ? "border-ok bg-ok text-white" : "border-expired bg-expired text-white") : "border-line-strong bg-paper hover:bg-sunk",
          )}
        >
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            {yes ? <path d="M5 12l5 5 9-10" /> : <path d="M6 6l12 12M18 6L6 18" />}
          </svg>
        </button>
      ))}
    </div>
  );
}
