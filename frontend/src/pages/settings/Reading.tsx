import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { Checkbox, FieldLabel, Input, Segmented } from "../../components/ui";
import type { Settings } from "../../types";
import { numberProps, ResetButton, SaveIndicator, useOverridesForm, type OverridesForm } from "./overrides";

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
    <SettingsBlock title="Limites d'une lecture">
      <SettingsRow label="Pages lues au maximum" hint="Par lecture.">
        <Input {...numberProps(form, "crawl.max_pages")} aria-label="Pages lues au maximum" className="w-28" />
      </SettingsRow>
      <SettingsRow label="Taille minimale d'une image, en pixels" hint="Les images plus petites (icônes, pastilles) sont ignorées.">
        <Input {...numberProps(form, "crawl.min_image_side_px")} aria-label="Taille minimale d'une image, en pixels" className="w-28" />
      </SettingsRow>
    </SettingsBlock>
  );
}

function Access(props: { form: OverridesForm }) {
  const { form } = props;
  return (
    <SettingsBlock title="Accès aux sites" description="Ce que Nyra fait pour atteindre les pages, comme le ferait un visiteur.">
      <div className="grid gap-4 p-5">
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
        <details className="max-w-xl rounded-xl border border-line-strong px-4 py-3">
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
    </SettingsBlock>
  );
}

/** How Nyra reads the sites: pace, size of a reading, what it clicks through. */
export function Reading(props: { data: Settings }) {
  const form = useOverridesForm(props.data);
  const pace = currentPace(form);
  return (
    <>
      <SettingsBlock
        title="Vitesse de lecture"
        description="Comment Nyra parcourt vos sites pour y trouver les images. Le changement s'applique à la prochaine lecture."
        aside={<SaveIndicator form={form} />}
      >
        <div className="p-5">
          <Segmented<Pace> label="Vitesse de lecture" value={pace as Pace} options={PACES} onChange={(value) => pick(form, value)} />
          <p className="mt-3 text-sm">{pace === "custom" ? "Personnalisé : ces délais ont été réglés à la main." : PACES.find((item) => item.value === pace)?.effect}</p>
        </div>
      </SettingsBlock>
      <Limits form={form} />
      <Access form={form} />
      <ResetButton form={form} keys={[...PACE_KEYS, ...OTHER_KEYS]} />
    </>
  );
}
