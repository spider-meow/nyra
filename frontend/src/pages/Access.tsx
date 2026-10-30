import { useQueryClient } from "@tanstack/react-query";
import { Suspense, createContext, lazy, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, Outlet, useLocation, useNavigate, useParams, useRouteError } from "react-router";
import { Layout } from "../components/Layout";
import { Button, Card, Checkbox, FieldLabel, Input, PasswordInput, Spinner } from "../components/ui";
import { ApiError, api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { returnPath } from "../lib/paths";
import { OrgProvider, brandLink, useMe, useOrganizations, useStaffOpenedOrganization } from "../lib/org";
import { useStaffOrgs } from "../lib/staff";
import { setViewAs } from "../lib/viewAs";
import type { Brand, Organization } from "../types";

const PasswordStrength = lazy(() => import("../components/PasswordStrength"));

function Centered(props: { title: string; body?: ReactNode; children?: ReactNode }) {
  return (
    <div className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex flex-col items-center gap-1.5">
          <p className="font-display text-[40px] leading-none">Nyra</p>
          <a href="https://axelproject.fr" target="_blank" rel="noopener noreferrer" className="text-[11px] font-medium tracking-[0.18em] text-muted uppercase underline-offset-4 transition-colors hover:text-ink hover:underline">
            by Axel Project
          </a>
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

/** Everything behind a session. Signed-out visitors go to /login. */
export function RequireSession() {
  const auth = useAuth();
  const location = useLocation();
  if (auth.phase === "loading") return <Centered title="Un instant"><div className="mt-4"><Spinner label="Vérification de la session…" /></div></Centered>;
  if (auth.phase === "unconfigured") return <Centered title="Service indisponible" body={auth.problem} />;
  // The page asked for goes with the visitor to /login, which brings them back to it once signed in (a shared link to a collection or a visual).
  if (auth.phase === "signed-out") return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  if (auth.mustSetPassword) return <Navigate to="/set-password" replace />;
  return <Outlet />;
}

export function Login() {
  const auth = useAuth();
  const from = returnPath((useLocation().state as { from?: unknown } | null)?.from);
  const [mode, setMode] = useState<"password" | "reset" | "sent">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  if (auth.phase === "signed-in") return <Navigate to={auth.mustSetPassword ? "/set-password" : from ?? "/"} replace />;
  if (auth.phase === "unconfigured") return <Centered title="Service indisponible" body={auth.problem} />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      if (mode === "password") {
        await auth.signIn(email, password, remember);
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
      <Centered title="Vérifiez votre messagerie" body={`Si un compte existe pour ${email}, un lien pour choisir un nouveau mot de passe vient d'être envoyé. Pensez à regarder dans vos courriers indésirables.`}>
        <Button className="mt-5 w-full" onClick={() => setMode("password")}>Retour à la connexion</Button>
      </Centered>
    );
  }

  return (
    <Centered
      title={mode === "password" ? "Connexion" : "Mot de passe oublié"}
      body={mode === "password" ? "L'accès se fait sur invitation de votre administrateur." : "Nous vous envoyons un lien pour en choisir un nouveau."}
    >
      <LoginForm
        mode={mode}
        email={email}
        password={password}
        remember={remember}
        error={error}
        pending={pending}
        disabled={pending || auth.phase === "loading"}
        onEmail={setEmail}
        onPassword={setPassword}
        onRemember={setRemember}
        onSubmit={(event) => void submit(event)}
        onToggle={() => {
          setError("");
          setMode(mode === "password" ? "reset" : "password");
        }}
      />
    </Centered>
  );
}

type LoginFormProps = {
  mode: "password" | "reset";
  email: string;
  password: string;
  remember: boolean;
  error: string;
  pending: boolean;
  disabled: boolean;
  onEmail: (value: string) => void;
  onPassword: (value: string) => void;
  onRemember: (value: boolean) => void;
  onSubmit: (event: FormEvent) => void;
  onToggle: () => void;
};

/** The e-mail (and password) fields, the submit button and the link that switches between sign-in and reset. */
function LoginForm(props: LoginFormProps) {
  const { mode } = props;
  return (
    <form className="mt-6 grid gap-4" onSubmit={props.onSubmit}>
      <div>
        <FieldLabel htmlFor="email">Adresse e-mail</FieldLabel>
        <Input id="email" type="email" autoComplete="username" required value={props.email} onChange={(event) => props.onEmail(event.target.value)} />
      </div>
      {mode === "password" ? (
        <div>
          <FieldLabel htmlFor="password">Mot de passe</FieldLabel>
          <PasswordInput id="password" autoComplete="current-password" required value={props.password} onChange={(event) => props.onPassword(event.target.value)} />
        </div>
      ) : null}
      {mode === "password" ? <Checkbox checked={props.remember} onChange={props.onRemember} label="Rester connecté" /> : null}
      {props.error ? <p className="text-sm text-expired" role="alert">{props.error}</p> : null}
      <Button type="submit" variant="primary" size="lg" disabled={props.disabled}>
        {props.pending ? "Un instant…" : mode === "password" ? "Se connecter" : "Envoyer le lien"}
      </Button>
      <button type="button" className="text-sm text-muted underline-offset-2 hover:text-ink hover:underline" onClick={props.onToggle}>
        {mode === "password" ? "Mot de passe oublié ?" : "Retour à la connexion"}
      </button>
    </form>
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
        <Button variant="primary" className="mt-5 w-full" onClick={() => navigate("/login")}>Demander un nouveau lien</Button>
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
          <FieldLabel htmlFor="new-password" hint={password.length < 10 ? `${password.length}/10 caractères minimum` : undefined}>Nouveau mot de passe</FieldLabel>
          <PasswordInput id="new-password" autoComplete="new-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          <Suspense fallback={null}>
            <PasswordStrength password={password} userInputs={auth.email.split(/[@.]/)} />
          </Suspense>
        </div>
        <div>
          <FieldLabel htmlFor="confirm-password" hint={confirm && password !== confirm ? "ne correspond pas" : undefined}>Confirmation</FieldLabel>
          <PasswordInput id="confirm-password" autoComplete="new-password" required value={confirm} onChange={(event) => setConfirm(event.target.value)} />
        </div>
        {error ? <p className="text-sm text-expired" role="alert">{error}</p> : null}
        <Button type="submit" variant="primary" size="lg" disabled={pending}>{pending ? "Enregistrement…" : "Enregistrer et continuer"}</Button>
      </form>
    </Centered>
  );
}

/** "/" → the only organization, or a picker when there are several; a platform admin gets every client. */
export function OrgHome() {
  const orgs = useOrganizations();
  const auth = useAuth();
  const me = useMe();
  const staff = Boolean(me.data?.staff);
  const clients = useStaffOrgs(staff);
  if (orgs.isLoading || me.isLoading || clients.isLoading) return <Centered title="Un instant"><div className="mt-4"><Spinner label="Chargement des organisations…" /></div></Centered>;
  const failure = orgs.error ?? clients.error;
  if (failure) return <Centered title="Erreur" body={errorMessage(failure)} />;
  const list = orgs.data ?? [];
  if (list.length === 1 && !staff) return <Navigate to={`/o/${list[0].slug}`} replace />;
  if (list.length === 0 && !staff) {
    return (
      <Centered title="Aucune organisation" body={`Le compte ${auth.email} n'est rattaché à aucune organisation. Demandez à votre administrateur de vous inviter.`}>
        <Button className="mt-5 w-full" onClick={() => void auth.signOut()}>Se déconnecter</Button>
      </Centered>
    );
  }
  const choices = staff
    ? (clients.data?.organizations ?? []).map((org) => ({ key: org.id, name: org.name, slug: org.slug, note: "Espace client, en tant qu'équipe Nyra" }))
    : list.map((org) => ({ key: org.org_id, name: org.name, slug: org.slug, note: org.role === "admin" ? "Administrateur" : "Lecture et validation" }));
  return (
    <Centered title="Organisations" body="Choisissez l'espace à ouvrir.">
      <div className="mt-5 grid gap-2">
        {choices.map((org) => (
          <Link key={org.key} to={`/o/${org.slug}`} className="rounded-xl border border-ink bg-paper px-4 py-3 text-sm font-medium hover:bg-yellow">
            {org.name}
            <span className="block text-xs font-normal text-muted">{org.note}</span>
          </Link>
        ))}
        {staff ? (
          <Link to="/back-office" className="rounded-xl border border-dashed border-ink bg-paper px-4 py-3 text-sm font-medium hover:bg-yellow">
            Back office Nyra
            <span className="block text-xs font-normal text-muted">Toutes les marques, la file de tâches, les performances</span>
          </Link>
        ) : null}
      </div>
    </Centered>
  );
}

const ShellOrg = createContext<Organization | null>(null);

export function useShellOrg(): Organization {
  const org = useContext(ShellOrg);
  if (!org) throw new Error("OrgShell manquant.");
  return org;
}

/** /o/:slug/*: resolves the organization; the brand is resolved one level down. */
export function OrgShell() {
  const { slug } = useParams();
  const orgs = useOrganizations();
  const me = useMe();
  const member = orgs.data?.find((item) => item.slug === slug);
  // Not one of the caller's organizations: a platform admin still opens it (they are in no client's team).
  const opened = useStaffOpenedOrganization(slug, !orgs.isLoading && !member && Boolean(me.data?.staff));
  // The role a platform admin chose (AXE-634) lasts while they stay in this organization, brands included, and goes when they leave it.
  const orgId = member?.org_id ?? opened.data?.org_id;
  useEffect(() => (orgId ? () => setViewAs(null) : undefined), [orgId]);
  if (orgs.isLoading || (!member && (me.isLoading || opened.isLoading))) return <Centered title="Un instant"><div className="mt-4"><Spinner /></div></Centered>;
  const failure = orgs.error ?? (member ? null : me.error ?? (opened.error instanceof ApiError && opened.error.status === 404 ? null : opened.error));
  if (failure) return <Centered title="Erreur" body={errorMessage(failure)} />;
  const org = member ?? opened.data;
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

/** /o/:slug: the dashboard of the organization's first brand (links from before brands: Legacy.tsx). */
export function FirstBrand() {
  const org = useShellOrg();
  const first = org.brands[0];
  if (!first) return <NoBrand org={org} />;
  return <Navigate to={brandLink(org, first.slug, "dashboard")} replace />;
}

/** /o/:slug/b/:brand/*: resolves the brand, then renders the app shell. */
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
  const staff = Boolean(useMe().data?.staff);
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
      navigate(brandLink(org, brand.slug, "dashboard"), { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setPending(false);
    }
  }

  if (!staff) {
    return (
      <Centered title="Aucune marque" body={`${org.name} n'a encore aucune marque. Contactez l'équipe Nyra pour en créer une.`}>
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

/** The router's errorElement: a page crashed. Plain words first, the technical detail folded away. */
export function ErrorScreen() {
  const error = useRouteError();
  const detail = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return (
    <Centered title="Quelque chose s'est mal passé" body="Cette page n'a pas pu s'afficher. Vos données ne sont pas touchées : rechargez la page, et si le problème revient, prévenez l'équipe Nyra.">
      <div className="mt-6 flex items-center gap-3">
        <Button variant="primary" onClick={() => window.location.reload()}>Recharger la page</Button>
        <a href="/" className="text-sm underline">Revenir à l'accueil</a>
      </div>
      {detail ? (
        <details className="mt-6 text-xs text-muted">
          <summary className="cursor-pointer select-none">Détail technique</summary>
          <p className="mt-2 font-mono break-words">{detail}</p>
        </details>
      ) : null}
    </Centered>
  );
}
