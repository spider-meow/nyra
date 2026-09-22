import { createContext, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { ApiError, api, bindApiSession } from "./api";
import { Signup } from "./Signup";
import { btn, card, field, label } from "./ui";

export type AuthConfig = {
  required: boolean;
  supabaseUrl: string;
  anonKey: string;
};

type SessionInfo = {
  email: string;
  role: string;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<SessionInfo | null>(null);

export function useAuth(): SessionInfo {
  const value = useContext(AuthContext);
  if (!value) throw new Error("Session absente.");
  return value;
}

type Org = { org_id: string; name: string; role: string };

export function AuthGate(props: { children: ReactNode }) {
  const [phase, setPhase] = useState<"loading" | "locked" | "login" | "orgs" | "ready">("loading");
  const [notice, setNotice] = useState("");
  const [client, setClient] = useState<SupabaseClient | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [active, setActive] = useState<Org | null>(null);

  useEffect(() => {
    let stopped = false;
    void (async () => {
      try {
        const config = await api<AuthConfig>("/api/auth/config");
        if (stopped) return;
        if (!config.supabaseUrl || !config.anonKey) {
          setNotice(
            config.supabaseUrl
              ? "La clé publique Supabase manque. Ajoute SUPABASE_ANON_KEY dans le .env à la racine (clé anon ou publishable, Project Settings > API), puis relance nyra ui."
              : "Ce serveur n'est pas connecté à Supabase. Les pages du produit restent fermées.",
          );
          setPhase("locked");
          return;
        }
        const supabase = createClient(config.supabaseUrl, config.anonKey, {
          auth: { persistSession: true, autoRefreshToken: true },
        });
        const { data } = await supabase.auth.getSession();
        if (stopped) return;
        setClient(supabase);
        setSession(data.session);
        setPhase(data.session ? "orgs" : "login");
      } catch (error) {
        if (!stopped) {
          setNotice(error instanceof Error ? error.message : "La configuration d'authentification est illisible.");
          setPhase("locked");
        }
      }
    })();
    return () => {
      stopped = true;
    };
  }, []);

  useEffect(() => {
    if (!client) return;
    const { data } = client.auth.onAuthStateChange((event, next) => {
      if (event === "TOKEN_REFRESHED" && next) {
        setSession(next);
        return;
      }
      if (event === "SIGNED_OUT") {
        setSession(null);
        setActive(null);
        setPhase("login");
        return;
      }
      if (event === "SIGNED_IN" && next) {
        setSession(next);
        setActive(null);
        setPhase("orgs");
      }
    });
    return () => data.subscription.unsubscribe();
  }, [client]);

  useEffect(() => {
    if (phase !== "orgs" || !session || !client) return;
    let stopped = false;
    bindApiSession({
      orgId: "",
      token: async () => (await client.auth.getSession()).data.session?.access_token ?? null,
    });
    void api<{ organizations: Org[] }>("/api/orgs")
      .then((data) => {
        if (stopped) return;
        const list = data.organizations;
        setOrgs(list);
        if (list.length === 1) {
          choose(list[0], client);
        } else {
          setNotice(list.length === 0 ? "Ce compte n'appartient à aucune organisation." : "");
        }
      })
      .catch((error: unknown) => {
        if (!stopped) setNotice(error instanceof ApiError ? error.message : "La liste des organisations a échoué.");
      });
    return () => {
      stopped = true;
    };
  }, [phase, session, client]);

  function choose(org: Org, supabase: SupabaseClient) {
    bindApiSession({
      orgId: org.org_id,
      token: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
    });
    setActive(org);
    setNotice("");
    setPhase("ready");
  }

  async function signOut() {
    bindApiSession(null);
    setActive(null);
    setOrgs([]);
    await client?.auth.signOut();
    setPhase("login");
  }

  if (phase === "loading") {
    return <Gate title="Nyra" body="Vérification de la session." />;
  }
  if (phase === "locked") {
    return <Gate title="Connexion requise" body={notice} />;
  }
  if (phase === "login" || !session || !client) {
    if (window.location.pathname === "/signup") {
      return (
        <Gate title="Créer un compte" body="Le compte est rattaché à une organisation. L'adresse se normalise toute seule.">
          <Signup client={client} />
        </Gate>
      );
    }
    return <Login client={client} notice={notice} onError={setNotice} />;
  }
  if (phase !== "ready" || !active) {
    return (
      <Gate title={orgs.length === 0 ? "Aucune organisation" : "Organisations"} body={notice || "Choisis l'organisation à ouvrir."}>
        <div className="mt-4 grid gap-2">
          {orgs.map((org) => (
            <button key={org.org_id} type="button" className={btn} onClick={() => choose(org, client)}>
              {org.name}
            </button>
          ))}
          <button type="button" className="text-sm text-muted" onClick={() => void signOut()}>
            Se déconnecter
          </button>
        </div>
      </Gate>
    );
  }

  return (
    <AuthContext.Provider value={{ email: session.user.email ?? "", role: active.role, signOut }}>
      {props.children}
    </AuthContext.Provider>
  );
}

function Login(props: { client: SupabaseClient | null; notice: string; onError: (message: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!props.client) return;
    setPending(true);
    props.onError("");
    const { error } = await props.client.auth.signInWithPassword({ email, password });
    setPending(false);
    if (error) props.onError(error.message);
  }

  return (
    <Gate title="Connexion" body="Références, exploration et correspondances s'ouvrent après la connexion.">
      <form className="mt-4 grid gap-3" onSubmit={(event) => void submit(event)}>
        <label>
          <span className={label}>Email</span>
          <input className={field} type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label>
          <span className={label}>Mot de passe</span>
          <input className={field} type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {props.notice ? <p className="text-sm font-medium text-ember">{props.notice}</p> : null}
        <button type="submit" className={btn} disabled={pending || !props.client}>
          {pending ? "Connexion…" : "Se connecter"}
        </button>
        <a className="text-sm text-muted" href="/signup">Créer un compte</a>
      </form>
    </Gate>
  );
}

function Gate(props: { title: string; body: string; children?: ReactNode }) {
  useEffect(() => {
    document.title = props.title === "Nyra" ? "Nyra" : `${props.title} — Nyra`;
  }, [props.title]);

  return (
    <div className="grid min-h-screen place-items-center bg-canvas px-4">
      <section className={`${card} w-full max-w-md`}>
        <p className="text-sm font-medium">Nyra</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">{props.title}</h1>
        <p className="mt-2 text-sm text-muted">{props.body}</p>
        {props.children}
      </section>
    </div>
  );
}
