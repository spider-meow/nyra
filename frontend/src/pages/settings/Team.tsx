import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { Select } from "../../components/Dropdown";
import { Button, Card, cx, FieldLabel, Input, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import { useMemberMutations, useMembers } from "../../lib/queries";
import type { Member } from "../../types";

const ROLE_LABEL: Record<Member["role"], string> = { admin: "Administrateur", client: "Lecture et validation" };

/** Two letters from the e-mail: "adrien.duchemin@…" gives AD. */
const initials = (email: string) => email.split("@")[0].split(/[._-]/).slice(0, 2).map((part) => part[0]).join("");

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
    <li className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
      <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-full bg-mint text-xs font-medium uppercase">{initials(member.email)}</span>
      <span className="min-w-0 flex-1 truncate">
        {member.email}
        {member.you ? <span className="text-muted"> · vous</span> : null}
      </span>
      {admin && !member.you ? (
        <>
          <Select
            className="w-52"
            aria-label={`Rôle de ${member.email}`}
            value={member.role}
            disabled={setRole.isPending}
            onChange={(event) => setRole.mutate({ userId: member.user_id, role: event.target.value as Member["role"] }, { onError: fail("Le rôle n'a pas été modifié") })}
          >
            <option value="client">{ROLE_LABEL.client}</option>
            <option value="admin">{ROLE_LABEL.admin}</option>
          </Select>
          <Button size="sm" variant="ghost" className="w-20" loading={remove.isPending && remove.variables === member.user_id} onClick={() => void leave()}>Retirer</Button>
        </>
      ) : (
        <span className={cx("rounded-md bg-sunk px-2.5 py-1 text-[13px] text-muted", admin && "sm:mr-[5.75rem]")}>{ROLE_LABEL[member.role]}</span>
      )}
    </li>
  );
}

function Invite(props: { onDone: () => void }) {
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
          props.onDone();
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
    <form className="flex flex-wrap items-end gap-3 border-t border-line bg-sunk px-5 py-4" onSubmit={submit}>
      <div className="min-w-56 flex-1">
        <FieldLabel htmlFor="invite-email">Inviter par e-mail</FieldLabel>
        <Input id="invite-email" autoFocus type="email" required maxLength={254} placeholder="prenom.nom@entreprise.com" value={email} onChange={(event) => setEmail(event.target.value)} />
      </div>
      <div>
        <FieldLabel htmlFor="invite-role">Rôle</FieldLabel>
        <Select id="invite-role" value={role} onChange={(event) => setRole(event.target.value as Member["role"])}>
          <option value="client">{ROLE_LABEL.client}</option>
          <option value="admin">{ROLE_LABEL.admin}</option>
        </Select>
      </div>
      <Button type="submit" variant="primary" loading={invite.isPending} disabled={!email.trim()}>Envoyer l'invitation</Button>
      <Button variant="ghost" onClick={props.onDone}>Annuler</Button>
    </form>
  );
}

/** Who can open the organization, and with which rights. */
export function Team() {
  const { admin } = useOrg();
  const members = useMembers();
  const [inviting, setInviting] = useState(false);
  const list = members.data?.members ?? [];
  return (
    <Card padded={false} tone="quiet">
      <div className="flex items-center justify-between gap-3 px-5 py-3">
        <p className="text-sm text-muted">{members.data ? `${list.length} ${list.length > 1 ? "membres" : "membre"}` : "Membres"}</p>
        {admin && !inviting ? <Button size="sm" variant="primary" onClick={() => setInviting(true)}>Inviter un membre</Button> : null}
      </div>
      {inviting ? <Invite onDone={() => setInviting(false)} /> : null}
      {members.isLoading ? <Skeleton className="m-5 h-24" /> : null}
      {members.error ? <p className="px-5 py-4 text-sm text-expired">{errorMessage(members.error)}</p> : null}
      <ul className="divide-y divide-line border-t border-line">
        {list.map((member) => <MemberRow key={member.user_id} member={member} />)}
      </ul>
    </Card>
  );
}
