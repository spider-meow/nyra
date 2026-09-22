import { useState, type FormEvent } from "react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ApiError, api } from "./api";
import { slugify, slugifyDraft } from "./slug";
import { btn, field, label } from "./ui";

type Props = {
  client: SupabaseClient | null;
};

export function Signup(props: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const canonical = slugify(slugEdited ? slug : name);

  function onName(value: string) {
    setName(value);
    if (!slugEdited) setSlug(slugify(value));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!props.client) return;
    if (!canonical) {
      setNotice("Le nom doit contenir une lettre ou un chiffre.");
      return;
    }
    setPending(true);
    setNotice("");
    try {
      await api("/api/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          organization_name: name.trim(),
          slug: canonical,
        }),
      });
      const { error } = await props.client.auth.signInWithPassword({ email, password });
      if (error) throw new ApiError(error.message);
      window.history.pushState({}, "", "/");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "La requête a échoué.");
      setPending(false);
    }
  }

  return (
    <form className="mt-4 grid gap-3" onSubmit={(event) => void submit(event)}>
      <label>
        <span className={label}>Email</span>
        <input className={field} type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
      </label>
      <label>
        <span className={label}>Mot de passe</span>
        <input className={field} type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} />
      </label>
      <label>
        <span className={label}>Organisation</span>
        <input className={field} required value={name} placeholder="Rémy Martin" onChange={(event) => onName(event.target.value)} />
      </label>
      <label>
        <span className={label}>Adresse</span>
        <input
          className={field}
          required
          value={slugEdited ? slug : canonical}
          placeholder="remy-martin"
          onChange={(event) => {
            setSlugEdited(true);
            setSlug(slugifyDraft(event.target.value));
          }}
        />
      </label>
      {notice ? <p className="text-sm font-medium text-ember">{notice}</p> : null}
      <button type="submit" className={btn} disabled={pending || !props.client}>
        {pending ? "Création…" : "Créer le compte"}
      </button>
      <a className="text-sm text-muted" href="/">Déjà un compte</a>
    </form>
  );
}
