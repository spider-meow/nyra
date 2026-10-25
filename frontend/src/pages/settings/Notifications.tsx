import { SettingsBlock } from "../../components/SettingsBlock";
import { Button, Checkbox, EmptyState, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useNotifications, useSetNotifications } from "../../lib/queries";
import type { Notifications as NotificationsData } from "../../types";

/** The e-mails a person may switch off. One today; the next one is another line here and another key on the API. */
const EMAILS = [
  {
    key: "monthly_email",
    label: "Résumé mensuel",
    hint: "Nouveaux visuels à traiter et visuels qui expirent bientôt (30 et 90 jours). Envoyé une fois par mois aux administrateurs.",
  },
] as const;

/** Why the box cannot be ticked, if it cannot: the role does not receive this e-mail, or the Nyra team stopped it. */
function lockedReason(state: NotificationsData["monthly_email"]): string | null {
  if (state.eligible !== true) return "Ce message est réservé aux administrateurs de l'organisation.";
  return state.allowed !== false ? null : "L'équipe Nyra a désactivé cet envoi pour votre compte.";
}

/** The signed-in person's choice of the e-mails Nyra sends them. The service e-mails (invitation, password) are not listed. */
export function Notifications() {
  const query = useNotifications();
  const save = useSetNotifications();

  if (query.isLoading) return <Skeleton className="h-40" />;
  if (query.error || !query.data?.monthly_email) {  // an answer without the field is an error, not a crash
    return (
      <div role="alert">
        <EmptyState title="Notifications indisponibles" body={errorMessage(query.error)} action={<Button size="sm" onClick={() => void query.refetch()}>Réessayer</Button>} />
      </div>
    );
  }
  const state = query.data.monthly_email;
  const reason = lockedReason(state);
  return (
    <SettingsBlock title="E-mails de Nyra">
      <div className="grid gap-4 p-5" aria-busy={save.isPending}>
        {EMAILS.map((email) => (
          <div key={email.key} className="grid gap-1.5">
            <Checkbox
              checked={state.eligible === true && state.allowed !== false && state.wanted !== false}
              disabled={reason !== null || save.isPending}
              onChange={(value) => save.mutate({ [email.key]: value })}
              label={email.label}
              hint={email.hint}
            />
            {reason ? <p className="pl-[28px] text-[13px] text-muted">{reason}</p> : null}
          </div>
        ))}
        {save.error ? <p role="alert" className="text-sm text-terracotta">{errorMessage(save.error)}</p> : null}
        <p className="text-[13px] text-muted">Les e-mails de service (invitation, nouveau mot de passe) ne peuvent pas être désactivés.</p>
      </div>
    </SettingsBlock>
  );
}
