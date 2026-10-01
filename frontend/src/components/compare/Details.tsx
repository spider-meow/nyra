import { pathOf } from "../../lib/format";
import type { Hit } from "../../types";

/** The pages the image is visible on. */
export function PageList(props: { hit: Hit }) {
  const { hit } = props;
  return (
    <div className="rounded-xl border border-line">
      <p className="border-b border-line px-3.5 py-2.5 text-[13px] font-semibold">
        Visible sur {hit.page_count} page{hit.page_count > 1 ? "s" : ""}
      </p>
      <ul className="max-h-40 overflow-y-auto px-3.5 py-2 font-mono text-xs">
        {hit.pages.map((page) => (
          <li key={page} className="truncate py-0.5">
            <a href={page} target="_blank" rel="noreferrer noopener" className="text-bark-700 hover:text-bark-800 hover:underline">
              {pathOf(page)}
            </a>
          </li>
        ))}
        {hit.page_count > hit.pages.length ? <li className="py-0.5 text-muted">et {hit.page_count - hit.pages.length} autre(s)</li> : null}
      </ul>
    </div>
  );
}

/** Address, method, score and variants, folded away. */
export function TechnicalDetails(props: { hit: Hit }) {
  const { hit } = props;
  return (
    <details className="text-[13px] text-muted">
      <summary className="cursor-pointer select-none">Détails techniques</summary>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt>Adresse de l'image</dt>
        <dd className="break-all"><a className="underline" href={hit.site_url} target="_blank" rel="noreferrer noopener">{hit.site_url}</a></dd>
        <dt>Méthode</dt>
        <dd>{methodLabel(hit.level, hit.confidence)}</dd>
        <dt>Score</dt>
        <dd className="tabular">{Math.round(hit.score * 100)} %</dd>
        <dt>Variantes</dt>
        <dd>{hit.site_image_ids.length} adresse(s) servant le même fichier</dd>
      </dl>
    </details>
  );
}

function methodLabel(level: string, confidence: string): string {
  if (level === "geo") {
    return confidence === "haut"
      ? "Même photo, vérifiée point par point (recadrage ou retouche possible)"
      : "Élément commun vérifié point par point (même détourage produit, autre composition ?)";
  }
  if (level === "clip") return "Ressemblance visuelle (CLIP), non vérifiée";
  return `Empreinte perceptuelle (${level})`;
}
