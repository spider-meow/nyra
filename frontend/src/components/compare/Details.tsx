import type { Hit } from "../../types";

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
