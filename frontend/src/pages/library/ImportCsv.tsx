import { useState } from "react";
import { Modal, useToast } from "../../components/feedback";
import { Button, Spinner, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate, plural } from "../../lib/format";
import { useLibraryMutations } from "../../lib/queries";
import type { ImportRow } from "../../types";

const importStatus: Record<ImportRow["status"], string> = {
  ok: "Prêt",
  unknown_file: "Fichier inconnu",
  bad_date: "Date invalide",
  bad_tags: "Tag invalide",
};

export function ImportCsv(props: { open: boolean; onClose: () => void }) {
  const { importCsv } = useLibraryMutations();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<ImportRow[] | null>(null);

  function close() {
    setFile(null);
    setRows(null);
    props.onClose();
  }

  function preview(next: File) {
    setFile(next);
    importCsv.mutate({ file: next, apply: false }, { onSuccess: (data) => setRows(data.rows), onError: (error) => toast.show({ tone: "error", message: "Le fichier n'a pas pu être lu", description: errorMessage(error) }) });
  }

  function apply() {
    if (!file) return;
    importCsv.mutate(
      { file, apply: true },
      {
        onSuccess: (data) => {
          toast.show({
            tone: "success",
            message: `${plural(data.applied, "visuel mis à jour", "visuels mis à jour")}`,
            description: "Le tableau de bord en tient compte dès maintenant.",
          });
          close();
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  const ready = rows?.filter((row) => row.status === "ok").length ?? 0;
  return (
    <Modal
      open={props.open}
      onClose={close}
      title="Importer depuis un CSV"
      wide
      footer={
        <>
          <Button onClick={close}>Annuler</Button>
          <Button variant="primary" disabled={!ready} loading={importCsv.isPending && rows !== null} onClick={apply}>
            {ready ? `Appliquer ${plural(ready, "ligne")}` : "Appliquer"}
          </Button>
        </>
      }
    >
      <ImportBody rows={rows} reading={importCsv.isPending && !rows} onFile={preview} />
    </Modal>
  );
}

function ImportBody(props: { rows: ImportRow[] | null; reading: boolean; onFile: (file: File) => void }) {
  return (
    <>
      <p className="text-sm text-ink-soft">
        Colonnes attendues : <code className="rounded bg-canvas px-1">filename</code>, <code className="rounded bg-canvas px-1">expiry_date</code>, et en option <code className="rounded bg-canvas px-1">credit</code>, <code className="rounded bg-canvas px-1">notes</code>, <code className="rounded bg-canvas px-1">tags</code> (séparés par des virgules). Dates au format AAAA-MM-JJ ou JJ/MM/AAAA. Sans colonne <code className="rounded bg-canvas px-1">tags</code>, les tags existants ne sont pas touchés ; avec, ils sont remplacés. Rien n'est modifié avant de cliquer sur « Appliquer ».
      </p>
      <input
        type="file"
        accept=".csv,text/csv"
        className="mt-4 block text-sm"
        onChange={(event) => {
          const next = event.target.files?.[0];
          if (next) props.onFile(next);
        }}
      />
      {props.reading ? (
        <div className="mt-4"><Spinner label="Lecture du fichier…" /></div>
      ) : null}
      {props.rows ? <ImportPreview rows={props.rows} /> : null}
    </>
  );
}

function ImportPreview({ rows }: { rows: ImportRow[] }) {
  return (
    <div className="mt-4 max-h-80 overflow-y-auto rounded-lg border border-line">
      <table className="w-full text-[13px]">
        <thead className="sticky top-0 bg-paper">
          <tr className="border-b border-line text-left text-muted">
            <th className="px-3 py-2 font-medium">Ligne</th>
            <th className="px-3 py-2 font-medium">Fichier</th>
            <th className="px-3 py-2 font-medium">Échéance</th>
            <th className="px-3 py-2 font-medium">Tags</th>
            <th className="px-3 py-2 font-medium">État</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((row) => (
            <tr key={`${row.line}-${row.filename}`}>
              <td className="px-3 py-1.5 text-muted tabular">{row.line}</td>
              <td className="max-w-[240px] truncate px-3 py-1.5">{row.filename}</td>
              <td className="px-3 py-1.5 tabular">{row.unlimited_rights ? "Illimitée" : row.expiry_date ? formatDate(row.expiry_date) : "·"}</td>
              <td className="max-w-[200px] truncate px-3 py-1.5" title={row.tags?.join(", ")}>{row.tags === null ? <span className="text-muted">inchangés</span> : row.tags.length ? row.tags.join(", ") : "·"}</td>
              <td className={cx("px-3 py-1.5", row.status === "ok" ? "text-ok" : "text-expired")} title={row.message}>
                {importStatus[row.status]}
                {row.message && row.status !== "ok" && row.status !== "unknown_file" ? <span className="block text-xs text-muted">{row.message}</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
