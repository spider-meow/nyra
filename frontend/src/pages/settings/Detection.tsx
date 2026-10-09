import { Card, Checkbox, FieldLabel, Input, Segmented } from "../../components/ui";
import { numberProps, SaveBar, useOverridesForm, type OverridesForm } from "./overrides";
import type { Settings } from "../../types";

type Level = "strict" | "balanced" | "wide";

const THRESHOLDS = ["match.phash_threshold", "match.dhash_threshold", "match.clip_similarity_high", "match.clip_similarity_medium", "match.clip_similarity_floor"];
const VERIFY = ["match.verify_clip_matches", "match.geometric_min_inliers", "match.geometric_review_coverage", "match.geometric_confirm_coverage"];

// Starting points, to be tuned on real cases (docs/MATCHING.md). "balanced" is the standard configuration.
const LEVEL_VALUES: Record<Exclude<Level, "balanced">, number[]> = {
  strict: [6, 6, 0.94, 0.88, 0.8],
  wide: [10, 10, 0.9, 0.82, 0.7],
};

const LEVELS: { value: Level; label: string; effect: string }[] = [
  { value: "strict", label: "Prudent", effect: "Très peu de fausses alertes à trier. Une copie très retouchée peut passer inaperçue." },
  { value: "balanced", label: "Équilibré", effect: "Le bon compromis pour la plupart des marques. Recommandé." },
  { value: "wide", label: "Large", effect: "Ne rate presque aucune copie, mais vous aurez plus d'images à vérifier." },
];

function currentLevel(form: OverridesForm): Level | "custom" {
  const now = THRESHOLDS.map(form.numberOf);
  const same = (values: number[]) => values.every((value, index) => value === now[index]);
  if (same(THRESHOLDS.map((name) => Number(form.defaultOf(name))))) return "balanced";
  return (Object.keys(LEVEL_VALUES) as (keyof typeof LEVEL_VALUES)[]).find((level) => same(LEVEL_VALUES[level])) ?? "custom";
}

function pick(form: OverridesForm, level: Level) {
  const values = level === "balanced" ? THRESHOLDS.map(() => "") : LEVEL_VALUES[level].map(String);
  form.setMany(Object.fromEntries(THRESHOLDS.map((name, index) => [name, values[index]])));
}

function Advanced(props: { form: OverridesForm }) {
  const { form } = props;
  const field = (name: string, label: string, hint: string, step = "1") => (
    <div key={name}>
      <FieldLabel htmlFor={name} hint={hint}>{label}</FieldLabel>
      <Input {...numberProps(form, name, step)} />
    </div>
  );
  return (
    <details className="mt-5 rounded-xl border border-ink px-4 py-3">
      <summary className="cursor-pointer text-sm font-medium">Réglages avancés</summary>
      <p className="mt-2 max-w-2xl text-sm text-muted">Les valeurs exactes derrière les trois niveaux. À toucher seulement si vous savez ce que vous faites.</p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {field("match.phash_threshold", "Tolérance, empreinte de forme", "0 à 32 · plus haut = plus tolérant")}
        {field("match.dhash_threshold", "Tolérance, empreinte de contours", "0 à 32 · plus haut = plus tolérant")}
        {field("match.clip_similarity_high", "Ressemblance « confirmé »", "0 à 1", "0.01")}
        {field("match.clip_similarity_medium", "Ressemblance « probable »", "0 à 1", "0.01")}
        {field("match.clip_similarity_floor", "Ressemblance « à vérifier »", "minimum, 0 à 1", "0.01")}
      </div>
      <div className="mt-5 grid gap-4">
        <Checkbox
          checked={form.boolOf(VERIFY[0])}
          onChange={(value) => form.set(VERIFY[0], String(value))}
          label="Vérifier chaque ressemblance sur les détails de l'image"
          hint="Écarte les autres prises de vue d'un même produit. Recommandé."
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {field(VERIFY[1], "Points communs minimum", "nombre")}
          {field(VERIFY[2], "Part commune pour « à vérifier »", "0 à 1", "0.01")}
          {field(VERIFY[3], "Part commune pour « confirmé »", "0 à 1", "0.01")}
        </div>
      </div>
    </details>
  );
}

/** How strict the detection is: three levels, the exact values behind them in a fold. */
export function Detection(props: { data: Settings }) {
  const form = useOverridesForm(props.data);
  const level = currentLevel(form);
  return (
    <Card>
      <h2 className="font-semibold">Détection</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted">À partir de quelle ressemblance Nyra vous propose une image. Le changement s'applique à la prochaine comparaison.</p>
      <div className="mt-4">
        <Segmented<Level> label="Niveau de détection" value={level as Level} options={LEVELS} onChange={(value) => pick(form, value)} />
      </div>
      <p className="mt-3 text-sm">
        {level === "custom" ? "Personnalisé : les valeurs avancées ne correspondent à aucun niveau." : LEVELS.find((item) => item.value === level)?.effect}
      </p>
      <div className="mt-6 max-w-xs">
        <FieldLabel htmlFor="report.default_within_days" hint="en jours">Expirations à surveiller par défaut</FieldLabel>
        <Input {...numberProps(form, "report.default_within_days")} />
        <p className="mt-1 text-xs text-muted">Les visuels qui expirent dans ce délai apparaissent dans « À traiter » et dans les rapports.</p>
      </div>
      <Advanced form={form} />
      <SaveBar form={form} keys={[...THRESHOLDS, ...VERIFY, "report.default_within_days"]} />
    </Card>
  );
}
