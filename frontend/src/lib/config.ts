import { api } from "./api";

export type SentryConfig = { dsn: string; environment: string; release: string; tracesSampleRate: number };

export type PublicConfig = { supabaseUrl: string; anonKey: string; sentry: SentryConfig | null };

// The page reads its public configuration once: sign-in and monitoring share the answer.
let request: Promise<PublicConfig> | null = null;

export function loadConfig(): Promise<PublicConfig> {
  request ??= api.get<PublicConfig>("/auth/config");
  return request;
}
