import { useState, type FormEvent } from "react";
import { AuditLog } from "../../components/AuditLog";
import { useConfirm, useToast } from "../../components/feedback";
import { Section } from "../../components/insights";
import { Button, EmptyState, FieldLabel, Input, PageHeader, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { useStaffMutations, useStaffOrgs } from "../../lib/staff";
import type { StaffOrg } from "../../types";

function NewOrganization() {
  const { createOrg } = useStaffMutations();
  const toast = useToast();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    createOrg.mutate(
      { name, admin_email: email },
      {
        onSuccess: ({ invited }) => {
          setName("");
          setEmail("");
          toast.show({ tone: "success", message: `Organisation ${name} créée`, description: invited ? "Son administrateur reçoit une invitation par e-mail." : "Son administrateur a déjà un compte." });
        },
        onError: (error) => toast.show({ tone: "error", message: "L'organisation n'a pas été créée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Section title="Nouvelle organisation" description="Un client, avec une première marque du même nom (facturée comme toute marque) et son premier administrateur.">
      <form className="flex flex-wrap items-end gap-3" onSubmit={submit}>
        <div className="min-w-48 flex-1">
          <FieldLabel htmlFor="org-name">Nom du client</FieldLabel>
          <Input id="org-name" required maxLength={120} placeholder="Hennessy" value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="min-w-56 flex-1">
          <FieldLabel htmlFor="org-admin">E-mail de son administrateur</FieldLabel>
          <Input id="org-admin" type="email" required maxLength={254} placeholder="prenom.nom@client.com" value={email} onChange={(event) => setEmail(event.target.value)} />
        </div>
        <Button type="submit" variant="primary" loading={createOrg.isPending} disabled={!name.trim() || !email.trim()}>Créer l'organisation</Button>
      </form>
    </Section>
  );
}

function NewBrand(props: { org: StaffOrg }) {
  const { createBrand } = useStaffMutations();
  const toast = useToast();
  const [name, setName] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    createBrand.mutate(
      { orgId: props.org.id, name },
      {
        onSuccess: () => {
          setName("");
          toast.show({ tone: "success", message: `Marque ${name} créée pour ${props.org.name}` });
        },
        onError: (error) => toast.show({ tone: "error", message: "La marque n'a pas été créée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <form className="mt-3 flex flex-wrap items-center gap-2" onSubmit={submit}>
      <Input aria-label={`Nouvelle marque pour ${props.org.name}`} required maxLength={120} placeholder="Nouvelle marque" value={name} onChange={(event) => setName(event.target.value)} className="max-w-64" />
      <Button type="submit" size="sm" loading={createBrand.isPending} disabled={!name.trim()}>Ajouter la marque</Button>
    </form>
  );
}

function OrganizationCard(props: { org: StaffOrg }) {
  const { org } = props;
  const { deleteBrand } = useStaffMutations();
  const confirm = useConfirm();
  const toast = useToast();
  const [journal, setJournal] = useState(false);

  async function remove(brand: StaffOrg["brands"][number]) {
    const ok = await confirm({
      title: `Supprimer la marque ${brand.name} ?`,
      body: <p>Sa bibliothèque, ses adresses, les images lues, les décisions et les rapports de cette marque sont effacés. Cette action est définitive.</p>,
      confirm: "Supprimer la marque",
      danger: true,
    });
    if (!ok) return;
    deleteBrand.mutate(
      { orgId: org.id, brandId: brand.id },
      {
        onSuccess: () => toast.show({ tone: "success", message: `Marque ${brand.name} supprimée` }),
        onError: (error) => toast.show({ tone: "error", message: `${brand.name} n'a pas été supprimée`, description: errorMessage(error) }),
      },
    );
  }

  return (
    <Section title={org.name} description={`${org.members} ${org.members > 1 ? "membres" : "membre"} · client depuis le ${formatDate(org.created_at)}`}>
      <ul className="divide-y divide-line">
        {org.brands.map((brand) => (
          <li key={brand.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate font-medium">{brand.name}</span>
            <span className="text-muted">créée le {formatDate(brand.created_at)}{brand.created_by ? ` par ${brand.created_by}` : ""}</span>
            <Button size="sm" variant="danger" disabled={deleteBrand.isPending} onClick={() => void remove(brand)}>Supprimer</Button>
          </li>
        ))}
      </ul>
      <NewBrand org={org} />
      <Button size="sm" variant="ghost" className="mt-3" aria-expanded={journal} onClick={() => setJournal(!journal)}>{journal ? "Masquer le journal d'activité" : "Journal d'activité"}</Button>
      {journal ? <AuditLog path={`/staff/organizations/${org.id}/audit`} cacheKey={`staff-${org.id}`} /> : null}
    </Section>
  );
}

/** /back-office/organizations — every client, its brands, and where a brand is created (each one is billed). */
export function Organizations() {
  const orgs = useStaffOrgs();
  return (
    <>
      <PageHeader title="Organisations et marques" />
      <div className="grid gap-4">
        <NewOrganization />
        {orgs.isLoading ? <Skeleton className="h-40" /> : null}
        {orgs.error ? <EmptyState title="Impossible de charger les organisations" body={errorMessage(orgs.error)} /> : null}
        {orgs.data?.organizations.map((org) => <OrganizationCard key={org.id} org={org} />)}
      </div>
    </>
  );
}
