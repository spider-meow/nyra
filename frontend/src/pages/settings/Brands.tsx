import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { BrandMark } from "../../components/BrandMark";
import { useConfirm, useToast } from "../../components/feedback";
import { Button, FieldLabel, Input, SettingsBlock } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { brandLink, useOrg } from "../../lib/org";
import { useBrandMutations } from "../../lib/queries";
import type { Brand } from "../../types";

type Editing = { id: string; name: string } | null;

function useBrandActions(editing: Editing, setEditing: (editing: Editing) => void) {
  const { org, brand: current } = useOrg();
  const { rename } = useBrandMutations();
  const navigate = useNavigate();
  const toast = useToast();

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

  return { rename, save };
}

/** The brand's logo: shown at the top of the side column. A picture of any size is shrunk by the server. */
function LogoActions(props: { brand: Brand }) {
  const { brand } = props;
  const { setLogo, removeLogo } = useBrandMutations();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);

  function pick(event: ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0];
    event.target.value = "";
    if (!picked) return;
    setLogo.mutate(
      { id: brand.id, file: picked },
      {
        onSuccess: () => toast.show({ tone: "success", message: `Logo de ${brand.name} enregistré`, duration: 3000 }),
        onError: (error) => toast.show({ tone: "error", message: "Le logo n'a pas été enregistré", description: errorMessage(error) }),
      },
    );
  }

  return (
    <>
      <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden aria-label={`Logo de ${brand.name}`} onChange={pick} />
      <Button size="sm" variant="ghost" loading={setLogo.isPending} onClick={() => file.current?.click()}>
        {brand.logo ? "Changer le logo" : "Ajouter un logo"}
      </Button>
      {brand.logo ? (
        <Button
          size="sm"
          variant="ghost"
          loading={removeLogo.isPending}
          onClick={() => removeLogo.mutate(brand.id, { onError: (error) => toast.show({ tone: "error", message: "Le logo n'a pas été retiré", description: errorMessage(error) }) })}
        >
          Retirer le logo
        </Button>
      ) : null}
    </>
  );
}

export function Brands() {
  const { admin, platformAdmin, org, brand: current } = useOrg();
  const [editing, setEditing] = useState<Editing>(null);
  const { rename, save } = useBrandActions(editing, setEditing);

  return (
    <SettingsBlock
      title="Vos marques"
      description="Chaque marque a sa bibliothèque et ses adresses (un site par marché, par exemple). Ses visuels ne sont comparés qu'aux images de ses propres sites."
    >
      <ul className="divide-y divide-line">
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
                <BrandMark brand={brand} size={28} />
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium">{brand.name}</span>
                  {brand.id === current.id ? <span className="text-muted"> · affichée</span> : null}
                </span>
                {admin ? (
                  <span className="flex flex-wrap gap-1">
                    <LogoActions brand={brand} />
                    <Button size="sm" variant="ghost" onClick={() => setEditing({ id: brand.id, name: brand.name })}>Renommer</Button>
                  </span>
                ) : null}
              </>
            )}
          </li>
        ))}
      </ul>
      {platformAdmin ? <NewBrand /> : null}
      {admin && !platformAdmin ? (
        <p className="border-t border-line px-5 py-4 text-sm text-muted">Pour ajouter une marque, contactez l'équipe Nyra.</p>
      ) : null}
    </SettingsBlock>
  );
}

function NewBrand() {
  const { org } = useOrg();
  const { create } = useBrandMutations();
  const navigate = useNavigate();
  const toast = useToast();
  const [name, setName] = useState("");

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

  return (
    <form className="flex flex-wrap items-end gap-3 border-t border-line px-5 py-4" onSubmit={add}>
      <div className="min-w-56 flex-1">
        <FieldLabel htmlFor="new-brand">Nouvelle marque</FieldLabel>
        <Input id="new-brand" required maxLength={120} placeholder="Louis XIII" value={name} onChange={(event) => setName(event.target.value)} />
      </div>
      <Button type="submit" loading={create.isPending} disabled={!name.trim()}>Créer</Button>
    </form>
  );
}

/** Deleting a brand, apart from the everyday settings: it cannot be undone. */
export function DeleteBrands() {
  const { platformAdmin, org, brand: current } = useOrg();
  const { remove } = useBrandMutations();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const toast = useToast();

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

  if (!platformAdmin) return null;
  return (
    <SettingsBlock
      danger
      title="Zone dangereuse"
      description="Supprimer une marque efface sa bibliothèque, ses adresses, les images lues, les décisions et les rapports. Les autres marques ne sont pas touchées. Impossible à annuler."
    >
      <ul className="divide-y divide-line">
        {org.brands.map((brand) => (
          <li key={brand.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
            <span className="min-w-0 flex-1 truncate font-medium">{brand.name}</span>
            <Button size="sm" variant="danger" loading={remove.isPending && remove.variables === brand.id} disabled={remove.isPending} onClick={() => void del(brand)}>
              Supprimer
            </Button>
          </li>
        ))}
      </ul>
    </SettingsBlock>
  );
}
