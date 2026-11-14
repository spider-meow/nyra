import { Navigate } from "react-router";
import { useAuth } from "../lib/auth";
import { LinkButton } from "../components/ui";

const STEPS = [
  { title: "Importez vos visuels", body: "Chaque image de référence est enregistrée avec sa date d'expiration de droits." },
  { title: "Nyra lit vos sites", body: "Le robot parcourt vos pages comme un visiteur et reconnaît vos images, même recadrées, retournées ou recompressées." },
  { title: "Agissez à temps", body: "Les images sont classées par urgence : expirées, bientôt expirées, à surveiller." },
];

/** Public home page (signed-out visitors). Signed-in ones go straight to their spaces. */
export function Home() {
  const auth = useAuth();
  if (auth.phase === "signed-in") return <Navigate to="/app" replace />;
  return (
    <div className="min-h-screen bg-canvas text-ink">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-5">
        <span className="font-display text-2xl">Nyra</span>
        <LinkButton to="/login" size="sm">Se connecter</LinkButton>
      </header>
      <main className="mx-auto max-w-5xl px-4">
        <section className="py-16 text-center sm:py-24">
          <h1 className="font-display text-4xl leading-tight sm:text-6xl">
            Vos images sous licence, <span className="bg-yellow px-2">sous contrôle</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-ink-soft">
            Nyra repère les images dont les droits ont expiré ou vont expirer sur vos sites, et vous dit lesquelles retirer en premier.
          </p>
          <LinkButton to="/login" variant="primary" className="mt-8">Accéder à mon espace</LinkButton>
        </section>
        <section aria-label="Comment ça marche" className="grid gap-4 pb-20 sm:grid-cols-3">
          {STEPS.map((step, index) => (
            <div key={step.title} className="rounded-2xl border border-ink bg-paper p-5">
              <p className="font-mono text-sm text-muted">0{index + 1}</p>
              <h2 className="mt-2 font-display text-xl">{step.title}</h2>
              <p className="mt-2 text-sm text-ink-soft">{step.body}</p>
            </div>
          ))}
        </section>
      </main>
      <footer className="border-t border-line py-6 text-center text-sm text-muted">Nyra</footer>
    </div>
  );
}
