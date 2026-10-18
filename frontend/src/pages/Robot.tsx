import { Card } from "../components/ui";

const SECTIONS: { title: string; lines: string[] }[] = [
  {
    title: "Qui est-ce ?",
    lines: [
      "NyraBot est le robot de Nyra, un outil de suivi des droits d'usage des visuels d'une marque. Il est exploité par Axel Project, à la demande de la marque dont il lit le site.",
      "Il vérifie où les visuels de la marque, dont les droits peuvent expirer, apparaissent encore en ligne sur ses propres sites.",
    ],
  },
  {
    title: "Ce qu'il fait",
    lines: [
      "Il ouvre les pages publiques d'un site comme un navigateur le ferait, puis repère les images qu'elles affichent. Il ne se connecte à aucun compte et ne remplit aucun formulaire ; il ferme seulement les bandeaux de cookies et les questions d'âge pour voir la page.",
      "Il garde une copie des images d'au moins 200 pixels de côté, pour les comparer aux visuels de la marque. Ces copies restent privées, réservées à l'équipe de la marque.",
    ],
  },
  {
    title: "Ce qu'il respecte",
    lines: [
      "Le fichier robots.txt du site. Il lit trois pages à la fois au plus, avec une à deux secondes d'attente entre deux pages.",
      "Une lecture n'a lieu que lorsque l'équipe de la marque la lance. Chaque requête porte la signature NyraBot, avec l'adresse de cette page et une adresse de contact.",
    ],
  },
  {
    title: "Le bloquer ou nous écrire",
    lines: [
      "Pour l'exclure d'un site, ajoutez à son robots.txt : User-agent: NyraBot, puis Disallow: /. Il s'y conforme dès sa prochaine lecture.",
      "Pour une question, ou s'il se comporte mal sur votre site, écrivez à l'adresse de contact qui figure dans sa signature (le champ User-Agent, dans vos journaux d'accès).",
    ],
  },
];

/** Public page the robot's signature points to: what it is, what it does, how to block it. Linked from nowhere else. */
export function Robot() {
  return (
    <div className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-[640px]">
        <div className="mb-8 flex flex-col items-center gap-1.5">
          <p className="font-display text-[40px] leading-none">Nyra</p>
          <a href="https://axelproject.fr" target="_blank" rel="noopener noreferrer" className="text-[11px] font-medium tracking-[0.18em] text-muted uppercase underline-offset-4 transition-colors hover:text-ink hover:underline">
            by Axel Project
          </a>
        </div>
        <Card className="p-8 shadow-float">
          <h1 className="font-display text-[34px] leading-none">Le robot Nyra</h1>
          {SECTIONS.map((section) => (
            <section key={section.title} className="mt-6">
              <h2 className="text-sm font-semibold">{section.title}</h2>
              {section.lines.map((line) => (
                <p key={line} className="mt-2 text-sm leading-relaxed text-muted">{line}</p>
              ))}
            </section>
          ))}
        </Card>
      </div>
    </div>
  );
}
