import { ZxcvbnFactory } from "@zxcvbn-ts/core";
import * as common from "@zxcvbn-ts/language-common";
import translations from "@zxcvbn-ts/language-fr/dist/translations.mjs";
import { cx } from "./ui";

// Only the French messages: the package's word lists (Wikipedia, names) weigh as much as the passwords list.
const estimator = new ZxcvbnFactory({ translations, graphs: common.adjacencyGraphs, dictionary: common.dictionary });

const LEVELS = ["Très faible", "Faible", "Moyen", "Bon", "Excellent"] as const;
const COLORS = ["bg-expired", "bg-expired", "bg-urgent", "bg-ink-soft", "bg-ink"] as const;
// zxcvbn slows down on very long inputs; the first characters decide the score anyway.
const MAX_CHECKED = 64;

/** A live strength bar. Loaded on demand (`lazy`): its dictionaries are too big for the first screen. */
export default function PasswordStrength(props: { password: string; userInputs?: string[] }) {
  if (!props.password) return null;
  const { score, feedback } = estimator.check(props.password.slice(0, MAX_CHECKED), props.userInputs);
  const hint = feedback.warning || feedback.suggestions[0];
  return (
    <div className="mt-2" aria-live="polite">
      <div className="flex gap-1" aria-hidden>
        {[0, 1, 2, 3, 4].map((step) => (
          <span key={step} className={cx("h-1.5 flex-1 rounded-full", score >= step ? COLORS[score] : "bg-line")} />
        ))}
      </div>
      <p className="mt-1.5 text-[13px] text-muted">
        <span className="font-medium text-ink-soft">{LEVELS[score]}</span>
        {hint ? ` · ${hint}` : ""}
      </p>
    </div>
  );
}
