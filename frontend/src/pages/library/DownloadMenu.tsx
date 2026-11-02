import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { Button, Spinner } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { bytes, num } from "../../lib/format";
import { useOrg } from "../../lib/org";
import type { DownloadChoice, LibraryItem } from "../../types";

/** Hands a file to the browser's download, without leaving the page. */
function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000); // after the browser has taken the file
}

function sizeText(choice: DownloadChoice): string {
  const pixels = choice.width && choice.height ? `${num(choice.width)} × ${num(choice.height)} px` : "Dimensions inconnues";
  return choice.bytes ? `${pixels} · ${bytes(choice.bytes)}` : pixels;
}

function Row(props: { choice: DownloadChoice; busy: boolean; disabled: boolean; onPick: () => void }) {
  return (
    <li role="none">
      <button type="button" role="menuitem" disabled={props.disabled} onClick={props.onPick} className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left outline-none hover:bg-sunk focus-visible:bg-sunk disabled:opacity-50">
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block text-sm font-medium">{props.choice.label}</span>
          <span className="block text-[12.5px] text-muted">{sizeText(props.choice)}</span>
        </span>
        {props.busy ? <span role="status" aria-label="Préparation du fichier" className="h-4 w-4 animate-spin rounded-full border-2 border-line-strong border-t-bark" /> : <Icon name="download" size={17} />}
      </button>
    </li>
  );
}

/** « Télécharger » and the sizes of one visual, with their dimensions. The file is made by the server when a size is picked; nothing is kept. */
export function DownloadMenu({ item }: { item: LibraryItem }) {
  const { apiPath, brand } = useOrg();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const list = useRef<HTMLUListElement>(null);
  const name = encodeURIComponent(item.filename);
  const sizes = useQuery({
    queryKey: ["library-downloads", brand.id, item.filename],
    queryFn: () => api.get<{ choices: DownloadChoice[] }>(apiPath(`/library/${name}/downloads`)),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  useEffect(() => {
    if (sizes.data) list.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
  }, [sizes.data]);

  async function pick(choice: DownloadChoice) {
    setBusy(choice.key);
    try {
      const file = await api.file(apiPath(`/library/${name}/download?size=${choice.key}`));
      saveBlob(file.blob, file.name);
      setOpen(false);
    } catch (error) {
      toast.show({ tone: "error", message: "Le téléchargement n'a pas abouti", description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation(); // closes the menu, not the sheet behind it
      setOpen(false);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const rows = [...(list.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not(:disabled)") ?? [])];
      event.preventDefault();
      rows[(rows.findIndex((row) => row === document.activeElement) + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length]?.focus();
    }
  }

  return (
    <div className="relative" onKeyDown={onKeyDown}>
      <Button aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="download" size={16} /> Télécharger <Icon name="down" size={14} />
      </Button>
      {open ? (
        <>
          <div className="fixed inset-0 z-30" onMouseDown={() => setOpen(false)} />
          <div className="absolute bottom-full left-0 z-40 mb-2 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-line-strong bg-paper p-1.5 shadow-float">
            {sizes.data ? (
              <ul ref={list} role="menu" aria-label="Taille du téléchargement" className="grid gap-0.5">
                {sizes.data.choices.map((choice) => (
                  <Row key={choice.key} choice={choice} busy={busy === choice.key} disabled={busy !== null} onPick={() => void pick(choice)} />
                ))}
              </ul>
            ) : sizes.isError ? (
              <p className="grid gap-2 px-3 py-2 text-[13px] text-muted">
                {errorMessage(sizes.error)}
                <Button size="sm" onClick={() => void sizes.refetch()}>Réessayer</Button>
              </p>
            ) : (
              <p className="px-3 py-2"><Spinner label="Tailles disponibles…" /></p>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
