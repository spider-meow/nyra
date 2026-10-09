import { useState } from "react";
import { useToast } from "../../components/feedback";
import { Button } from "../../components/ui";
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

/** A settings section's draft: what the person typed, what applies, and the save that sends it. */
export function useOverridesForm(data: Settings) {
  const save = useSaveSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft | null>(null);
  const current = draft ?? draftFrom(data);

  const defaultOf = (name: string) => {
    const [section, key] = name.split(".");
    return data.defaults[section as (typeof SECTIONS)[number]][key];
  };
  const text = (name: string) => current[name] ?? "";
  const numberOf = (name: string) => (text(name).trim() === "" ? Number(defaultOf(name)) : Number(text(name).replace(",", ".")));
  const boolOf = (name: string) => (text(name) === "" ? Boolean(defaultOf(name)) : text(name) === "true");
  const set = (name: string, value: string) => setDraft({ ...current, [name]: value });
  const setMany = (values: Record<string, string>) => setDraft({ ...current, ...values });

  function submit() {
    save.mutate(overridesFrom(current), {
      onSuccess: () => {
        toast.show({ tone: "success", message: "Réglages enregistrés", description: "Ils s'appliquent à la prochaine lecture ou comparaison." });
        setDraft(null);
      },
      onError: (error) => toast.show({ tone: "error", message: "Les réglages n'ont pas été enregistrés", description: errorMessage(error) }),
    });
  }

  return { text, numberOf, boolOf, defaultOf, set, setMany, submit, saving: save.isPending, dirty: draft !== null, cancel: () => setDraft(null) };
}

export type OverridesForm = ReturnType<typeof useOverridesForm>;

/** Save, cancel, and a way back to the standard values for the keys of one section. */
export function SaveBar(props: { form: OverridesForm; keys: string[] }) {
  const { form } = props;
  const customised = props.keys.some((name) => form.text(name) !== "");
  return (
    <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
      <Button variant="primary" loading={form.saving} disabled={!form.dirty} onClick={form.submit}>Enregistrer</Button>
      <Button disabled={!form.dirty} onClick={form.cancel}>Annuler les modifications</Button>
      <Button
        variant="ghost"
        className="ml-auto"
        disabled={!customised}
        onClick={() => form.setMany(Object.fromEntries(props.keys.map((name) => [name, ""])))}
      >
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
