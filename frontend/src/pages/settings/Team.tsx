import { useState, type FormEvent } from "react";
import { Avatar } from "../../components/Avatar";
import { useConfirm, useToast } from "../../components/feedback";
import { Select } from "../../components/Dropdown";
import { Icon } from "../../components/icons";
import { Button, Card, cx, FieldLabel, Input, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useMemberMutations, useMembers } from "../../lib/queries";
import type { Member } from "../../types";
import { displayName, isLastAdmin, matches, ROLE_HELP, ROLE_LABEL, SEARCH_ABOVE, splitPending } from "./team";

const ROLES: Member["role"][] = ["client", "admin"];

function MemberCard(props: { member: Member; last: boolean }) {
  const { admin, org } = useOrg();
  const { setRole, remove } = useMemberMutations();
  const confirm = useConfirm();
  const toast = useToast();
  const { member, last } = props;
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

  const editable = admin && !member.you;
  const help = !admin || !last ? ROLE_HELP[member.role] : member.you ? "Vous êtes le seul administrateur : invitez-en un autre." : "Dernier administrateur : nommez-en un autre avant de le retirer ou de changer son rôle.";
  return (
    <li className={cx("grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-3 rounded-xl border bg-paper p-4", member.you ? "border-ink" : "border-line")}>
      <div className="flex items-center gap-3">
        <Avatar seed={member.user_id} size={48} />
        <div className="min-w-0 leading-tight">
          <p className="truncate text-[15px] font-semibold">
            {displayName(member.email)}
            {member.you ? <span className="ml-2 rounded-full bg-ink px-2 py-0.5 align-[2px] text-[11.5px] font-medium text-white">vous</span> : null}
          </p>
          <p className="truncate text-[13px] text-muted" title={member.email}>{member.email}</p>
        </div>
      </div>
      {editable ? (
        <div className="flex items-center gap-2 border-t border-line pt-3">
          <Select
            className="min-w-0 flex-1"
            aria-label={`Rôle de ${member.email}`}
            value={member.role}
            disabled={setRole.isPending || last}
            onChange={(event) => {
              const role = ROLES.find((known) => known === event.target.value);
              if (role) setRole.mutate({ userId: member.user_id, role }, { onError: fail("Le rôle n'a pas été modifié") });
            }}
          >
            {ROLES.map((role) => <option key={role} value={role}>{ROLE_LABEL[role]}</option>)}
          </Select>
          <Button size="sm" variant="ghost" disabled={last} loading={remove.isPending && remove.variables === member.user_id} onClick={() => void leave()}>Retirer</Button>
        </div>
      ) : (
        <p className="border-t border-line pt-3"><span className={cx("rounded-full px-2.5 py-0.5 text-[12.5px] font-medium", member.role === "admin" ? "bg-yellow" : "bg-sunk text-ink-soft")}>{ROLE_LABEL[member.role]}</span></p>
      )}
      <p className="text-[12.5px] text-muted">{help}{member.you && admin && !last ? " Vous ne pouvez pas changer votre propre rôle." : ""}</p>
    </li>
  );
}

/** An invitation nobody opened yet: when it was sent, and for an administrator « Renvoyer » (the mail again) and « Annuler » (the person is removed). */
function PendingCard({ member }: { member: Member }) {
  const { admin, org } = useOrg();
  const { resend, remove } = useMemberMutations();
  const confirm = useConfirm();
  const toast = useToast();

  async function cancel() {
    const ok = await confirm({
      title: `Annuler l'invitation de ${member.email} ?`,
      body: <p>Cette personne n'aura pas accès à {org.name}. Vous pourrez l'inviter de nouveau.</p>,
      confirm: "Annuler l'invitation",
      danger: true,
    });
    if (ok) remove.mutate(member.user_id, { onError: (error) => toast.show({ tone: "error", message: "L'invitation n'a pas été annulée", description: errorMessage(error) }) });
  }

  return (
    <li className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-3 rounded-xl border-[1.5px] border-dashed border-line-strong bg-canvas p-4">
      <div className="flex items-center gap-3">
        <span aria-hidden className="grid size-12 shrink-0 place-items-center rounded-full border-[1.5px] border-dashed border-line-strong bg-sunk text-faint"><Icon name="mail" size={20} /></span>
        <div className="min-w-0 leading-tight">
          <p className="truncate text-[15px] font-semibold" title={member.email}>{member.email}</p>
          <p className="text-[13px] text-muted">Invitation envoyée le {formatDate(member.invited_at)} · {ROLE_LABEL[member.role]}</p>
        </div>
      </div>
      {admin ? (
        <div className="flex gap-2">
          <Button
            size="sm"
            loading={resend.isPending && resend.variables === member.user_id}
            onClick={() => resend.mutate(member.user_id, {
              onSuccess: ({ invited }) => toast.show({ tone: "success", message: invited ? "Invitation renvoyée" : "Cette personne a déjà accepté l'invitation" }),
              onError: (error) => toast.show({ tone: "error", message: "L'invitation n'a pas été renvoyée", description: errorMessage(error) }),
            })}
          >
            Renvoyer
          </Button>
          <Button size="sm" variant="ghost" loading={remove.isPending && remove.variables === member.user_id} onClick={() => void cancel()}>Annuler</Button>
        </div>
      ) : null}
    </li>
  );
}

/** The two roles as two cards that say what each allows. */
function RoleChoice(props: { value: Member["role"]; onChange: (role: Member["role"]) => void }) {
  return (
    <div role="radiogroup" aria-label="Rôle" className="grid max-w-3xl gap-3 sm:grid-cols-2">
      {ROLES.map((role) => (
        <button
          key={role}
          type="button"
          role="radio"
          aria-checked={props.value === role}
          onClick={() => props.onChange(role)}
          className={cx("grid gap-0.5 rounded-xl border px-4 py-3 text-left", props.value === role ? "border-2 border-ink bg-yellow/30" : "border-line-strong bg-paper hover:bg-sunk")}
        >
          <span className="flex items-center gap-1.5 text-sm font-medium">{ROLE_LABEL[role]}{props.value === role ? <Icon name="check" size={14} strokeWidth={3} /> : null}</span>
          <span className="text-[13px] text-muted">{ROLE_HELP[role]}</span>
        </button>
      ))}
    </div>
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
    <form className="grid gap-4 border-t border-line bg-sunk px-5 py-5" onSubmit={submit}>
      <div className="max-w-md">
        <FieldLabel htmlFor="invite-email">Inviter par e-mail</FieldLabel>
        <Input id="invite-email" autoFocus type="email" required maxLength={254} placeholder="prenom.nom@entreprise.com" value={email} onChange={(event) => setEmail(event.target.value)} />
      </div>
      <div>
        <FieldLabel>Rôle</FieldLabel>
        <RoleChoice value={role} onChange={setRole} />
      </div>
      <div className="flex gap-3">
        <Button type="submit" variant="primary" loading={invite.isPending} disabled={!email.trim()}>Envoyer l'invitation</Button>
        <Button variant="ghost" onClick={props.onDone}>Annuler</Button>
      </div>
    </form>
  );
}

/** Who can open the organization, and with which rights. */
export function Team() {
  const { admin } = useOrg();
  const members = useMembers();
  const [inviting, setInviting] = useState(false);
  const [query, setQuery] = useState("");
  const all = members.data?.members ?? [];
  const { active, waiting } = splitPending(all.filter((member) => matches(member, query)));
  const total = splitPending(all);
  const alone = all.length === 1 && all[0].you;
  return (
    <Card padded={false} tone="quiet">
      <div className="flex flex-wrap items-center gap-3 px-5 py-3">
        <p className="flex-1 text-sm font-medium text-muted">{members.data ? `${total.active.length} ${total.active.length > 1 ? "membres" : "membre"}${total.waiting.length ? ` · ${total.waiting.length} ${total.waiting.length > 1 ? "invitations" : "invitation"} en attente` : ""}` : "Membres"}</p>
        {all.length > SEARCH_ABOVE ? <Input type="search" className="w-full sm:w-64" aria-label="Rechercher une personne" placeholder="Rechercher une personne…" value={query} onChange={(event) => setQuery(event.target.value)} /> : null}
        {admin && !inviting ? <Button size="sm" variant="primary" onClick={() => setInviting(true)}>Inviter un membre</Button> : null}
      </div>
      {inviting ? <Invite onDone={() => setInviting(false)} /> : null}
      {members.isLoading ? <Skeleton className="m-5 h-24" /> : null}
      {members.error ? <p className="px-5 py-4 text-sm text-expired">{errorMessage(members.error)}</p> : null}
      <ul className="grid grid-cols-1 gap-3 border-t border-line p-5 sm:grid-cols-2">
        {active.map((member) => <MemberCard key={member.user_id} member={member} last={isLastAdmin(all, member)} />)}
      </ul>
      {waiting.length ? (
        <>
          <h3 className="border-t border-line px-5 pb-1 pt-4 text-[13px] font-medium uppercase tracking-wide text-muted">Invitations en attente</h3>
          <ul className="grid grid-cols-1 gap-3 px-5 pb-5 pt-2 sm:grid-cols-2">
            {waiting.map((member) => <PendingCard key={member.user_id} member={member} />)}
          </ul>
        </>
      ) : null}
      {query && !active.length && !waiting.length ? <p className="px-5 pb-5 text-sm text-muted">Aucun membre ne correspond à « {query} ».</p> : null}
      {alone ? (
        <div className="grid justify-items-center gap-2 px-5 pb-8 pt-2 text-center">
          <p className="text-lg font-semibold">Personne d'autre pour l'instant</p>
          <p className="max-w-sm text-sm text-muted">Invitez votre première personne : elle recevra un e-mail pour choisir son mot de passe.</p>
          {admin && !inviting ? <Button variant="primary" onClick={() => setInviting(true)}>Inviter un membre</Button> : null}
        </div>
      ) : null}
    </Card>
  );
}
