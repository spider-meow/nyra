import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { useConfirm, useToast } from "../../components/feedback";
import { Button, Card, FieldLabel, Input } from "../../components/ui";
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

export function Brands() {
  const { admin, org, brand: current } = useOrg();
  const [editing, setEditing] = useState<Editing>(null);
  const { rename, save } = useBrandActions(editing, setEditing);

  return (
    <Card padded={false}>
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
                  </span>
                ) : null}
              </>
            )}
          </li>
        ))}
      </ul>
      {admin ? <NewBrand /> : null}
    </Card>
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
  const { org, brand: current } = useOrg();
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

  return (
    <Card padded={false}>
      <div className="px-5 pt-5">
        <h2 className="font-semibold">Supprimer une marque</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Efface sa bibliothèque, ses adresses, les images lues, les décisions et les rapports. Les autres marques ne sont pas touchées. Impossible à annuler.
        </p>
      </div>
      <ul className="mt-3 divide-y divide-line border-t border-line">
        {org.brands.map((brand) => (
          <li key={brand.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
            <span className="min-w-0 flex-1 truncate font-medium">{brand.name}</span>
            <Button size="sm" variant="danger" loading={remove.isPending && remove.variables === brand.id} disabled={remove.isPending} onClick={() => void del(brand)}>
              Supprimer
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
