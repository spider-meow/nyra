import { Icon } from "../../components/icons";
import { Button, Card, LinkButton, PageHeader, cx } from "../../components/ui";
import { useOrg } from "../../lib/org";

/** The three first steps, shown until there is a library and a first reading. */
export function Start(props: { noLibrary: boolean; neverCrawled: boolean }) {
  const { link, admin } = useOrg();
  const { noLibrary, neverCrawled } = props;
  return (
    <>
      <PageHeader title="Tableau de bord" description="Les visuels dont les droits expirent, et ceux qui sont encore en ligne." />
      <Card>
        <h2 className="font-display text-3xl">Mise en route</h2>
        <p className="mt-1 text-sm text-muted">Trois étapes pour savoir quels visuels retirer du site.</p>
        <ol className="mt-5 grid gap-4 text-[15px]">
          <Step done={!noLibrary} index={1} title="Déposer les visuels sous droits et leur date d'expiration" to={link("bibliotheque")} cta="Ouvrir la bibliothèque" />
          <Step done={!neverCrawled} index={2} title="Ajouter les sites sur lesquels ils pourraient encore apparaître, puis les lire" to={link("lectures")} cta="Sites et lectures" disabled={noLibrary || !admin} />
          <Step done={false} index={3} title="Traiter les correspondances trouvées" to={link("a-traiter")} cta="Voir les correspondances" disabled={neverCrawled} />
        </ol>
      </Card>
    </>
  );
}

function Step(props: { index: number; title: string; done: boolean; to: string; cta: string; disabled?: boolean }) {
  return (
    <li className="flex flex-wrap items-center gap-3">
      <span
        className={cx(
          "grid h-8 w-8 shrink-0 place-items-center rounded-full border border-ink text-xs font-semibold",
          props.done ? "bg-mint text-ink" : "bg-yellow text-ink",
        )}
        aria-label={props.done ? "Fait" : "À faire"}
      >
        {props.done ? <Icon name="check" size={14} strokeWidth={2.2} /> : props.index}
      </span>
      <span className={cx("min-w-0 flex-1", props.done && "text-muted line-through decoration-1")}>{props.title}</span>
      {!props.done ? (
        props.disabled ? (
          <Button size="sm" disabled>{props.cta}</Button>
        ) : (
          <LinkButton to={props.to} size="sm" variant="primary">{props.cta}</LinkButton>
        )
      ) : null}
    </li>
  );
}
