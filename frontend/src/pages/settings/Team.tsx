import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { Select } from "../../components/Dropdown";
import { Button, Card, FieldLabel, Input, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import { useMemberMutations, useMembers } from "../../lib/queries";
import type { Member } from "../../types";

const ROLE_LABEL: Record<Member["role"], string> = { admin: "Administrateur", client: "Lecture et validation" };
const ROLE_HELP = "Un administrateur gère la bibliothèque, les lectures et les réglages. Les autres consultent et valident les correspondances.";

function MemberRow(props: { member: Member }) {
  const { admin, org } = useOrg();
  const { setRole, remove } = useMemberMutations();
  const confirm = useConfirm();
  const toast = useToast();
  const { member } = props;
  const fail = (message: string) => (error: Error) => toast.show({ tone: "error", message, description: errorMessage(error) });

  async function leave() {
    const ok = await confirm({
      title: `Retirer ${member.email} ?`,
      body: <p>Cette personne n'aura plus accès à {org.name}. Son compte n'est pas supprimé : vous pourrez la réinviter.</p>,
      confirm: "Retirer",
      danger: true,
    });
    if (ok) remove.mutate(member.user_id, { onError: fail("La personne n'a pas été retirée") });
  }

  return (
    <li className="flex flex-wrap items-center gap-3 px-5 py-2.5 text-sm">
      <span className="min-w-0 flex-1 truncate">
        {member.email}
        {member.you ? <span className="text-muted"> · vous</span> : null}
      </span>
      {admin && !member.you ? (
        <>
          <Select
            aria-label={`Rôle de ${member.email}`}
            value={member.role}
            disabled={setRole.isPending}
            onChange={(event) => setRole.mutate({ userId: member.user_id, role: event.target.value as Member["role"] }, { onError: fail("Le rôle n'a pas été modifié") })}
          >
            <option value="client">{ROLE_LABEL.client}</option>
            <option value="admin">{ROLE_LABEL.admin}</option>
          </Select>
          <Button size="sm" variant="ghost" loading={remove.isPending && remove.variables === member.user_id} onClick={() => void leave()}>Retirer</Button>
        </>
      ) : (
        <span className="text-muted">{ROLE_LABEL[member.role]}</span>
      )}
    </li>
  );
}

function Invite() {
  const { invite } = useMemberMutations();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Member["role"]>("client");

  function submit(event: FormEvent) {
    event.preventDefault();
    invite.mutate(
      { email, role },
      {
        onSuccess: ({ invited }) => {
          setEmail("");
          toast.show({
            tone: "success",
            message: invited ? "Invitation envoyée" : "Personne ajoutée",
            description: invited ? "Elle recevra un e-mail pour choisir son mot de passe." : "Elle a déjà un compte : elle peut se connecter.",
          });
        },
        onError: (error) => toast.show({ tone: "error", message: "La personne n'a pas été ajoutée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <form className="flex flex-wrap items-end gap-3 border-t border-line px-5 py-4" onSubmit={submit}>
      <div className="min-w-56 flex-1">
        <FieldLabel htmlFor="invite-email">Inviter par e-mail</FieldLabel>
        <Input id="invite-email" type="email" required maxLength={254} placeholder="prenom.nom@entreprise.com" value={email} onChange={(event) => setEmail(event.target.value)} />
      </div>
      <div>
        <FieldLabel htmlFor="invite-role">Rôle</FieldLabel>
        <Select id="invite-role" value={role} onChange={(event) => setRole(event.target.value as Member["role"])}>
          <option value="client">{ROLE_LABEL.client}</option>
          <option value="admin">{ROLE_LABEL.admin}</option>
        </Select>
      </div>
      <Button type="submit" variant="primary" loading={invite.isPending} disabled={!email.trim()}>Inviter</Button>
    </form>
  );
}

/** Who can open the organization, and with which rights. */
export function Team() {
  const { admin, org } = useOrg();
  const members = useMembers();
  return (
    <Card padded={false}>
      <div className="px-5 pt-5">
        <h2 className="font-semibold">Équipe de {org.name}</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">{ROLE_HELP}</p>
      </div>
      {members.isLoading ? <Skeleton className="m-5 h-24" /> : null}
      {members.error ? <p className="px-5 py-4 text-sm text-expired">{errorMessage(members.error)}</p> : null}
      <ul className="mt-3 divide-y divide-line border-t border-line">
        {(members.data?.members ?? []).map((member) => <MemberRow key={member.user_id} member={member} />)}
      </ul>
      {admin ? <Invite /> : null}
    </Card>
  );
}
