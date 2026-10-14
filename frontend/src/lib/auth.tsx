import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { bindSession } from "./api";
import { loadConfig } from "./config";
import { clearStored } from "./storage";

type Phase = "loading" | "unconfigured" | "signed-out" | "signed-in";

type AuthState = {
  phase: Phase;
  session: Session | null;
  email: string;
  /** True after arriving from an invitation or a password-reset link. */
  mustSetPassword: boolean;
  problem: string;
  signIn: (email: string, password: string, remember: boolean) => Promise<void>;
  signOut: () => Promise<void>;
  requestReset: (email: string) => Promise<void>;
  setPassword: (password: string) => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

// Supabase puts `type=invite` / `type=recovery` in the URL fragment of the
// links it e-mails. Read it before the client consumes the fragment.
const arrivedToSetPassword = /type=(invite|recovery)/.test(window.location.hash);

/**
 * "Rester connecté": the session lives in localStorage (survives closing the
 * browser) or, when unchecked, in sessionStorage (ends with the tab). The
 * choice is made at sign-in; Supabase's token refreshes follow it.
 */
const REMEMBER_KEY = "nyra-remember";
const remembered = () => window.localStorage.getItem(REMEMBER_KEY) !== "0";
const sessionStore: Pick<Storage, "getItem" | "setItem" | "removeItem"> = {
  getItem: (key) => window.sessionStorage.getItem(key) ?? window.localStorage.getItem(key),
  setItem(key, value) {
    const [keep, drop] = remembered() ? [window.localStorage, window.sessionStorage] : [window.sessionStorage, window.localStorage];
    keep.setItem(key, value);
    drop.removeItem(key);
  },
  removeItem(key) {
    window.sessionStorage.removeItem(key);
    window.localStorage.removeItem(key);
  },
};

type Booted ={ client: SupabaseClient; session: Session | null } | { problem: string };

/**
 * Reads the public config, creates the Supabase client and restores the session.
 * `null`: the provider went away midway, nothing to apply.
 */
async function connect(stopped: () => boolean): Promise<Booted | null> {
  const config = await loadConfig();
  if (stopped()) return null;
  if (!config.supabaseUrl || !config.anonKey) {
    return { problem: "Le serveur n'a pas de clé publique Supabase (SUPABASE_ANON_KEY). Voir .env.example." };
  }
  const supabase = createClient(config.supabaseUrl, config.anonKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, storage: sessionStore },
  });
  // Bind before any render can fire a request: child effects run before
  // this provider's own effects, so binding in an effect would be too late.
  bindSession(
    async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
    () => void supabase.auth.signOut(),
  );
  const { data } = await supabase.auth.getSession();
  if (stopped()) return null;
  return { client: supabase, session: data.session };
}

/** `connect`, with a failure turned into the problem to show. */
async function bootstrap(stopped: () => boolean): Promise<Booted | null> {
  try {
    return await connect(stopped);
  } catch (error) {
    return stopped() ? null : { problem: error instanceof Error ? error.message : "Configuration illisible." };
  }
}

/** Supabase answers in English; these are the cases a person can run into. */
const AUTH_ERRORS: [RegExp, string][] = [
  [/Invalid login credentials/i, "Email ou mot de passe incorrect."],
  [/after \d+ seconds|rate limit/i, "Trop de demandes. Patientez une minute avant de réessayer."],
  [/different from the old/i, "Choisissez un mot de passe différent de l'ancien."],
];

function authError(message: string): Error {
  return new Error(AUTH_ERRORS.find(([pattern]) => pattern.test(message))?.[1] ?? message);
}

type Actions = Pick<AuthState, "signIn" | "signOut" | "requestReset" | "setPassword">;

function authActions(client: SupabaseClient | null, onPasswordSet: () => void): Actions {
  return {
    async signIn(email, password, remember) {
      if (!client) return;
      window.localStorage.setItem(REMEMBER_KEY, remember ? "1" : "0");
      const { error } = await client.auth.signInWithPassword({ email, password });
      if (error) throw authError(error.message);
    },
    async signOut() {
      await client?.auth.signOut();
    },
    async requestReset(email) {
      if (!client) return;
      const { error } = await client.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/set-password`,
      });
      if (error) throw authError(error.message);
    },
    async setPassword(password) {
      if (!client) return;
      const { error } = await client.auth.updateUser({ password });
      if (error) throw authError(error.message);
      onPasswordSet();
    },
  };
}

export function AuthProvider(props: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [client, setClient] = useState<SupabaseClient | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [mustSetPassword, setMustSetPassword] = useState(arrivedToSetPassword);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let stopped = false;
    void bootstrap(() => stopped).then((booted) => {
      if (!booted) return;
      if ("problem" in booted) {
        setProblem(booted.problem);
        setPhase("unconfigured");
        return;
      }
      setClient(booted.client);
      setSession(booted.session);
      setPhase(booted.session ? "signed-in" : "signed-out");
    });
    return () => {
      stopped = true;
    };
  }, []);

  useEffect(() => {
    if (!client) return;
    const { data } = client.auth.onAuthStateChange((event, next) => {
      if (event === "PASSWORD_RECOVERY") setMustSetPassword(true);
      // Signed out (button, expired session, another tab): forget this browser's saved preferences and list copies.
      if (!next) {
        clearStored();
        queryClient.clear(); // the next person to sign in on this page must not see this one's lists
      }
      setSession(next);
      setPhase(next ? "signed-in" : "signed-out");
    });
    return () => data.subscription.unsubscribe();
  }, [client, queryClient]);

  const value = useMemo<AuthState>(
    () => ({
      phase,
      session,
      email: session?.user.email ?? "",
      mustSetPassword,
      problem,
      ...authActions(client, () => setMustSetPassword(false)),
    }),
    [client, phase, session, mustSetPassword, problem],
  );

  return <AuthContext.Provider value={value}>{props.children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider manquant.");
  return value;
}
