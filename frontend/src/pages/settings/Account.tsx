import { useState, type ReactNode } from "react";
import { useToast } from "../../components/feedback";
import { Button, Card } from "../../components/ui";
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

function Row(props: { label: string; hint?: string; children: ReactNode }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 text-sm">
      <div>
        <p className="font-medium">{props.label}</p>
        {props.hint ? <p className="mt-0.5 text-muted">{props.hint}</p> : null}
      </div>
      {props.children}
    </li>
  );
}

/** The signed-in person: e-mail, rights, password, sign out. */
export function Account() {
  const { admin, org } = useOrg();
  const auth = useAuth();
  return (
    <Card padded={false} tone="quiet">
      <ul className="divide-y divide-line">
        <Row label="Adresse e-mail"><span className="text-muted">{auth.email}</span></Row>
        <Row label="Droits"><span className="text-muted">{admin ? `Administrateur de ${org.name}` : `Lecture et validation, ${org.name}`}</span></Row>
        <Row label="Mot de passe" hint="Nous vous envoyons un lien par e-mail pour en choisir un nouveau."><ChangePassword /></Row>
        <Row label="Session"><Button onClick={() => void auth.signOut()}>Se déconnecter</Button></Row>
      </ul>
    </Card>
  );
}
