import { useRef, useState } from "react";
import { Modal, useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { Button, cx } from "../../components/ui";
import { api, errorMessage, isCancelled } from "../../lib/api";
import { plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import type { LibraryItem } from "../../types";
import { saveBlob } from "./DownloadMenu";

/** The sizes of an archive (the long side in px, as `downloads.STEPS` on the server): the same for every visual, a smaller one stays as it is. */
const SIZES = [
  { key: "original", label: "Original", hint: "Tel qu'enregistré (2 048 px au plus)" },
  { key: "large", label: "Grand", hint: "2 000 px sur le grand côté" },
  { key: "medium", label: "Moyen", hint: "1 200 px sur le grand côté" },
  { key: "web", label: "Web", hint: "800 px sur le grand côté" },
];
/** Same as `downloads.MAX_ZIP_FILES`: the server refuses more, this only says so before the wait. */
const MAX_FILES = 50;

/** « Télécharger » for the selection: one size for all, one ZIP. The server makes the file while it sends it, so the wait has no progress to show: it says so and can be cancelled. */
export function ZipDialog({ items, onClose }: { items: LibraryItem[]; onClose: () => void }) {
  const { apiPath } = useOrg();
  const toast = useToast();
  const [size, setSize] = useState("original");
  const [busy, setBusy] = useState(false);
  const running = useRef<AbortController | null>(null);
  const tooMany = items.length > MAX_FILES;

  async function start() {
    const controller = new AbortController();
    running.current = controller;
    setBusy(true);
    try {
      const body = JSON.stringify({ filenames: items.map((item) => item.filename), size });
      const file = await api.file(apiPath("/library/zip"), { method: "POST", body, signal: controller.signal });
      saveBlob(file.blob, file.name);
      onClose();
    } catch (error) {
      if (!isCancelled(error)) toast.show({ tone: "error", message: "Le téléchargement n'a pas abouti", description: errorMessage(error) });
    } finally {
      running.current = null;
      setBusy(false);
    }
  }

  function close() {
    running.current?.abort(); // closing while the file is prepared cancels it: the server stops at the next image
    onClose();
  }

  return (
    <Modal
      open
      onClose={close}
      title={`Télécharger ${plural(items.length, "visuel", "visuels")}`}
      footer={
        <>
          <Button onClick={close}>{busy ? "Annuler le téléchargement" : "Annuler"}</Button>
          <Button variant="primary" disabled={tooMany} loading={busy} onClick={() => void start()}>Télécharger le ZIP</Button>
        </>
      }
    >
      <div className="grid gap-3">
        <ul className="grid gap-1 pt-1" role="radiogroup" aria-label="Taille du téléchargement">
          {SIZES.map((option) => (
            <li key={option.key}>
              <button
                type="button"
                role="radio"
                aria-checked={size === option.key}
                disabled={busy}
                onClick={() => setSize(option.key)}
                className={cx("flex w-full items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm disabled:opacity-60", size === option.key ? "border-ink bg-yellow/40 font-medium" : "border-line hover:bg-sunk")}
              >
                <span className={cx("grid h-4 w-4 shrink-0 place-items-center rounded-full border", size === option.key ? "border-ink bg-ink text-white" : "border-line-strong")}>
                  {size === option.key ? <Icon name="check" size={10} strokeWidth={3} /> : null}
                </span>
                <span className="min-w-0 leading-tight">
                  <span className="block">{option.label}</span>
                  <span className="block text-[12.5px] font-normal text-muted">{option.hint}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        <p className="text-[13px] text-muted">Un visuel plus petit que la taille choisie reste tel quel : il n'est jamais agrandi.</p>
        {tooMany ? <p role="alert" className="rounded-lg bg-peach-soft px-3 py-2 text-[13px] text-bark-800">{MAX_FILES} visuels au plus par fichier : téléchargez en deux fois.</p> : null}
        {busy ? <p role="status" className="rounded-lg bg-sunk px-3 py-2 text-[13px] text-ink-soft">Préparation du fichier… Cela peut prendre un moment : gardez cette fenêtre ouverte.</p> : null}
      </div>
    </Modal>
  );
}
