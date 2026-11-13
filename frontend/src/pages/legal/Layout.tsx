import type { ReactNode } from "react";
import { Link } from "react-router";

/** Date of the three legal texts: change it whenever one of them changes. */
export const UPDATED = "13 novembre 2026";
export const CONTACT = "contact@axelproject.fr";

const PAGES = [
  { to: "/legal", label: "Mentions légales" },
  { to: "/privacy", label: "Confidentialité" },
  { to: "/terms", label: "Conditions d'utilisation" },
];

export const Section = (props: { title: string; children: ReactNode }) => (
  <section className="mt-9">
    <h2 className="font-display text-xl">{props.title}</h2>
    <div className="mt-2 grid gap-3 text-sm leading-relaxed text-ink-soft">{props.children}</div>
  </section>
);

export const List = (props: { items: ReactNode[] }) => (
  <ul className="grid list-disc gap-1.5 pl-5">{props.items.map((item, index) => <li key={index}>{item}</li>)}</ul>
);

/** Frame of a public legal page: reachable without an account, linked from the foot of the interface and of the sign-in page. */
export function LegalLayout(props: { title: string; children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-[760px] items-baseline gap-3 px-6 py-5">
          <Link to="/" className="font-display text-[22px] leading-none">Nyra</Link>
          <a href="https://axelproject.fr" target="_blank" rel="noopener noreferrer" className="text-[10px] font-medium tracking-[0.18em] text-muted uppercase hover:text-ink">by Axel Project</a>
        </div>
      </header>
      <main className="mx-auto max-w-[760px] px-6 pt-10 pb-16">
        <h1 className="font-display text-4xl leading-tight">{props.title}</h1>
        <p className="mt-2 text-xs text-muted">Dernière mise à jour : {UPDATED}</p>
        {props.children}
        <nav aria-label="Pages légales" className="mt-12 flex flex-wrap gap-x-5 gap-y-1 border-t border-line pt-5 text-sm">
          {PAGES.map((page) => <Link key={page.to} to={page.to} className="text-muted underline hover:text-ink">{page.label}</Link>)}
        </nav>
      </main>
    </div>
  );
}
