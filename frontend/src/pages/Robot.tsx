import { useState, type ReactNode } from "react";
import { Button } from "../components/ui";

// What the robot signs its requests with: keep in step with `crawl.user_agent` in config.yaml.
const CONTACT = "contact@axelproject.fr";
const USER_AGENT = `NyraBot/0.1 (+https://github.com/spider-meow/nyra; contact: ${CONTACT})`;
const ROBOTS_TXT = "User-agent: NyraBot\nDisallow: /";
const IP_ADDRESS = "188.166.17.130";

type Copy = "idle" | "copied" | "failed";

function Row(props: { name: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-t border-line py-3 text-sm leading-relaxed sm:grid-cols-[170px_minmax(0,1fr)] sm:gap-4">
      <dt className="text-muted">{props.name}</dt>
      <dd>{props.children}</dd>
    </div>
  );
}

function CopyButton(props: { text: string }) {
  const [state, setState] = useState<Copy>("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(props.text);
      setState("copied");
    } catch {
      setState("failed"); // the page was opened where the clipboard is blocked: the text stays there to select
    }
    window.setTimeout(() => setState("idle"), 2000);
  }
  return <Button size="sm" onClick={() => void copy()}>{state === "copied" ? "Copié" : state === "failed" ? "Copie impossible" : "Copier"}</Button>;
}

const Section = (props: { title: string; children: ReactNode }) => (
  <section className="mt-10">
    <h2 className="text-xs font-medium tracking-[0.06em] text-muted uppercase">{props.title}</h2>
    {props.children}
  </section>
);

/** Public page the robot's signature points to: what it is, what it does, how to block it. Linked from nowhere else. */
export function Robot() {
  return (
    <div className="min-h-screen">
      <header className="bg-ink text-canvas">
        <div className="mx-auto max-w-[760px] px-6 pt-5 pb-12">
          <div className="flex items-baseline gap-3">
            <span className="font-display text-[22px] leading-none">Nyra</span>
            <a href="https://axelproject.fr" target="_blank" rel="noopener noreferrer" className="text-[10px] font-medium tracking-[0.18em] text-canvas/60 uppercase hover:text-canvas">by Axel Project</a>
          </div>
          <h1 className="mt-12 font-display text-[56px] leading-none">NyraBot</h1>
          <p className="mt-4 max-w-[520px] text-base leading-relaxed text-canvas/80">Le robot qui lit des pages publiques pour une marque, afin de retrouver où ses visuels sont encore en ligne.</p>
        </div>
      </header>
      <main className="mx-auto max-w-[760px] px-6 pt-2 pb-16">
        <Section title="Fiche technique">
          <dl className="mt-2">
            <Row name="User-agent"><span className="font-mono text-[13px] break-words">{USER_AGENT}</span></Row>
            <Row name="Nom dans robots.txt"><span className="font-mono text-[13px]">NyraBot</span></Row>
            <Row name="Respecte robots.txt"><span className="rounded-md bg-ok-soft px-2 py-0.5 text-xs font-medium text-ok">Oui</span></Row>
            <Row name="Respecte Crawl-delay">Oui, jusqu'à 10 secondes. Quand un délai est demandé, il lit une seule page à la fois.</Row>
            <Row name="Cadence">3 pages à la fois au plus, chacune suivie d'une attente de 1 à 2 secondes.</Row>
            <Row name="Adresse IP"><span className="font-mono text-[13px]">{IP_ADDRESS}</span></Row>
            <Row name="Comportement">Aucun compte, aucun formulaire. Il ferme seulement les bandeaux de cookies et les questions d'âge pour voir la page.</Row>
            <Row name="Données gardées">Une copie des images d'au moins 200 pixels de côté, privée, réservée à l'équipe de la marque.</Row>
            <Row name="Exploité par">Axel Project, pour le compte de la marque dont il lit le site.</Row>
            <Row name="Contact"><a href={`mailto:${CONTACT}`} className="underline">{CONTACT}</a></Row>
          </dl>
        </Section>
        <Section title="Le bloquer">
          <p className="mt-2 text-sm leading-relaxed text-muted">Ajoutez ces deux lignes au robots.txt du site. Il s'y conforme dès sa prochaine lecture.</p>
          <div className="mt-3 flex items-start gap-3">
            <pre className="min-w-0 flex-1 rounded-lg bg-ink px-4 py-3 font-mono text-[13px] leading-relaxed text-canvas">{ROBOTS_TXT}</pre>
            <CopyButton text={ROBOTS_TXT} />
          </div>
        </Section>
        <Section title="Une précision">
          <p className="mt-2 text-sm leading-relaxed text-muted">NyraBot affiche les pages comme un navigateur : pour une même page, il demande plusieurs fichiers à la suite (scripts, styles), puis télécharge les images qu'il y trouve. Ces requêtes comptent pour une seule page dans la cadence ci-dessus.</p>
        </Section>
      </main>
    </div>
  );
}
