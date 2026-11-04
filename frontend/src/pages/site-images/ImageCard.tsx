import { useState, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { LabelChips } from "../../components/Labels";
import { Button, Card, FadeImage, cx } from "../../components/ui";
import { Modal, useToast } from "../../components/feedback";
import { errorMessage } from "../../lib/api";
import { formatDate, pathOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useSiteImageDetail, useSplitVariant } from "../../lib/siteImages";
import type { ImageLabel, SiteImage, SiteImageVariant } from "../../types";
import { PACKSHOT_KEYS, PackshotFeedback, usePackshotDecision } from "./PackshotFeedback";

/**
 * A card shows what helps to decide: the picture (a click enlarges it), its name, the page it comes from and one action.
 * Its state is the tab, its type the filter: neither is repeated here; sizes, versions and measures are in the detail.
 */
export function ImageCard(props: {
  item: SiteImage;
  labels: ImageLabel[];
  selected: boolean;
  /** Whether any card is ticked: the boxes then stay visible on every card, not only on the one under the pointer. */
  selecting: boolean;
  /** `range`: Shift was held (tick everything since the last click). */
  onSelect?: (on: boolean, range: boolean) => void;
  onOpen: () => void;
  onAdd?: () => void;
  onIgnore?: () => void;
  /** The action of the tab the card is shown in, in place of "Ajouter" and "Ignorer". */
  footer?: ReactNode;
}) {
  const { item } = props;
  const page = item.pages[0];
  const { decide } = usePackshotDecision(item);
  // Nyra team (the measures are sent to nobody else): P, N and Backspace answer for the card under the focus, and the focus goes to the next card.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const answer = PACKSHOT_KEYS[event.key];
    if (!item.packshot_measures || answer === undefined || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target instanceof HTMLInputElement && event.target.type !== "checkbox") return;
    event.preventDefault();
    decide(answer);
    (event.currentTarget.nextElementSibling?.querySelector("button, input") as HTMLElement | null)?.focus();
  }
  return (
    <div className="contents" onKeyDown={onKeyDown}>
    <Card padded={false} className={cx("group flex flex-col overflow-hidden", props.selected && "bg-yellow/45 ring-2 ring-focus")}>
      <div className="relative">
        <button type="button" className="block w-full bg-canvas" onClick={props.onOpen} aria-label={`Agrandir ${item.filename}`}>
          {item.thumb ? (
            <FadeImage src={item.thumb} alt="" loading="lazy" className="aspect-square w-full object-contain" />
          ) : (
            <span className="block aspect-square w-full" />
          )}
        </button>
        {props.onSelect ? (
          <input
            type="checkbox"
            aria-label={`Sélectionner ${item.filename}`}
            className={cx(
              "absolute top-2 left-2 h-5 w-5 accent-ink focus:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100",
              props.selecting || props.selected ? "opacity-100" : "opacity-0",
            )}
            checked={props.selected}
            onChange={(event) => props.onSelect?.(event.target.checked, (event.nativeEvent as MouseEvent).shiftKey === true)}
          />
        ) : null}
        {!item.compared ? (
          <span className="absolute top-2 right-2 rounded-full bg-paper/90 px-2 py-0.5 text-[11px] text-muted">pas encore comparée</span>
        ) : null}
        {item.variants.length > 1 ? (
          <span className="absolute bottom-2 right-2 rounded-full bg-paper/90 px-2 py-0.5 text-[11px] text-muted">{plural(item.variants.length, "version")}</span>
        ) : null}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        <p className="truncate text-sm font-medium" title={item.filename}>{item.filename}</p>
        <LabelChips labels={props.labels} />
        <p className="flex min-w-0 gap-1 text-xs text-muted">
          {page ? (
            <a href={page} target="_blank" rel="noreferrer noopener" className="truncate hover:text-ink hover:underline" title={page}>{pathOf(page)}</a>
          ) : null}
          <span className="shrink-0">{page ? "· " : ""}{plural(item.page_count, "page")}</span>
        </p>
        {props.footer ?? (props.onAdd ? <AddOrIgnore onAdd={props.onAdd} onIgnore={props.onIgnore} /> : null)}
        {item.packshot_measures ? <PackshotFeedback item={item} /> : null}
      </div>
    </Card>
    </div>
  );
}

function AddOrIgnore(props: { onAdd: () => void; onIgnore?: () => void }) {
  return (
    <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1.5">
      <Button size="sm" onClick={props.onAdd}>Ajouter à la bibliothèque</Button>
      <button type="button" className="px-1 text-[13px] text-muted underline underline-offset-2 hover:text-ink" onClick={props.onIgnore}>Ignorer</button>
    </div>
  );
}

export function Zoom(props: { item: SiteImage | null; onClose: () => void }) {
  const item = props.item;
  return (
    <Modal open={item !== null} onClose={props.onClose} title={item?.filename ?? ""} wide footer={<Button onClick={props.onClose}>Fermer</Button>}>
      {item ? <ZoomBody key={item.id} item={item} onClose={props.onClose} /> : null}
    </Modal>
  );
}

/** The chosen version of a photo (the largest at first), the other versions as a strip, and where the chosen one is. */
function ZoomBody(props: { item: SiteImage; onClose: () => void }) {
  const { item } = props;
  const { admin } = useOrg();
  const [chosen, setChosen] = useState(0);
  const variant = item.variants[chosen] ?? item.variants[0];
  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="grid content-start gap-3">
        <a href={variant.image || variant.thumb} target="_blank" rel="noreferrer noopener" className="block overflow-hidden rounded-lg border border-line bg-canvas">
          <img src={variant.image || variant.thumb || undefined} alt="" className="max-h-[60vh] w-full object-contain" />
        </a>
        {item.variants.length > 1 ? <VersionStrip variants={item.variants} chosen={chosen} onChoose={setChosen} /> : null}
        {admin && item.variants.length > 1 ? <SplitVersion id={variant.id} onDone={props.onClose} /> : null}
      </div>
      <div className="grid content-start gap-3 text-sm">
        <p className="text-muted">
          Vue pour la première fois le {formatDate(item.first_seen)}
          {variant.width && variant.height ? ` · ${variant.width} × ${variant.height} px` : ""}
        </p>
        <Detail id={variant.id} fallback={<Occurrences variant={variant} />} />
        {variant.url_count > 1 ? (
          <p className="text-muted">Le même fichier est servi à {variant.url_count} adresses (tailles ou CDN différents).</p>
        ) : null}
      </div>
    </div>
  );
}

/** The grouping can join two different photos (a shared banner or template): a person takes the version on screen out of the group. It gets its own card, nothing is hidden. */
function SplitVersion(props: { id: string; onDone: () => void }) {
  const split = useSplitVariant();
  const toast = useToast();
  const run = () =>
    split.mutate(props.id, {
      onSuccess: () => {
        toast.show({ tone: "success", message: "Version séparée du groupe", description: "Elle a maintenant sa propre carte." });
        props.onDone();
      },
      onError: (error) => toast.show({ tone: "error", message: "La version n'a pas été séparée", description: errorMessage(error) }),
    });
  return (
    <p className="text-xs text-muted">
      Cette version n'est pas la même photo que les autres ?{" "}
      <button type="button" disabled={split.isPending} onClick={run} className="font-medium text-ink underline underline-offset-2 disabled:opacity-60">
        Séparer cette version du groupe
      </button>
    </p>
  );
}

function VersionStrip(props: { variants: SiteImageVariant[]; chosen: number; onChoose: (index: number) => void }) {
  return (
    <div className="min-w-0">
      <p className="mb-1.5 text-xs text-muted">{plural(props.variants.length, "version")} de cette photo sur les sites</p>
      <div className="flex gap-2 overflow-x-auto pb-1" role="group" aria-label="Versions de la photo">
        {props.variants.map((variant, index) => (
          <button
            key={variant.id}
            type="button"
            aria-label={`Version ${index + 1}`}
            aria-pressed={index === props.chosen}
            onClick={() => props.onChoose(index)}
            className={cx("shrink-0 rounded-md border bg-canvas p-0.5 text-center text-[11px] text-muted", index === props.chosen ? "border-ink" : "border-line hover:border-ink-soft")}
          >
            <img src={variant.thumb || undefined} alt="" loading="lazy" className="h-14 w-14 object-contain" />
            {variant.width && variant.height ? `${variant.width} × ${variant.height}` : "·"}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Where the image is and the visual of the library it looks like (one request, made once the sheet is open); the sample of pages of the card shows while it comes. */
function Detail(props: { id: string; fallback: ReactNode }) {
  const { link } = useOrg();
  const detail = useSiteImageDetail(props.id);
  const pages = detail.data?.pages.flatMap((answer) => answer.pages);
  const first = detail.data?.pages[0];
  if (!first || !pages) return <>{props.fallback}</>;
  return (
    <>
      {first.visual ? (
        <p className="rounded-lg border border-line bg-canvas px-3 py-2">
          Ressemble à <Link className="font-medium text-ink underline underline-offset-2" to={`${link("library")}?visual=${first.visual.id}`}>{first.visual.filename}</Link> dans la bibliothèque · {Math.round(first.visual.score * 100)} %
        </p>
      ) : null}
      <div>
        <p className="font-medium">{plural(first.page_total, "page", "pages")}</p>
        <ul className="mt-1 grid gap-1">
          {pages.map(({ site, page }) => (
            <li key={`${site}${page}`} className="flex min-w-0 items-baseline gap-2">
              <span className="shrink-0 rounded-full bg-canvas px-2 py-0.5 text-[11px] text-muted">{site}</span>
              <a href={page} target="_blank" rel="noreferrer noopener" className="truncate text-ink-soft hover:underline" title={page}>{page}</a>
            </li>
          ))}
        </ul>
        {detail.hasNextPage ? <Button size="sm" className="mt-2" loading={detail.isFetchingNextPage} onClick={() => void detail.fetchNextPage()}>Voir plus</Button> : null}
      </div>
    </>
  );
}

/** Each page where this version was read, with the site version (US, FR...) it belongs to. */
function Occurrences(props: { variant: SiteImageVariant }) {
  const { variant } = props;
  return (
    <div>
      <p className="font-medium">{plural(variant.where_count, "page", "pages")}</p>
      <ul className="mt-1 grid gap-1">
        {variant.where.map(({ site, page }) => (
          <li key={`${site}${page}`} className="flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 rounded-full bg-canvas px-2 py-0.5 text-[11px] text-muted">{site}</span>
            <a href={page} target="_blank" rel="noreferrer noopener" className="truncate text-ink-soft hover:underline" title={page}>{page}</a>
          </li>
        ))}
        {variant.where_count > variant.where.length ? <li className="text-muted">et {variant.where_count - variant.where.length} autre(s)</li> : null}
      </ul>
    </div>
  );
}
