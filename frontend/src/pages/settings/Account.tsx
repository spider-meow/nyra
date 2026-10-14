import { useState } from "react";
import { useToast } from "../../components/feedback";
import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { Button } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { useOrg } from "../../lib/org";

/** The password is chosen on the page the e-mailed link opens (with its strength meter), never typed here. */
function ChangePassword() {
  const auth = useAuth();
  const toast = useToast();
  const [sending, setSending] = useState(false);

  async function send() {
    setSending(true);
    try {
      await auth.requestReset(auth.email);
      toast.show({ tone: "success", message: "Lien envoyé", description: `Consultez la messagerie de ${auth.email}.` });
    } catch (error) {
      toast.show({ tone: "error", message: "Le lien n'a pas été envoyé", description: errorMessage(error) });
    } finally {
      setSending(false);
    }
  }

  return <Button loading={sending} onClick={() => void send()}>Recevoir un lien</Button>;
}

/** The signed-in person: e-mail, rights, password, sign out. */
export function Account() {
  const { admin, org } = useOrg();
  const auth = useAuth();
  return (
    <SettingsBlock title="Connexion">
      <SettingsRow label="Adresse e-mail"><span className="text-muted">{auth.email}</span></SettingsRow>
      <SettingsRow label="Droits"><span className="text-muted">{admin ? `Administrateur de ${org.name}` : `Lecture et validation, ${org.name}`}</span></SettingsRow>
      <SettingsRow label="Mot de passe" hint="Nous vous envoyons un lien par e-mail pour en choisir un nouveau."><ChangePassword /></SettingsRow>
      <SettingsRow label="Session"><Button onClick={() => void auth.signOut()}>Se déconnecter</Button></SettingsRow>
    </SettingsBlock>
  );
}
