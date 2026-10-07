import { useState, type FormEvent } from "react";
import { useToast } from "../../components/feedback";
import { Button, Card, FieldLabel, Input } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { useOrg } from "../../lib/org";

const MIN_PASSWORD = 8;

function ChangePassword() {
  const auth = useAuth();
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await auth.setPassword(password);
      setPassword("");
      toast.show({ tone: "success", message: "Mot de passe modifié" });
    } catch (error) {
      toast.show({ tone: "error", message: "Le mot de passe n'a pas été modifié", description: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="mt-5 flex max-w-md flex-wrap items-end gap-3" onSubmit={(event) => void submit(event)}>
      <div className="min-w-56 flex-1">
        <FieldLabel htmlFor="new-password" hint={`${MIN_PASSWORD} caractères au moins`}>Nouveau mot de passe</FieldLabel>
        <Input id="new-password" type="password" autoComplete="new-password" minLength={MIN_PASSWORD} value={password} onChange={(event) => setPassword(event.target.value)} />
      </div>
      <Button type="submit" loading={saving} disabled={password.length < MIN_PASSWORD}>Changer</Button>
    </form>
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
