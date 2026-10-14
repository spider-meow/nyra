import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { Section } from "../../components/insights";
import { Button, EmptyState, FieldLabel, Input, PageHeader, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useStaffAdmins, useStaffMutations } from "../../lib/staff";
import type { StaffAdmin } from "../../types";

function AdminRow(props: { admin: StaffAdmin }) {
  const { admin } = props;
  const { removeAdmin } = useStaffMutations();
  const confirm = useConfirm();
  const toast = useToast();

  async function remove() {
    const ok = await confirm({
      title: `Retirer ${admin.email} ?`,
      body: <p>Cette personne ne pourra plus créer de marques ni ouvrir le back office. Son compte et ses organisations ne changent pas.</p>,
      confirm: "Retirer",
      danger: true,
    });
    if (ok) removeAdmin.mutate(admin.user_id, { onError: (error) => toast.show({ tone: "error", message: "Le rôle n'a pas été retiré", description: errorMessage(error) }) });
  }

  return (
    <li className="flex flex-wrap items-center gap-3 py-2.5 text-sm">
      <span className="min-w-0 flex-1 truncate">
        {admin.email}
        {admin.you ? <span className="text-muted"> · vous</span> : null}
      </span>
      {admin.you ? null : <Button size="sm" variant="ghost" loading={removeAdmin.isPending && removeAdmin.variables === admin.user_id} onClick={() => void remove()}>Retirer</Button>}
    </li>
  );
}

function AddAdmin() {
  const { addAdmin } = useStaffMutations();
  const toast = useToast();
  const [email, setEmail] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    addAdmin.mutate(email, {
      onSuccess: ({ invited }) => {
        setEmail("");
        toast.show({ tone: "success", message: "Platform admin ajouté", description: invited ? "Il reçoit une invitation par e-mail." : "Il a déjà un compte." });
      },
      onError: (error) => toast.show({ tone: "error", message: "La personne n'a pas été ajoutée", description: errorMessage(error) }),
    });
  }

  return (
    <form className="mt-3 flex flex-wrap items-end gap-3 border-t border-line pt-4" onSubmit={submit}>
      <div className="min-w-56 flex-1">
        <FieldLabel htmlFor="admin-email">Ajouter par e-mail</FieldLabel>
        <Input id="admin-email" type="email" required maxLength={254} placeholder="prenom.nom@axelproject.fr" value={email} onChange={(event) => setEmail(event.target.value)} />
      </div>
      <Button type="submit" variant="primary" loading={addAdmin.isPending} disabled={!email.trim()}>Ajouter</Button>
    </form>
  );
}

/** /back-office/platform-admins — who runs the platform: creates brands, opens the back office. */
export function Admins() {
  const admins = useStaffAdmins();
  return (
    <>
      <PageHeader title="Platform admins" />
      <Section title="Platform admins">
        {admins.isLoading ? <Skeleton className="h-24" /> : null}
        {admins.error ? <EmptyState title="Impossible de charger la liste" body={errorMessage(admins.error)} /> : null}
        <ul className="divide-y divide-line">{admins.data?.admins.map((admin) => <AdminRow key={admin.user_id} admin={admin} />)}</ul>
        <AddAdmin />
      </Section>
    </>
  );
}
