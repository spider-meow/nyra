import { useState } from "react";
import { useToast } from "../../components/feedback";
import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { Button } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { useOrg } from "../../lib/org";

const RESEND_DELAY_MS = 30_000;

/** The password is chosen on the page the e-mailed link opens (with its strength meter), never typed here. */
function ChangePassword() {
  const auth = useAuth();
  const toast = useToast();
  const [sending, setSending] = useState(false);
  const [waiting, setWaiting] = useState(false);

  async function send() {
    setSending(true);
    try {
      await auth.requestReset(auth.email);
      // Off for a while: Supabase limits how many links it sends. A timer left running after leaving the page does nothing.
      setWaiting(true);
      setTimeout(() => setWaiting(false), RESEND_DELAY_MS);
      toast.show({ tone: "success", message: "Lien envoyé", description: `Consultez la messagerie de ${auth.email}.` });
    } catch (error) {
      toast.show({ tone: "error", message: "Le lien n'a pas été envoyé", description: errorMessage(error) });
    } finally {
      setSending(false);
    }
  }

  return <Button loading={sending} disabled={waiting} onClick={() => void send()}>{waiting ? "Lien envoyé" : "Changer mon mot de passe"}</Button>;
}

/** The signed-in person: e-mail, role, password, sign out. */
export function Account() {
  const { admin, org } = useOrg();
  const auth = useAuth();
  return (
    <SettingsBlock title="Identifiants">
      <SettingsRow label="Adresse e-mail"><span className="text-muted">{auth.email}</span></SettingsRow>
      <SettingsRow label="Rôle" hint={org.name}><span className="text-muted">{admin ? "Administrateur" : "Lecture et validation"}</span></SettingsRow>
      <SettingsRow label="Mot de passe" hint={`Un lien de réinitialisation sera envoyé à ${auth.email}.`}><ChangePassword /></SettingsRow>
      <SettingsRow label="Session"><Button onClick={() => void auth.signOut()}>Se déconnecter</Button></SettingsRow>
    </SettingsBlock>
  );
}
