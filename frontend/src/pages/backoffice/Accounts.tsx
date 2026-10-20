import { useState, type FormEvent } from "react";
import { Select } from "../../components/Dropdown";
import { useConfirm, useToast } from "../../components/feedback";
import { Section } from "../../components/insights";
import { Button, EmptyState, FieldLabel, Input, PageHeader, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate, formatDateTime } from "../../lib/format";
import { useStaffMutations, useStaffOrgs, useStaffUsers } from "../../lib/staff";
import type { Member, StaffUser } from "../../types";

const ROLE_LABEL: Record<Member["role"], string> = { admin: "Administrateur", client: "Lecture et validation" };

function AddPerson() {
  const orgs = useStaffOrgs();
  const { addMember } = useStaffMutations();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [orgId, setOrgId] = useState("");
  const [role, setRole] = useState<Member["role"]>("client");
  const list = orgs.data?.organizations ?? [];
  const target = orgId || list[0]?.id || "";

  function submit(event: FormEvent) {
    event.preventDefault();
    addMember.mutate(
      { orgId: target, email, role },
      {
        onSuccess: ({ invited }) => {
          setEmail("");
          toast.show({ tone: "success", message: invited ? "Invitation envoyée" : "Personne ajoutée", description: invited ? "Elle reçoit un e-mail pour choisir son mot de passe." : "Elle a déjà un compte." });
        },
        onError: (error) => toast.show({ tone: "error", message: "La personne n'a pas été ajoutée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Section title="Ajouter une personne" description="À n'importe quelle organisation. Sans compte, elle est invitée par e-mail ; vous pouvez aussi vous ajouter vous-même pour ouvrir l'espace d'un client.">
      <form className="flex flex-wrap items-end gap-3" onSubmit={submit}>
        <div className="min-w-56 flex-1">
          <FieldLabel htmlFor="person-email">E-mail</FieldLabel>
          <Input id="person-email" type="email" required maxLength={254} placeholder="prenom.nom@client.com" value={email} onChange={(event) => setEmail(event.target.value)} />
        </div>
        <div>
          <FieldLabel htmlFor="person-org">Organisation</FieldLabel>
          <Select id="person-org" value={target} onChange={(event) => setOrgId(event.target.value)} className="w-52">
            {list.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
          </Select>
        </div>
        <div>
          <FieldLabel htmlFor="person-role">Rôle</FieldLabel>
          <Select id="person-role" value={role} onChange={(event) => setRole(event.target.value as Member["role"])} className="w-52">
            <option value="client">{ROLE_LABEL.client}</option>
            <option value="admin">{ROLE_LABEL.admin}</option>
          </Select>
        </div>
        <Button type="submit" variant="primary" loading={addMember.isPending} disabled={!email.trim() || !target}>Ajouter</Button>
      </form>
    </Section>
  );
}

function UserRow(props: { user: StaffUser }) {
  const { user } = props;
  const { setRole, removeMember } = useStaffMutations();
  const confirm = useConfirm();
  const toast = useToast();
  const fail = (message: string) => (error: Error) => toast.show({ tone: "error", message, description: errorMessage(error) });

  async function leave(orgId: string, orgName: string) {
    const ok = await confirm({
      title: `Retirer ${user.email} de ${orgName} ?`,
      body: <p>Cette personne n'aura plus accès à {orgName}. Son compte n'est pas supprimé.</p>,
      confirm: "Retirer",
      danger: true,
    });
    if (ok) removeMember.mutate({ orgId, userId: user.user_id }, { onError: fail("La personne n'a pas été retirée") });
  }

  return (
    <li className="py-3 text-sm">
      <p className="flex flex-wrap items-baseline gap-x-3">
        <span className="font-medium">{user.email || "(sans e-mail)"}</span>
        {user.staff ? <span className="text-xs font-medium">platform admin</span> : null}
        <span className="text-xs text-muted">{user.last_sign_in_at ? `connecté le ${formatDateTime(user.last_sign_in_at)}` : "ne s'est jamais connecté"}</span>
      </p>
      {user.memberships.length === 0 ? <p className="mt-1 text-muted">Aucune organisation.</p> : null}
      <ul className="mt-1 grid gap-1.5">
        {user.memberships.map((m) => (
          <li key={m.org_id} className="flex flex-wrap items-center gap-2">
            <span className="min-w-40 flex-1 text-muted">{m.org_name}</span>
            <Select
              className="w-52"
              aria-label={`Rôle de ${user.email} dans ${m.org_name}`}
              value={m.role}
              disabled={setRole.isPending}
              onChange={(event) => setRole.mutate({ orgId: m.org_id, userId: user.user_id, role: event.target.value as Member["role"] }, { onError: fail("Le rôle n'a pas été modifié") })}
            >
              <option value="client">{ROLE_LABEL.client}</option>
              <option value="admin">{ROLE_LABEL.admin}</option>
            </Select>
            <Button size="sm" variant="ghost" disabled={removeMember.isPending} onClick={() => void leave(m.org_id, m.org_name)}>Retirer</Button>
          </li>
        ))}
      </ul>
    </li>
  );
}

/** The administrators of every organization, for the periodic check: who, since when, last sign-in. */
function OrgAdmins(props: { users: StaffUser[] }) {
  const rows = props.users
    .flatMap((user) => user.memberships.filter((m) => m.role === "admin").map((m) => ({ ...m, user })))
    .sort((a, b) => a.org_name.localeCompare(b.org_name) || a.user.email.localeCompare(b.user.email));
  return (
    <Section title={`${rows.length} administrateurs d'organisation`} description="Contrôle périodique : chaque personne doit être connue de l'organisation et toujours y travailler. « Retirer » est dans la liste des comptes ci-dessous.">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="py-2 pr-2 font-medium">Organisation</th>
              <th className="px-2 py-2 font-medium">E-mail</th>
              <th className="px-2 py-2 font-medium">Membre depuis</th>
              <th className="py-2 pl-2 font-medium">Dernière connexion</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((row) => (
              <tr key={`${row.org_id}-${row.user.user_id}`}>
                <td className="py-2 pr-2 font-medium">{row.org_name}</td>
                <td className="px-2 py-2">{row.user.email || "(sans e-mail)"}</td>
                <td className="px-2 py-2 text-muted">{formatDate(row.since)}</td>
                <td className="py-2 pl-2 text-muted">{row.user.last_sign_in_at ? formatDateTime(row.user.last_sign_in_at) : "jamais"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/** /back-office/accounts — every account and the organizations it belongs to. */
export function Accounts() {
  const users = useStaffUsers();
  const [search, setSearch] = useState("");
  const list = (users.data?.users ?? []).filter((user) => user.email.toLowerCase().includes(search.trim().toLowerCase()));
  return (
    <>
      <PageHeader title="Comptes" />
      <div className="grid gap-4">
        <AddPerson />
        {users.data ? <OrgAdmins users={users.data.users} /> : null}
        <Section title={`${users.data?.users.length ?? 0} comptes`} aside={<Input aria-label="Chercher un e-mail" placeholder="Chercher un e-mail" value={search} onChange={(event) => setSearch(event.target.value)} className="w-56" />}>
          {users.isLoading ? <Skeleton className="h-40" /> : null}
          {users.error ? <EmptyState title="Impossible de charger les comptes" body={errorMessage(users.error)} /> : null}
          <ul className="divide-y divide-line">{list.map((user) => <UserRow key={user.user_id} user={user} />)}</ul>
        </Section>
      </div>
    </>
  );
}
