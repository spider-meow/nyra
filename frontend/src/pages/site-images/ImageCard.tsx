import { Button, Card, cx } from "../../components/ui";
import { Modal } from "../../components/feedback";
import { formatDate, pathOf, plural } from "../../lib/format";
import type { SiteImage } from "../../types";

export function ImageCard(props: {
  item: SiteImage;
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
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        <span className="w-fit rounded-full bg-urgent-soft px-2 py-0.5 text-[11px] font-medium text-urgent">Droits non vérifiés</span>
        <p className="truncate text-sm font-medium" title={item.filename}>{item.filename}</p>
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
          <div className="mt-auto flex gap-1 pt-2">
            <Button size="sm" variant="primary" className="flex-1" onClick={props.onAdd}>Ajouter</Button>
            <Button size="sm" variant="ghost" onClick={props.onIgnore}>Ignorer</Button>
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
      {item ? (
        <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <a href={item.image || item.thumb} target="_blank" rel="noreferrer noopener" className="block overflow-hidden rounded-lg border border-line bg-canvas">
            <img src={item.image || item.thumb || undefined} alt="" className="max-h-[60vh] w-full object-contain" />
          </a>
          <div className="grid content-start gap-3 text-sm">
            <p className="text-muted">
              Vue pour la première fois le {formatDate(item.first_seen)}
              {item.width && item.height ? ` · ${item.width} × ${item.height} px` : ""}
            </p>
            <div>
              <p className="font-medium">{plural(item.page_count, "page", "pages")}</p>
              <ul className="mt-1 grid gap-1">
                {item.pages.map((page) => (
                  <li key={page} className="truncate">
                    <a href={page} target="_blank" rel="noreferrer noopener" className="text-ink-soft hover:underline">{page}</a>
                  </li>
                ))}
                {item.page_count > item.pages.length ? <li className="text-muted">et {item.page_count - item.pages.length} autre(s)</li> : null}
              </ul>
            </div>
            {item.url_count > 1 ? (
              <p className="text-muted">Le même fichier est servi à {item.url_count} adresses (tailles ou CDN différents).</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </Modal>
  );
}
