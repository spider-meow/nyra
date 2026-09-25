import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, Outlet, useNavigate, useParams } from "react-router";
import { Layout } from "../components/Layout";
import { Logo } from "../components/Logo";
import { Button, Card, FieldLabel, Input, Spinner } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { OrgProvider, brandLink, useOrganizations } from "../lib/org";
import type { Brand, Organization } from "../types";

function Centered(props: { title: string; body?: ReactNode; children?: ReactNode }) {
  return (
    <div className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex flex-col items-center gap-1.5">
          <div className="flex items-center gap-2.5">
            <Logo size={28} />
            <p className="font-display text-[30px] leading-none">Nyra</p>
          </div>
          <p className="text-[11px] font-medium tracking-[0.18em] text-muted uppercase">by Axel Project</p>
        </div>
        <Card className="p-8 shadow-float">
          <h1 className="font-display text-[34px] leading-none">{props.title}</h1>
          {props.body ? <p className="mt-2.5 text-sm leading-relaxed text-muted">{props.body}</p> : null}
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
      <form className="mt-6 grid gap-4" onSubmit={(event) => void submit(event)}>
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
        <Button type="submit" variant="primary" size="lg" disabled={pending || auth.phase === "loading"}>
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
      <form className="mt-6 grid gap-4" onSubmit={(event) => void submit(event)}>
        <div>
          <FieldLabel htmlFor="new-password" hint="10 caractères minimum">Nouveau mot de passe</FieldLabel>
          <Input id="new-password" type="password" autoComplete="new-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
        </div>
        <div>
          <FieldLabel htmlFor="confirm-password">Confirmation</FieldLabel>
          <Input id="confirm-password" type="password" autoComplete="new-password" required value={confirm} onChange={(event) => setConfirm(event.target.value)} />
        </div>
        {error ? <p className="text-sm text-expired" role="alert">{error}</p> : null}
        <Button type="submit" variant="primary" size="lg" disabled={pending}>{pending ? "Enregistrement…" : "Enregistrer et continuer"}</Button>
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
  if (list.length === 1) return <Navigate to={`/o/${list[0].slug}`} replace />;
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
          <Link key={org.org_id} to={`/o/${org.slug}`} className="rounded-xl border border-line-strong bg-paper px-4 py-3 text-sm font-medium hover:border-faint">
            {org.name}
            <span className="block text-xs font-normal text-muted">{org.role === "admin" ? "Administrateur" : "Lecture et validation"}</span>
          </Link>
        ))}
      </div>
    </Centered>
  );
}

const ShellOrg = createContext<Organization | null>(null);

function useShellOrg(): Organization {
  const org = useContext(ShellOrg);
  if (!org) throw new Error("OrgShell manquant.");
  return org;
}

/** /o/:slug/*: resolves the organization; the brand is resolved one level down. */
export function OrgShell() {
  const { slug } = useParams();
  const orgs = useOrganizations();
  if (orgs.isLoading) return <Centered title="Un instant"><div className="mt-4"><Spinner /></div></Centered>;
  if (orgs.error) return <Centered title="Erreur" body={errorMessage(orgs.error)} />;
  const org = orgs.data?.find((item) => item.slug === slug);
  if (!org) return <NotFound message="Cette organisation n'existe pas, ou vous n'y avez pas accès." />;
  // An API from before brands answers without them.
  if (!Array.isArray(org.brands)) {
    return <Centered title="Serveur à mettre à jour" body="L'API ne connaît pas encore les marques. Redémarrez-la (ou redéployez-la) avec la dernière version du code." />;
  }
  return (
    <ShellOrg.Provider value={org}>
      <Outlet />
    </ShellOrg.Provider>
  );
}

/**
 * /o/:slug, and addresses from before brands (/o/:slug/bibliotheque): the
 * same page of the organization's first brand.
 */
export function FirstBrand() {
  const org = useShellOrg();
  const params = useParams();
  const first = org.brands[0];
  if (!first) return <NoBrand org={org} />;
  return <Navigate to={brandLink(org, first.slug, params["*"] || "tableau-de-bord")} replace />;
}

/** /o/:slug/m/:brand/*: resolves the brand, then renders the app shell. */
export function BrandShell() {
  const org = useShellOrg();
  const { brand: slug } = useParams();
  const brand = org.brands.find((item) => item.slug === slug);
  if (!brand) return <NotFound message="Cette marque n'existe pas dans cette organisation." />;
  return (
    // Keyed by brand: switching brands starts every screen from a clean state.
    <OrgProvider key={brand.id} org={org} brand={brand}>
      <Layout />
    </OrgProvider>
  );
}

function NoBrand(props: { org: Organization }) {
  const { org } = props;
  const auth = useAuth();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const { brand } = await api.post<{ brand: Brand }>(`/orgs/${org.org_id}/brands`, { name });
      await client.invalidateQueries({ queryKey: ["orgs"] });
      navigate(brandLink(org, brand.slug, "tableau-de-bord"), { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setPending(false);
    }
  }

  if (org.role !== "admin") {
    return (
      <Centered title="Aucune marque" body={`${org.name} n'a encore aucune marque. Un administrateur doit en créer une.`}>
        <Button className="mt-5 w-full" onClick={() => void auth.signOut()}>Se déconnecter</Button>
      </Centered>
    );
  }
  return (
    <Centered title="Première marque" body={`Chaque marque de ${org.name} a sa bibliothèque et ses adresses (un site par marché, par exemple).`}>
      <form className="mt-5 grid gap-4" onSubmit={(event) => void submit(event)}>
        <div>
          <FieldLabel htmlFor="brand-name">Nom de la marque</FieldLabel>
          <Input id="brand-name" required maxLength={120} placeholder="Louis XIII" value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        {error ? <p className="text-sm text-expired" role="alert">{error}</p> : null}
        <Button type="submit" variant="primary" loading={pending} disabled={!name.trim()}>{pending ? "Création…" : "Créer la marque"}</Button>
      </form>
    </Centered>
  );
}

export function NotFound(props: { message?: string }) {
  return (
    <Centered title="Page introuvable" body={props.message ?? "L'adresse ne correspond à aucune page."}>
      <Link to="/" className="mt-5 block text-sm underline">Revenir à l'accueil</Link>
    </Centered>
  );
}
