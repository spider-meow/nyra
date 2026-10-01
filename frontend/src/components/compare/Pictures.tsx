import { useState } from "react";
import { cx } from "../ui";
import { hostOf } from "../../lib/format";
import type { Hit, MatchGroup } from "../../types";

export type Mode = "side" | "overlay";
export type Zoom = { src: string; title: string };

/** The reference and the found image, side by side or one over the other. */
export function Pictures(props: { group: MatchGroup; hit: Hit; mode: Mode; onZoom: (zoom: Zoom) => void }) {
  const { group, hit, onZoom } = props;
  const [mix, setMix] = useState(50);
  const refSrc = group.ref_image || group.ref_thumb;
  const siteSrc = hit.site_image || hit.site_thumb;

  if (props.mode === "side") {
    return (
      <div className="grid grid-cols-2 gap-3">
        <Figure label="Référence" src={refSrc} onZoom={() => onZoom({ src: refSrc, title: `Référence : ${group.filename}` })} />
        <Figure found label={`Sur ${hostOf(hit.site_url)}`} src={siteSrc} onZoom={() => onZoom({ src: siteSrc, title: hit.site_url })} />
      </div>
    );
  }
  return (
    <div>
      <div className="relative aspect-[4/3] overflow-hidden rounded-2xl bg-side">
        <img src={refSrc} alt="Référence" className="absolute inset-0 h-full w-full object-contain" />
        <img src={siteSrc} alt="Image trouvée" className="absolute inset-0 h-full w-full object-contain" style={{ opacity: mix / 100 }} />
      </div>
      <label className="mt-2 flex items-center gap-3 text-[13px] text-muted">
        Référence
        <input type="range" min={0} max={100} value={mix} onChange={(event) => setMix(Number(event.target.value))} className="flex-1 accent-bark" aria-label="Mélange entre la référence et l'image trouvée" />
        Trouvée
      </label>
    </div>
  );
}

function Figure(props: { label: string; src: string; onZoom: () => void; found?: boolean }) {
  return (
    <figure className="min-w-0">
      <button
        type="button"
        onClick={props.onZoom}
        className={cx("block w-full overflow-hidden rounded-2xl bg-side", props.found && "ring-3 ring-peach")}
        aria-label={`Agrandir : ${props.label}`}
      >
        {props.src ? <img src={props.src} alt="" className="aspect-square w-full object-contain" /> : <span className="block aspect-square" />}
      </button>
      <figcaption className="mt-2 truncate text-[13px] font-medium">{props.label}</figcaption>
    </figure>
  );
}
