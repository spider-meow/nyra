import { useState } from "react";
import { LabelChips } from "../../components/Labels";
import { Button, Card, cx } from "../../components/ui";
import { Modal } from "../../components/feedback";
import { formatDate, pathOf, plural } from "../../lib/format";
import type { ImageLabel, SiteImage, SiteImageVariant } from "../../types";

export function ImageCard(props: {
  item: SiteImage;
  labels: ImageLabel[];
  selected: boolean;
  onSelect?: (on: boolean) => void;
  onOpen: () => void;
  onAdd?: () => void;
  onIgnore?: () => void;
}) {
  const { item } = props;
  return (
    <Card padded={false} className={cx("flex flex-col overflow-hidden", props.selected && "ring-2 ring-focus")}>
      <div className="relative">
        <button type="button" className="block w-full bg-canvas" onClick={props.onOpen} aria-label={`Agrandir ${item.filename}`}>
          {item.thumb ? (
            <img src={item.thumb} alt="" loading="lazy" className="aspect-square w-full object-contain" />
          ) : (
            <span className="block aspect-square w-full" />
          )}
        </button>
        {props.onSelect ? (
          <input
            type="checkbox"
            aria-label={`Sélectionner ${item.filename}`}
            className="absolute top-2 left-2 h-4 w-4 accent-ink"
            checked={props.selected}
            onChange={(event) => props.onSelect?.(event.target.checked)}
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
        <span className="w-fit rounded-full bg-urgent-soft px-2 py-0.5 text-[11px] font-medium text-urgent">Droits non vérifiés</span>
        <p className="truncate text-sm font-medium" title={item.filename}>{item.filename}</p>
        <LabelChips labels={props.labels} />
        <p className="text-xs text-muted">
          {item.sites.join(", ")} · {plural(item.page_count, "page")}
          {item.width && item.height ? ` · ${item.width} × ${item.height}` : ""}
        </p>
        {item.pages[0] ? (
          <a href={item.pages[0]} target="_blank" rel="noreferrer noopener" className="truncate text-xs text-muted hover:text-ink hover:underline" title={item.pages[0]}>
            {pathOf(item.pages[0])}
          </a>
        ) : null}
        {props.onAdd ? (
          <div className="mt-auto flex items-center gap-3 pt-1.5 text-[13px]">
            <button type="button" className="font-medium text-ink underline-offset-2 hover:underline" onClick={props.onAdd}>+ Ajouter</button>
            <button type="button" className="text-muted underline-offset-2 hover:text-ink hover:underline" onClick={props.onIgnore}>Ignorer</button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export function Zoom(props: { item: SiteImage | null; onClose: () => void }) {
  const item = props.item;
  return (
    <Modal open={item !== null} onClose={props.onClose} title={item?.filename ?? ""} wide footer={<Button onClick={props.onClose}>Fermer</Button>}>
      {item ? <ZoomBody key={item.id} item={item} /> : null}
    </Modal>
  );
}

/** The chosen version of a photo (the largest at first), the other versions as a strip, and where the chosen one is. */
function ZoomBody(props: { item: SiteImage }) {
  const { item } = props;
  const [chosen, setChosen] = useState(0);
  const variant = item.variants[chosen] ?? item.variants[0];
  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="grid content-start gap-3">
        <a href={variant.image || variant.thumb} target="_blank" rel="noreferrer noopener" className="block overflow-hidden rounded-lg border border-line bg-canvas">
          <img src={variant.image || variant.thumb || undefined} alt="" className="max-h-[60vh] w-full object-contain" />
        </a>
        {item.variants.length > 1 ? <VersionStrip variants={item.variants} chosen={chosen} onChoose={setChosen} /> : null}
      </div>
      <div className="grid content-start gap-3 text-sm">
        <p className="text-muted">
          Vue pour la première fois le {formatDate(item.first_seen)}
          {variant.width && variant.height ? ` · ${variant.width} × ${variant.height} px` : ""}
        </p>
        <Occurrences variant={variant} />
        {variant.url_count > 1 ? (
          <p className="text-muted">Le même fichier est servi à {variant.url_count} adresses (tailles ou CDN différents).</p>
        ) : null}
      </div>
    </div>
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
