import { useEffect, useState } from "react";
import { useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { Button, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useSaveSettings } from "../../lib/queries";
import type { Settings } from "../../types";

const SECTIONS = ["crawl", "match", "report"] as const;

/** Edits in progress, by "section.key". An empty value means "use the default". */
type Draft = Record<string, string>;

function draftFrom(settings: Settings): Draft {
  const draft: Draft = {};
  for (const section of SECTIONS) {
    for (const [key, value] of Object.entries(settings.overrides[section] ?? {})) {
      draft[`${section}.${key}`] = Array.isArray(value) ? value.join("\n") : String(value);
    }
  }
  return draft;
}

function overridesFrom(current: Draft): Settings["overrides"] {
  const overrides: Settings["overrides"] = { crawl: {}, match: {}, report: {} };
  for (const [name, value] of Object.entries(current)) {
    const [head, key] = name.split(".");
    const section = SECTIONS.find((item) => item === head);
    if (!section || value.trim() === "") continue;
    if (key === "pre_actions") overrides.crawl![key] = value.split("\n").map((line) => line.trim()).filter(Boolean);
    else if (value === "true" || value === "false") overrides[section]![key] = value === "true";
    else overrides[section]![key] = Number(value.replace(",", "."));
  }
  return overrides;
}

const AUTOSAVE_DELAY_MS = 800;

/** waiting: edited, not sent yet · saving: on its way · saved: the server has it · error: refused, edit again or retry. */
export type SaveState = "idle" | "waiting" | "saving" | "saved" | "error";

/**
 * A settings section's draft: what the person typed and what applies. Changes are saved on their own a moment
 * after the last keystroke (they only take effect at the next reading or comparison, and can be undone the same way).
 */
export function useOverridesForm(data: Settings) {
  const save = useSaveSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  const current = draft ?? draftFrom(data);

  function send(sent: Draft) {
    setState("saving");
    save.mutate(overridesFrom(sent), {
      onSuccess: () => {
        // An edit made while this one was on its way stays a draft and goes out next.
        setDraft((now) => (now === sent ? null : now));
        setState((now) => (now === "saving" ? "saved" : now));
      },
      onError: (error) => {
        setState("error");
        toast.show({ tone: "error", message: "Les réglages n'ont pas été enregistrés", description: errorMessage(error) });
      },
    });
  }

  useEffect(() => {
    if (draft === null || state !== "waiting" || save.isPending) return;
    const timer = setTimeout(() => send(draft), AUTOSAVE_DELAY_MS);
    return () => clearTimeout(timer);
    // `send` only reads `save` and `setState`, which do not change what a pending timer must do.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, state, save.isPending]);

  const defaultOf = (name: string) => {
    const [section, key] = name.split(".");
    return data.defaults[section as (typeof SECTIONS)[number]][key];
  };
  const text = (name: string) => current[name] ?? "";
  const numberOf = (name: string) => (text(name).trim() === "" ? Number(defaultOf(name)) : Number(text(name).replace(",", ".")));
  const boolOf = (name: string) => (text(name) === "" ? Boolean(defaultOf(name)) : text(name) === "true");
  const edit = (values: Draft) => {
    setDraft({ ...current, ...values });
    setState("waiting");
  };

  return {
    text, numberOf, boolOf, defaultOf,
    set: (name: string, value: string) => edit({ [name]: value }),
    setMany: edit,
    state,
    retry: () => draft && send(draft),
  };
}

export type OverridesForm = ReturnType<typeof useOverridesForm>;

const STATE_LABEL: Record<SaveState, string> = {
  idle: "Enregistré automatiquement",
  waiting: "Modification en cours…",
  saving: "Enregistrement…",
  saved: "Enregistré · s'applique à la prochaine lecture",
  error: "Non enregistré",
};

/** Where the section's changes stand, next to its first title (a live region: it is read out when it changes). */
export function SaveIndicator(props: { form: OverridesForm }) {
  const { form } = props;
  return (
    <span role="status" className={cx("flex items-center gap-1.5 text-xs", form.state === "error" ? "text-expired" : "text-muted")}>
      {form.state === "saved" ? <Icon name="check" size={13} strokeWidth={2} /> : null}
      {STATE_LABEL[form.state]}
      {form.state === "error" ? <button type="button" className="underline" onClick={form.retry}>Réessayer</button> : null}
    </span>
  );
}

/** A way back to the standard values for the keys of one section. */
export function ResetButton(props: { form: OverridesForm; keys: string[] }) {
  const customised = props.keys.some((name) => props.form.text(name) !== "");
  return (
    <div className="mt-6 flex justify-end">
      <Button variant="ghost" disabled={!customised} onClick={() => props.form.setMany(Object.fromEntries(props.keys.map((name) => [name, ""])))}>
        Revenir aux valeurs habituelles
      </Button>
    </div>
  );
}

/** A number field whose placeholder is the standard value. */
export function numberProps(form: OverridesForm, name: string, step = "1") {
  return {
    id: name,
    type: "number" as const,
    step,
    placeholder: String(form.defaultOf(name)),
    value: form.text(name),
    onChange: (event: { target: { value: string } }) => form.set(name, event.target.value),
  };
}
