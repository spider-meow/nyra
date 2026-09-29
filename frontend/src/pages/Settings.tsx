import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { useConfirm, useToast } from "../components/feedback";
import { Button, Card, Checkbox, EmptyState, FieldLabel, Input, PageHeader, Skeleton, Thumb } from "../components/ui";
import { errorMessage } from "../lib/api";
import { formatDate, hostOf } from "../lib/format";
import { useAuth } from "../lib/auth";
import { brandLink, useOrg } from "../lib/org";
import { useBrandMutations, useExclusionMutations, useExclusions, useSaveSettings, useSettings } from "../lib/queries";
import type { Brand, Settings as SettingsData, SettingsSection } from "../types";

type Field = { section: "crawl" | "match" | "report"; key: string; label: string; hint: string; step?: string };

const numericFields: { title: string; description: string; fields: Field[] }[] = [
  {
    title: "Détection",
    description: "Plus un seuil de hachage est haut, plus la détection tolère de retouches (et de faux positifs). Plus un seuil de similarité est bas, plus elle ratisse large.",
    fields: [
      { section: "match", key: "phash_threshold", label: "Seuil pHash", hint: "bits d'écart, 0 à 32" },
      { section: "match", key: "dhash_threshold", label: "Seuil dHash", hint: "bits d'écart, 0 à 32" },
      { section: "match", key: "clip_similarity_high", label: "Similarité « confirmé »", hint: "0 à 1", step: "0.01" },
      { section: "match", key: "clip_similarity_medium", label: "Similarité « probable »", hint: "0 à 1", step: "0.01" },
      { section: "match", key: "clip_similarity_floor", label: "Similarité « à vérifier »", hint: "plancher, 0 à 1", step: "0.01" },
    ],
  },
  {
    title: "Lecture du site",
    description: "Le rythme de lecture reste courtois : chaque page lue attend un délai avant la suivante.",
    fields: [
      { section: "crawl", key: "max_pages", label: "Pages par lecture", hint: "par défaut" },
      { section: "crawl", key: "concurrency", label: "Pages en parallèle", hint: "1 à 8" },
      { section: "crawl", key: "delay_seconds_min", label: "Délai minimum", hint: "secondes", step: "0.1" },
      { section: "crawl", key: "delay_seconds_max", label: "Délai maximum", hint: "secondes", step: "0.1" },
      { section: "crawl", key: "min_image_side_px", label: "Taille minimale d'image", hint: "pixels, plus petit côté" },
    ],
  },
  {
    title: "Rapports",
    description: "Fenêtre proposée par défaut dans « À traiter » et pour les rapports.",
    fields: [{ section: "report", key: "default_within_days", label: "Fenêtre par défaut", hint: "jours" }],
  },
];

type Draft = Record<string, string>;

function draftFrom(settings: SettingsData): Draft {
  const draft: Draft = {};
  for (const section of ["crawl", "match", "report"] as const) {
    for (const [key, value] of Object.entries(settings.overrides[section] ?? {})) {
      draft[`${section}.${key}`] = Array.isArray(value) ? value.join("\n") : String(value);
    }
  }
  return draft;
}

export function Settings() {
  const { admin, org } = useOrg();
  const auth = useAuth();
  const settings = useSettings();
  const save = useSaveSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft | null>(null);

  if (settings.isLoading) return <Skeleton className="h-96" />;
  if (settings.error || !settings.data) return <EmptyState title="Réglages indisponibles" body={errorMessage(settings.error)} />;
  const data = settings.data;
  const current = draft ?? draftFrom(data);
  const defaultOf = (section: Field["section"], key: string) => (data.defaults[section] as SettingsSection)[key];
  const set = (name: string, value: string) => setDraft({ ...current, [name]: value });
  const bool = (key: "respect_robots_txt" | "dismiss_overlays") => {
    const value = current[`crawl.${key}`];
    return value === undefined || value === "" ? Boolean(defaultOf("crawl", key)) : value === "true";
  };

  function submit(event: FormEvent) {
    event.preventDefault();
    const overrides: SettingsData["overrides"] = { crawl: {}, match: {}, report: {} };
    for (const [name, value] of Object.entries(current)) {
      const [section, key] = name.split(".") as [Field["section"], string];
      if (value.trim() === "") continue;
      if (key === "pre_actions") overrides.crawl![key] = value.split("\n").map((line) => line.trim()).filter(Boolean);
      else if (value === "true" || value === "false") overrides[section]![key] = value === "true";
      else overrides[section]![key] = Number(value.replace(",", "."));
    }
    save.mutate(overrides, {
      onSuccess: () => {
        toast.show({ tone: "success", message: "Réglages enregistrés", description: "Ils s'appliquent à la prochaine lecture ou comparaison." });
        setDraft(null);
      },
      onError: (error) => toast.show({ tone: "error", message: "Les réglages n'ont pas été enregistrés", description: errorMessage(error) }),
    });
  }

  return (
    <>
      <PageHeader
        title="Réglages"
        description={`Propres à ${org.name}${org.brands.length > 1 ? ", communs à toutes ses marques" : ""}. Un champ vide reprend la valeur par défaut, affichée en grisé.`}
      />
      <Brands />
      <form className="grid gap-4" onSubmit={submit}>
        {numericFields.map((group) => (
          <Card key={group.title}>
            <h2 className="font-semibold">{group.title}</h2>
            <p className="mt-1 max-w-2xl text-sm text-muted">{group.description}</p>
            <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {group.fields.map((field) => {
                const name = `${field.section}.${field.key}`;
                return (
                  <div key={name}>
                    <FieldLabel htmlFor={name} hint={field.hint}>{field.label}</FieldLabel>
                    <Input
                      id={name}
                      type="number"
                      step={field.step ?? "1"}
                      disabled={!admin}
                      placeholder={String(defaultOf(field.section, field.key))}
                      value={current[name] ?? ""}
                      onChange={(event) => set(name, event.target.value)}
                    />
                  </div>
                );
              })}
            </div>
          </Card>
        ))}

        <Card>
          <h2 className="font-semibold">Accès au site</h2>
          <div className="mt-4 grid gap-3">
            <Checkbox
              checked={bool("dismiss_overlays")}
              disabled={!admin}
              onChange={(value) => set("crawl.dismiss_overlays", String(value))}
              label="Passer automatiquement les bandeaux cookies et les contrôles d'âge"
              hint="Refuse les cookies quand c'est possible, renseigne une date de naissance fictive et valide."
            />
            <Checkbox
              checked={bool("respect_robots_txt")}
              disabled={!admin}
              onChange={(value) => set("crawl.respect_robots_txt", String(value))}
              label="Respecter robots.txt"
              hint="À ne décocher que pour un site dont vous avez l'autorisation explicite."
            />
            <div className="mt-2 max-w-xl">
              <FieldLabel htmlFor="crawl.pre_actions" hint="un sélecteur CSS par ligne">Clics supplémentaires à chaque page</FieldLabel>
              <textarea
                id="crawl.pre_actions"
                rows={3}
                disabled={!admin}
                placeholder={"#age-gate button.confirm\n.cookie-banner .close"}
                value={current["crawl.pre_actions"] ?? ""}
                onChange={(event) => set("crawl.pre_actions", event.target.value)}
                className="w-full rounded-lg border border-line-strong bg-paper px-3 py-2 font-mono text-[13px] outline-none focus:border-focus focus:ring-2 focus:ring-focus-soft disabled:bg-canvas"
              />
              <p className="mt-1 text-xs text-muted">Pour un contrôle d'âge que la détection automatique ne passe pas.</p>
            </div>
          </div>
        </Card>

        {admin ? (
          <div className="flex gap-2">
            <Button type="submit" variant="primary" loading={save.isPending} disabled={draft === null}>Enregistrer</Button>
            <Button onClick={() => setDraft(null)} disabled={draft === null}>Annuler les modifications</Button>
          </div>
        ) : (
          <p className="text-sm text-muted">Seuls les administrateurs peuvent modifier ces réglages.</p>
        )}
      </form>

      <Exclusions />

      <Card className="mt-8">
        <h2 className="font-semibold">Compte</h2>
        <p className="mt-1 text-sm text-muted">
          Connecté en tant que {auth.email} · {admin ? "administrateur" : "lecture et validation"}.
        </p>
        <Button className="mt-3" onClick={() => void auth.signOut()}>Se déconnecter</Button>
      </Card>
    </>
  );
}

function Brands() {
  const { admin, org, brand: current } = useOrg();
  const { create, rename, remove } = useBrandMutations();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const toast = useToast();
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);

  function add(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      { name },
      {
        onSuccess: ({ brand }) => {
          setName("");
          toast.show({ tone: "success", message: `Marque ${brand.name} créée`, description: "Ajoutez maintenant ses adresses, puis sa bibliothèque." });
          navigate(brandLink(org, brand.slug, "lectures"));
        },
        onError: (error) => toast.show({ tone: "error", message: "La marque n'a pas été créée", description: errorMessage(error) }),
      },
    );
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    rename.mutate(editing, {
      onSuccess: ({ brand }) => {
        setEditing(null);
        toast.show({ tone: "success", message: `Marque renommée : ${brand.name}`, duration: 3000 });
        // The address of the current brand follows its new slug.
        if (brand.id === current.id) navigate(brandLink(org, brand.slug, "reglages"), { replace: true });
      },
      onError: (error) => toast.show({ tone: "error", message: "La marque n'a pas été renommée", description: errorMessage(error) }),
    });
  }

  async function del(brand: Brand) {
    const ok = await confirm({
      title: `Supprimer la marque ${brand.name} ?`,
      body: (
        <>
          <p>
            Sa bibliothèque, ses adresses, les images lues, les correspondances, les décisions, les exclusions et les rapports de cette
            marque seront effacés. Les autres marques ne sont pas touchées.
          </p>
          <p className="mt-2 font-medium">Cette action est définitive.</p>
        </>
      ),
      confirm: "Supprimer la marque",
      danger: true,
    });
    if (!ok) return;
    const pending = toast.loading(`Suppression de ${brand.name}…`, "Bibliothèque, adresses et historique de la marque.");
    remove.mutate(brand.id, {
      onSuccess: () => {
        toast.update(pending, { tone: "success", message: `Marque ${brand.name} supprimée`, description: "Les autres marques n'ont pas été touchées." });
        if (brand.id === current.id) navigate(`/o/${org.slug}`, { replace: true });
      },
      onError: (error) => toast.update(pending, { tone: "error", message: `${brand.name} n'a pas été supprimée`, description: errorMessage(error) }),
    });
  }

  return (
    <Card className="mb-4" padded={false}>
      <div className="px-5 pt-5">
        <h2 className="font-semibold">Marques</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Chaque marque a sa bibliothèque et ses adresses (un site par marché, par exemple). Ses visuels ne sont comparés qu'aux images de ses
          propres sites.
        </p>
      </div>
      <ul className="mt-3 divide-y divide-line border-t border-line">
        {org.brands.map((brand) => (
          <li key={brand.id} className="flex flex-wrap items-center gap-3 px-5 py-2.5 text-sm">
            {editing?.id === brand.id ? (
              <form className="flex flex-1 items-center gap-2" onSubmit={save}>
                <Input aria-label="Nom de la marque" autoFocus required maxLength={120} value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} className="max-w-72" />
                <Button size="sm" type="submit" variant="primary" loading={rename.isPending}>OK</Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Annuler</Button>
              </form>
            ) : (
              <>
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium">{brand.name}</span>
                  {brand.id === current.id ? <span className="text-muted"> · affichée</span> : null}
                </span>
                {admin ? (
                  <span className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => setEditing({ id: brand.id, name: brand.name })}>Renommer</Button>
                    <Button size="sm" variant="ghost" loading={remove.isPending && remove.variables === brand.id} disabled={remove.isPending} onClick={() => void del(brand)}>Supprimer</Button>
                  </span>
                ) : null}
              </>
            )}
          </li>
        ))}
      </ul>
      {admin ? (
        <form className="flex flex-wrap items-end gap-3 border-t border-line px-5 py-4" onSubmit={add}>
          <div className="min-w-56 flex-1">
            <FieldLabel htmlFor="new-brand">Nouvelle marque</FieldLabel>
            <Input id="new-brand" required maxLength={120} placeholder="Louis XIII" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <Button type="submit" loading={create.isPending} disabled={!name.trim()}>Créer</Button>
        </form>
      ) : null}
    </Card>
  );
}

function Exclusions() {
  const { admin, brand, org } = useOrg();
  const exclusions = useExclusions();
  const { remove } = useExclusionMutations();
  const toast = useToast();
  const items = exclusions.data?.exclusions ?? [];
  return (
    <Card className="mt-8" padded={false}>
      <div className="px-5 pt-5">
        <h2 className="font-semibold">Images exclues{org.brands.length > 1 ? ` · ${brand.name}` : ""}</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Faux positifs récurrents (logos, visuels génériques) qui ne sont plus jamais proposés, copies proches comprises.
          On exclut une image depuis l'écran « À traiter ».
        </p>
      </div>
      {items.length ? (
        <ul className="mt-3 divide-y divide-line border-t border-line">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
              <Thumb src={item.thumb_url} size={40} />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{item.reason || "Sans raison indiquée"}</span>
                <span className="block truncate text-xs text-muted">
                  {item.site_url ? `${hostOf(item.site_url)} · ` : ""}exclue le {formatDate(item.created_at)}
                </span>
              </span>
              {admin ? (
                <Button
                  size="sm"
                  variant="ghost"
                  loading={remove.isPending && remove.variables === item.id}
                  disabled={remove.isPending}
                  onClick={() =>
                    remove.mutate(item.id, {
                      onSuccess: () =>
                        toast.show({ tone: "success", message: "Image réintégrée", description: "Elle est de nouveau comparée : une comparaison vient d'être programmée." }),
                      onError: (error) => toast.show({ tone: "error", message: "L'image n'a pas été réintégrée", description: errorMessage(error) }),
                    })
                  }
                >
                  Réintégrer
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-5 pt-2 pb-5 text-sm text-muted">Aucune image exclue.</p>
      )}
    </Card>
  );
}
