import { useState, type MouseEvent } from "react";
import type { Hit, MatchGroup } from "./types";
import { btn, btnDanger, btnGhost, card } from "./ui";

const levelLabel: Record<string, string> = {
  phash: "Hachage perceptuel (niveau 1)",
  dhash: "Hachage de différence (niveau 1)",
  clip: "Modèle visuel CLIP (niveau 2)",
};

type Props = {
  group: MatchGroup;
  hit: Hit;
  onClose: () => void;
  onStatusChange: (matchId: number, status: "confirmed" | "rejected", note?: string) => Promise<void>;
  onExclude: (matchId: number, reason?: string) => Promise<void>;
};

function ZoomImage(props: { src: string; alt: string }) {
  const [zoomed, setZoomed] = useState(false);
  const [origin, setOrigin] = useState("50% 50%");

  function move(event: MouseEvent<HTMLDivElement>) {
    if (!zoomed) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 100;
    const y = ((event.clientY - rect.top) / rect.height) * 100;
    setOrigin(`${x}% ${y}%`);
  }

  return (
    <div
      className="relative h-[280px] w-full overflow-hidden rounded-2xl border border-line bg-canvas"
      onMouseMove={move}
      onMouseLeave={() => setOrigin("50% 50%")}
    >
      <img
        className={`h-full w-full object-contain transition-transform duration-150 ${zoomed ? "cursor-zoom-out scale-[2.2]" : "cursor-zoom-in"}`}
        style={zoomed ? { transformOrigin: origin } : undefined}
        alt={props.alt}
        src={props.src}
        onClick={() => setZoomed((value) => !value)}
      />
    </div>
  );
}

export function CompareModal(props: Props) {
  const { group, hit } = props;
  const [note, setNote] = useState(hit.reviewed_note || "");
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={props.onClose}>
      <div className={`${card} max-h-[90vh] w-full max-w-3xl overflow-y-auto`} onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold">{group.filename}</h3>
            <p className="text-sm text-muted">
              Score {Math.round((hit.score || 0) * 100)} % · {levelLabel[hit.level] || hit.level}
            </p>
          </div>
          <button type="button" className={btnGhost} onClick={props.onClose}>Fermer</button>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Référence</p>
            <ZoomImage src={group.ref_image} alt={group.filename} />
          </div>
          <div>
            <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Trouvée sur le site</p>
            <ZoomImage src={hit.site_image} alt="Image trouvée sur le site" />
          </div>
        </div>

        <div className="mt-4 grid gap-1 text-sm">
          <p><strong>Statut</strong> : {hit.match_status === "confirmed" ? "Confirmée" : hit.match_status === "rejected" ? "Rejetée" : "En attente"}</p>
          <p><strong>Pages source</strong> ({hit.page_count ?? hit.pages.length}) :</p>
          <ul className="grid gap-1 pl-4">
            {hit.pages.map((url) => (
              <li key={url} className="list-disc">
                <a className="break-all text-sm underline" href={url} target="_blank" rel="noreferrer">{url}</a>
              </li>
            ))}
          </ul>
        </div>

        <label className="mt-4 block">
          <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-muted">Note (optionnelle)</span>
          <textarea
            className="min-h-[64px] w-full rounded-2xl border border-line bg-canvas p-3 text-sm outline-none focus:border-ink focus:bg-paper"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Pourquoi cette décision ?"
          />
        </label>

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            className={btn}
            disabled={busy}
            onClick={() => void run(() => props.onStatusChange(hit.match_id, "confirmed", note.trim() || undefined))}
          >
            Confirmer
          </button>
          <button
            type="button"
            className={btnGhost}
            disabled={busy}
            onClick={() => void run(() => props.onStatusChange(hit.match_id, "rejected", note.trim() || undefined))}
          >
            Rejeter
          </button>
          <button
            type="button"
            className={btnDanger}
            disabled={busy}
            onClick={() => void run(() => props.onExclude(hit.match_id, note.trim() || undefined))}
          >
            Exclure définitivement
          </button>
        </div>
        <p className="mt-2 text-xs text-muted">
          "Exclure définitivement" retient ce visuel comme faux positif récurrent (logo, asset générique) : il ne remontera plus jamais, même après un nouveau crawl.
        </p>
      </div>
    </div>
  );
}
