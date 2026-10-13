import { useState } from "react";
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

  return (
    <div className="mt-5">
      <p className="text-sm text-muted">Pour changer de mot de passe, nous vous envoyons un lien par e-mail.</p>
      <Button className="mt-3" loading={sending} onClick={() => void send()}>Recevoir un lien</Button>
    </div>
  );
}

/** The signed-in person: e-mail, rights, password, sign out. */
export function Account() {
  const { admin, org } = useOrg();
  const auth = useAuth();
  return (
    <Card>
      <h2 className="font-semibold">Mon compte</h2>
      <p className="mt-1 text-sm text-muted">
        {auth.email} · {admin ? `administrateur de ${org.name}` : `lecture et validation, ${org.name}`}
      </p>
      <ChangePassword />
      <Button className="mt-6" onClick={() => void auth.signOut()}>Se déconnecter</Button>
    </Card>
  );
}
