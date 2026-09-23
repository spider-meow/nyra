import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, Outlet, useNavigate, useParams } from "react-router";
import { Layout } from "../components/Layout";
import { Button, Card, FieldLabel, Input, Spinner } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { OrgProvider, useOrganizations } from "../lib/org";

function Centered(props: { title: string; body?: ReactNode; children?: ReactNode }) {
  return (
    <div className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-[15px] font-semibold tracking-tight">Nyra</p>
        <Card>
          <h1 className="text-xl font-semibold tracking-tight">{props.title}</h1>
          {props.body ? <p className="mt-1.5 text-sm text-muted">{props.body}</p> : null}
          {props.children}
        </Card>
      </div>
    </div>
  );
}

/** Everything behind a session. Signed-out visitors go to /connexion. */
export function RequireSession() {
  const auth = useAuth();
  if (auth.phase === "loading") return <Centered title="Un instant"><div className="mt-4"><Spinner label="Vérification de la session…" /></div></Centered>;
  if (auth.phase === "unconfigured") return <Centered title="Service indisponible" body={auth.problem} />;
  if (auth.phase === "signed-out") return <Navigate to="/connexion" replace />;
  if (auth.mustSetPassword) return <Navigate to="/mot-de-passe" replace />;
  return <Outlet />;
}

export function Login() {
  const auth = useAuth();
  const [mode, setMode] = useState<"password" | "reset" | "sent">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  if (auth.phase === "signed-in") return <Navigate to={auth.mustSetPassword ? "/mot-de-passe" : "/"} replace />;
  if (auth.phase === "unconfigured") return <Centered title="Service indisponible" body={auth.problem} />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      if (mode === "password") {
        await auth.signIn(email, password);
      } else {
        await auth.requestReset(email);
        setMode("sent");
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  if (mode === "sent") {
    return (
      <Centered title="Vérifiez votre messagerie" body={`Si un compte existe pour ${email}, un lien pour choisir un nouveau mot de passe vient d'être envoyé.`}>
        <Button className="mt-5 w-full" onClick={() => setMode("password")}>Retour à la connexion</Button>
      </Centered>
    );
  }

  return (
    <Centered
      title={mode === "password" ? "Connexion" : "Mot de passe oublié"}
      body={mode === "password" ? "L'accès se fait sur invitation de votre administrateur." : "Nous vous envoyons un lien pour en choisir un nouveau."}
    >
      <form className="mt-5 grid gap-4" onSubmit={(event) => void submit(event)}>
        <div>
          <FieldLabel htmlFor="email">Adresse e-mail</FieldLabel>
          <Input id="email" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
        </div>
        {mode === "password" ? (
          <div>
            <FieldLabel htmlFor="password">Mot de passe</FieldLabel>
            <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          </div>
        ) : null}
        {error ? <p className="text-sm text-expired" role="alert">{error}</p> : null}
        <Button type="submit" variant="primary" disabled={pending || auth.phase === "loading"}>
          {pending ? "Un instant…" : mode === "password" ? "Se connecter" : "Envoyer le lien"}
        </Button>
        <button
          type="button"
          className="text-sm text-muted underline-offset-2 hover:text-ink hover:underline"
          onClick={() => {
            setError("");
            setMode(mode === "password" ? "reset" : "password");
          }}
        >
          {mode === "password" ? "Mot de passe oublié ?" : "Retour à la connexion"}
        </button>
      </form>
    </Centered>
  );
}

export function SetPassword() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  if (auth.phase === "loading") return <Centered title="Un instant"><div className="mt-4"><Spinner /></div></Centered>;
  if (auth.phase !== "signed-in") {
    return (
      <Centered title="Lien expiré" body="Ce lien n'est plus valide. Demandez-en un nouveau depuis la page de connexion.">
        <Link to="/connexion" className="mt-5 block text-sm underline">Aller à la connexion</Link>
      </Centered>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password.length < 10) return setError("Au moins 10 caractères.");
    if (password !== confirm) return setError("Les deux mots de passe ne correspondent pas.");
    setPending(true);
    setError("");
    try {
      await auth.setPassword(password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setPending(false);
    }
  }

  return (
    <Centered title="Choisissez votre mot de passe" body={`Pour le compte ${auth.email}.`}>
      <form className="mt-5 grid gap-4" onSubmit={(event) => void submit(event)}>
        <div>
          <FieldLabel htmlFor="new-password" hint="10 caractères minimum">Nouveau mot de passe</FieldLabel>
          <Input id="new-password" type="password" autoComplete="new-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
        </div>
        <div>
          <FieldLabel htmlFor="confirm-password">Confirmation</FieldLabel>
          <Input id="confirm-password" type="password" autoComplete="new-password" required value={confirm} onChange={(event) => setConfirm(event.target.value)} />
        </div>
        {error ? <p className="text-sm text-expired" role="alert">{error}</p> : null}
        <Button type="submit" variant="primary" disabled={pending}>{pending ? "Enregistrement…" : "Enregistrer et continuer"}</Button>
      </form>
    </Centered>
  );
}

/** "/" → the only organization, or a picker when there are several. */
export function OrgHome() {
  const orgs = useOrganizations();
  const auth = useAuth();
  if (orgs.isLoading) return <Centered title="Un instant"><div className="mt-4"><Spinner label="Chargement des organisations…" /></div></Centered>;
  if (orgs.error) return <Centered title="Erreur" body={errorMessage(orgs.error)} />;
  const list = orgs.data ?? [];
  if (list.length === 1) return <Navigate to={`/o/${list[0].slug}/tableau-de-bord`} replace />;
  if (list.length === 0) {
    return (
      <Centered title="Aucune organisation" body={`Le compte ${auth.email} n'est rattaché à aucune organisation. Demandez à votre administrateur de vous inviter.`}>
        <Button className="mt-5 w-full" onClick={() => void auth.signOut()}>Se déconnecter</Button>
      </Centered>
    );
  }
  return (
    <Centered title="Organisations" body="Choisissez l'espace à ouvrir.">
      <div className="mt-5 grid gap-2">
        {list.map((org) => (
          <Link key={org.org_id} to={`/o/${org.slug}/tableau-de-bord`} className="rounded-lg border border-line-strong px-3 py-2.5 text-sm font-medium hover:bg-canvas">
            {org.name}
            <span className="block text-xs font-normal text-muted">{org.role === "admin" ? "Administrateur" : "Lecture et validation"}</span>
          </Link>
        ))}
      </div>
    </Centered>
  );
}

/** /o/:slug/* — resolves the organization, then renders the app shell. */
export function OrgShell() {
  const { slug } = useParams();
  const orgs = useOrganizations();
  if (orgs.isLoading) return <Centered title="Un instant"><div className="mt-4"><Spinner /></div></Centered>;
  const org = orgs.data?.find((item) => item.slug === slug);
  if (!org) return <NotFound message="Cette organisation n'existe pas, ou vous n'y avez pas accès." />;
  return (
    <OrgProvider org={org}>
      <Layout />
    </OrgProvider>
  );
}

export function NotFound(props: { message?: string }) {
  return (
    <Centered title="Page introuvable" body={props.message ?? "L'adresse ne correspond à aucune page."}>
      <Link to="/" className="mt-5 block text-sm underline">Revenir à l'accueil</Link>
    </Centered>
  );
}
