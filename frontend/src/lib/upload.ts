import type { ToastInput } from "../components/feedback";
import { ApiError, api, errorMessage, isUnanswered } from "./api";
import { plural } from "./format";

// A serverless host refuses request bodies above ~4.5 MB: send a few files at a time.
const BATCH_BYTES = 3_500_000;
const BATCH_FILES = 8;

export function batches(files: File[]): File[][] {
  const out: File[][] = [];
  let current: File[] = [];
  let size = 0;
  for (const file of files) {
    if (current.length && (size + file.size > BATCH_BYTES || current.length >= BATCH_FILES)) {
      out.push(current);
      current = [];
      size = 0;
    }
    current.push(file);
    size += file.size;
  }
  if (current.length) out.push(current);
  return out;
}

/**
 * The choices common to an upload: the rights (a date, or unlimited rights, or neither: each file keeps what it had) and what to do
 * with a file whose name is already in the library: keep both (the new one is numbered, `a (1).jpg`), or `replace` the old image.
 */
export type UploadRights = { expiry: string; unlimited: boolean; replace: boolean };
export const NO_RIGHTS: UploadRights = { expiry: "", unlimited: false, replace: false };

/** Where an upload goes: an existing collection, a new one (created just before sending), or nowhere. */
export type Destination = { collectionId?: string; newName?: string };

export type UploadResult = {
  saved: string[];
  /** Also in `saved`: `replace` was chosen and a visual of that name was already in the library. */
  replaced: string[];
  /** Also in `saved`, under `to`: the name `from` was already taken, the new visual got another one. */
  renamed: { from: string; to: string }[];
  failed: { filename: string; reason: string }[];
};

/** One request: what the server saved or refused goes into `result`; a failed request refuses the whole batch. */
export async function sendBatch(path: string, batch: File[], result: UploadResult, rights: UploadRights, collectionId?: string): Promise<void> {
  const body = new FormData();
  for (const file of batch) body.append("files", file);
  if (rights.replace) body.append("replace", "true");
  if (rights.unlimited) body.append("unlimited_rights", "true");
  else if (rights.expiry) body.append("expiry_date", rights.expiry);
  if (collectionId) body.append("collection_id", collectionId);
  try {
    const data = await api.post<UploadResult>(path, body);
    result.saved.push(...data.saved);
    result.replaced.push(...(data.replaced ?? []));
    result.renamed.push(...(data.renamed ?? []));
    result.failed.push(...data.failed);
  } catch (error) {
    const reason =
      error instanceof ApiError && error.status === 413
        ? "Fichier trop lourd pour être envoyé (4 Mo au plus par fichier sur ce serveur)."
        : isUnanswered(error)
          ? "Le serveur n'a pas répondu pendant l'envoi. Réessayez ; si cela se reproduit avec ce fichier, il est peut-être trop grand (en pixels) : réduisez-le, puis renvoyez-le."
          : errorMessage(error);
    result.failed.push(...batch.map((file) => ({ filename: file.name, reason })));
  }
}

/** Every file sent must come back as saved or refused; anything else is reported, never dropped silently. */
export function reportUnanswered(files: File[], result: UploadResult): void {
  const answered = new Set([...result.saved, ...result.renamed.map((item) => item.from), ...result.failed.map((item) => item.filename)]);
  for (const file of files) {
    if (!answered.has(file.name)) result.failed.push({ filename: file.name, reason: "Le serveur n'a rien répondu pour ce fichier." });
  }
}

/** The final state of the progress toast. */
export function summary(result: UploadResult): Partial<ToastInput> {
  const saved = result.saved.length;
  const replaced = result.replaced.length;
  const renamed = result.renamed.length;
  const failed = result.failed.length;
  const added = saved - replaced;
  if (saved && !failed && !replaced && !renamed) {
    return {
      tone: "success",
      message: saved > 1 ? `${saved} visuels ajoutés` : "Visuel ajouté",
      description: "L'indexation démarre, puis la comparaison avec les sites de la marque se fait toute seule.",
      progress: undefined,
    };
  }
  if (saved) {
    return {
      tone: failed ? "info" : "success",
      message: `${saved} sur ${saved + failed} ${saved + failed > 1 ? "fichiers enregistrés" : "fichier enregistré"}`,
      description: [
        added ? plural(added, "nouveau", "nouveaux") : "",
        replaced ? `${replaced} ${replaced > 1 ? "remplacés" : "remplacé"} (même nom déjà présent)` : "",
        renamed ? `${renamed} ${renamed > 1 ? "renommés" : "renommé"} (nom déjà pris)` : "",
        failed ? plural(failed, "refusé", "refusés") : "",
      ].filter(Boolean).join(" · ") + ". Le détail est en haut de la bibliothèque.",
      progress: undefined,
    };
  }
  return {
    tone: "error",
    message: failed > 1 ? "Aucun visuel n'a pu être ajouté" : "Le visuel n'a pas pu être ajouté",
    description: failed > 1 ? "Le détail est affiché en haut de la bibliothèque." : result.failed[0]?.reason,
    progress: undefined,
  };
}
