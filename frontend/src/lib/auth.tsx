import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, bindSession } from "./api";

type Phase = "loading" | "unconfigured" | "signed-out" | "signed-in";

type AuthState = {
  phase: Phase;
  session: Session | null;
  email: string;
  /** True after arriving from an invitation or a password-reset link. */
  mustSetPassword: boolean;
  problem: string;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  requestReset: (email: string) => Promise<void>;
  setPassword: (password: string) => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

// Supabase puts `type=invite` / `type=recovery` in the URL fragment of the
// links it e-mails. Read it before the client consumes the fragment.
const arrivedToSetPassword = /type=(invite|recovery)/.test(window.location.hash);

export function AuthProvider(props: { children: ReactNode }) {
  const [client, setClient] = useState<SupabaseClient | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [mustSetPassword, setMustSetPassword] = useState(arrivedToSetPassword);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let stopped = false;
    void (async () => {
      try {
        const config = await api.get<{ supabaseUrl: string; anonKey: string }>("/auth/config");
        if (stopped) return;
        if (!config.supabaseUrl || !config.anonKey) {
          setProblem("Le serveur n'a pas de clé publique Supabase (SUPABASE_ANON_KEY). Voir .env.example.");
          setPhase("unconfigured");
          return;
        }
        const supabase = createClient(config.supabaseUrl, config.anonKey, {
          auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
        });
        // Bind before any render can fire a request: child effects run before
        // this provider's own effects, so binding in an effect would be too late.
        bindSession(
          async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
          () => void supabase.auth.signOut(),
        );
        const { data } = await supabase.auth.getSession();
        if (stopped) return;
        setClient(supabase);
        setSession(data.session);
        setPhase(data.session ? "signed-in" : "signed-out");
      } catch (error) {
        if (!stopped) {
          setProblem(error instanceof Error ? error.message : "Configuration illisible.");
          setPhase("unconfigured");
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
      if (event === "PASSWORD_RECOVERY") setMustSetPassword(true);
      setSession(next);
      setPhase(next ? "signed-in" : "signed-out");
    });
    return () => data.subscription.unsubscribe();
  }, [client]);

  const value = useMemo<AuthState>(
    () => ({
      phase,
      session,
      email: session?.user.email ?? "",
      mustSetPassword,
      problem,
      async signIn(email, password) {
        if (!client) return;
        const { error } = await client.auth.signInWithPassword({ email, password });
        if (error) throw new Error(error.message === "Invalid login credentials" ? "Email ou mot de passe incorrect." : error.message);
      },
      async signOut() {
        await client?.auth.signOut();
      },
      async requestReset(email) {
        if (!client) return;
        const { error } = await client.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}/mot-de-passe`,
        });
        if (error) throw new Error(error.message);
      },
      async setPassword(password) {
        if (!client) return;
        const { error } = await client.auth.updateUser({ password });
        if (error) throw new Error(error.message);
        setMustSetPassword(false);
      },
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
