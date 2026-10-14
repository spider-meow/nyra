import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { Modal, useToast } from "../../components/feedback";
import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { Button, FieldLabel, Input } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useBrandMutations, useOverview } from "../../lib/queries";

/** What goes with the brand, in the numbers of the brand on screen (nothing is invented while they load). */
function Consequences() {
  const overview = useOverview();
  const stats = overview.data?.stats;
  const sites = overview.data?.sites.length;
  const lines = [
    stats && plural(stats.reference_images, "visuel de la bibliothèque", "visuels de la bibliothèque"),
    sites !== undefined && plural(sites, "adresse", "adresses"),
    stats && plural(stats.site_images, "image lue sur les sites", "images lues sur les sites"),
    stats && plural(stats.matches, "correspondance", "correspondances"),
  ].filter(Boolean);
  return (
    <ul className="mt-2 list-disc pl-5">
      {lines.map((line) => <li key={String(line)}>{line}</li>)}
      <li>les décisions, les exclusions et les rapports</li>
    </ul>
  );
}

function ConfirmDelete(props: { open: boolean; onClose: () => void }) {
  const { org, brand } = useOrg();
  const { remove } = useBrandMutations();
  const navigate = useNavigate();
  const toast = useToast();
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === brand.name;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!matches) return;
    remove.mutate(brand.id, {
      onSuccess: () => {
        toast.show({ tone: "success", message: `Marque ${brand.name} supprimée`, description: "Les autres marques n'ont pas été touchées." });
        navigate(`/o/${org.slug}`, { replace: true });
      },
      onError: (error) => toast.show({ tone: "error", message: `${brand.name} n'a pas été supprimée`, description: errorMessage(error) }),
    });
  }

  return (
    <Modal
      open={props.open}
      onClose={props.onClose}
      title={`Supprimer ${brand.name} ?`}
      footer={
        <>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button type="submit" form="delete-brand" variant="danger" loading={remove.isPending} disabled={!matches}>Supprimer la marque</Button>
        </>
      }
    >
      <form id="delete-brand" className="grid gap-4 text-sm text-ink-soft" onSubmit={submit}>
        <div>
          <p>Seront effacés, pour cette marque seulement :</p>
          <Consequences />
          <p className="mt-3 font-medium text-ink">Cette action est définitive.</p>
        </div>
        <div>
          <FieldLabel htmlFor="delete-brand-name">Pour confirmer, écrivez <span className="font-semibold text-ink">{brand.name}</span></FieldLabel>
          <Input id="delete-brand-name" data-autofocus autoComplete="off" value={typed} onChange={(event) => setTyped(event.target.value)} />
        </div>
      </form>
    </Modal>
  );
}

/** Deleting the brand on screen: kept apart at the bottom, for the Nyra team only (a brand is a billed item). */
export function DangerZone() {
  const { platformAdmin, brand } = useOrg();
  const [open, setOpen] = useState(false);
  if (!platformAdmin) return null;
  return (
    <SettingsBlock danger title="Zone dangereuse" description="Réservée à l'équipe Nyra : chaque marque est un élément facturé. Supprimer une marque ne touche pas aux autres.">
      <SettingsRow label={`Supprimer ${brand.name}`} hint="Sa bibliothèque, ses adresses, les images lues, les décisions et les rapports sont effacés. Impossible à annuler.">
        <Button variant="danger" onClick={() => setOpen(true)}>Supprimer la marque…</Button>
      </SettingsRow>
      <ConfirmDelete key={String(open)} open={open} onClose={() => setOpen(false)} />
    </SettingsBlock>
  );
}
