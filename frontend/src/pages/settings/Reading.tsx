import { Card, Checkbox, FieldLabel, Input, Segmented } from "../../components/ui";
import { numberProps, SaveBar, useOverridesForm, type OverridesForm } from "./overrides";
import type { Settings } from "../../types";

type Pace = "calm" | "normal" | "fast";

const PACE_KEYS = ["crawl.concurrency", "crawl.delay_seconds_min", "crawl.delay_seconds_max"];
const OTHER_KEYS = ["crawl.max_pages", "crawl.min_image_side_px", "crawl.dismiss_overlays", "crawl.respect_robots_txt", "crawl.pre_actions"];

// concurrency, delay min, delay max. "normal" is the standard configuration.
const PACE_VALUES: Record<Exclude<Pace, "normal">, number[]> = { calm: [1, 2, 4], fast: [6, 0.5, 1] };

const PACES: { value: Pace; label: string; effect: string }[] = [
  { value: "calm", label: "Discret", effect: "Une page à la fois, avec de longues pauses. Pour un site fragile ou qui bloque facilement." },
  { value: "normal", label: "Normal", effect: "Quelques pages en parallèle, avec une courte pause entre deux. Recommandé." },
  { value: "fast", label: "Rapide", effect: "Beaucoup de pages en parallèle. Plus court, mais plus visible pour le site." },
];

function currentPace(form: OverridesForm): Pace | "custom" {
  const now = PACE_KEYS.map(form.numberOf);
  const same = (values: number[]) => values.every((value, index) => value === now[index]);
  if (same(PACE_KEYS.map((name) => Number(form.defaultOf(name))))) return "normal";
  return (Object.keys(PACE_VALUES) as (keyof typeof PACE_VALUES)[]).find((pace) => same(PACE_VALUES[pace])) ?? "custom";
}

function pick(form: OverridesForm, pace: Pace) {
  const values = pace === "normal" ? PACE_KEYS.map(() => "") : PACE_VALUES[pace].map(String);
  form.setMany(Object.fromEntries(PACE_KEYS.map((name, index) => [name, values[index]])));
}

function Limits(props: { form: OverridesForm }) {
  const { form } = props;
  return (
    <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <div>
        <FieldLabel htmlFor="crawl.max_pages" hint="par lecture">Pages lues au maximum</FieldLabel>
        <Input {...numberProps(form, "crawl.max_pages")} />
      </div>
      <div>
        <FieldLabel htmlFor="crawl.min_image_side_px" hint="en pixels">Taille minimale d'une image</FieldLabel>
        <Input {...numberProps(form, "crawl.min_image_side_px")} />
        <p className="mt-1 text-xs text-muted">Les images plus petites (icônes, pastilles) sont ignorées.</p>
      </div>
    </div>
  );
}

function Access(props: { form: OverridesForm }) {
  const { form } = props;
  return (
    <div className="mt-6 grid gap-3">
      <Checkbox
        checked={form.boolOf("crawl.dismiss_overlays")}
        onChange={(value) => form.set("crawl.dismiss_overlays", String(value))}
        label="Passer les bandeaux cookies et les contrôles d'âge"
        hint="Refuse les cookies quand c'est possible, renseigne une date de naissance fictive et valide."
      />
      <Checkbox
        checked={form.boolOf("crawl.respect_robots_txt")}
        onChange={(value) => form.set("crawl.respect_robots_txt", String(value))}
        label="Respecter les consignes du site (robots.txt)"
        hint="À ne décocher que pour un site dont vous avez l'autorisation explicite."
      />
      <details className="mt-1 max-w-xl rounded-xl border border-ink px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium">Un contrôle d'âge qui bloque quand même ?</summary>
        <div className="mt-3">
          <FieldLabel htmlFor="crawl.pre_actions" hint="un sélecteur CSS par ligne">Éléments à cliquer sur chaque page</FieldLabel>
          <textarea
            id="crawl.pre_actions"
            rows={3}
            placeholder={"#age-gate button.confirm\n.cookie-banner .close"}
            value={form.text("crawl.pre_actions")}
            onChange={(event) => form.set("crawl.pre_actions", event.target.value)}
            className="w-full rounded-lg border border-line-strong bg-paper px-3 py-2 font-mono text-[13px] outline-none focus:border-focus focus:ring-2 focus:ring-focus-soft"
          />
        </div>
      </details>
    </div>
  );
}

/** How Nyra reads the sites: pace, size of a reading, what it clicks through. */
export function Reading(props: { data: Settings }) {
  const form = useOverridesForm(props.data);
  const pace = currentPace(form);
  return (
    <Card>
      <h2 className="font-semibold">Lecture des sites</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted">Comment Nyra parcourt vos sites pour y trouver les images. Le changement s'applique à la prochaine lecture.</p>
      <div className="mt-4">
        <Segmented<Pace> label="Vitesse de lecture" value={pace as Pace} options={PACES} onChange={(value) => pick(form, value)} />
      </div>
      <p className="mt-3 text-sm">{pace === "custom" ? "Personnalisé : ces délais ont été réglés à la main." : PACES.find((item) => item.value === pace)?.effect}</p>
      <Limits form={form} />
      <Access form={form} />
      <SaveBar form={form} keys={[...PACE_KEYS, ...OTHER_KEYS]} />
    </Card>
  );
}
