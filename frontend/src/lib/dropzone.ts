/** The logic of dropping files on the page (see `useUpload`), apart from React so that `npm run check:drop` can run it. */

/** Whether a drag carries files: a dragged link, a selected text or a card of the page must not light the drop zone. */
export function carriesFiles(types: ArrayLike<string> | undefined): boolean {
  return Array.from(types ?? []).includes("Files");
}

/** The files already waiting, then the dropped ones: the same file dropped twice is kept once. */
export function mergeFiles(waiting: File[], dropped: File[]): File[] {
  const same = (a: File, b: File) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
  return dropped.reduce((all, file) => (all.some((other) => same(other, file)) ? all : [...all, file]), waiting);
}

/**
 * Counts how deep a drag is inside the page: the browser fires `dragenter` on every element it crosses and `dragleave`
 * on the one it leaves, so a plain on/off flag flickers or sticks. Over when the count is back to 0 (or after a drop).
 */
export function dragDepth(depth: number, event: "enter" | "leave" | "drop"): number {
  if (event === "drop") return 0;
  return Math.max(0, depth + (event === "enter" ? 1 : -1));
}

/** The formats the server takes (`IMAGE_SUFFIXES` in routes/common.py): judged by the extension, since a browser gives TIFF no reliable type. */
const IMAGE_EXTENSION = /\.(jpe?g|png|webp|gif|bmp|tiff?|avif)$/i;
const isVideo = (file: { name: string; type: string }) => file.type.startsWith("video/") || /\.(mp4|mov|avi|mkv|webm)$/i.test(file.name);

/** Images go on the waiting list; the rest (videos, documents) stays out. An empty type is fine (the extension decides), another type is not. */
export function splitImages<T extends { name: string; type: string }>(files: T[]): { images: T[]; rejected: T[] } {
  const ok = (file: T) => IMAGE_EXTENSION.test(file.name) && (file.type === "" || file.type.startsWith("image/"));
  return { images: files.filter(ok), rejected: files.filter((file) => !ok(file)) };
}

/** What to tell the person about the files left out: how many and why (« 2 vidéos non prises en charge »), then their names (three at most). */
export function rejectionNotice(rejected: { name: string; type: string }[]): { message: string; description: string } {
  const many = rejected.length > 1;
  const what = rejected.every(isVideo) ? (many ? "vidéos non prises" : "vidéo non prise") : many ? "fichiers non pris" : "fichier non pris";
  const names = rejected.slice(0, 3).map((file) => file.name).join(", ") + (rejected.length > 3 ? ` et ${rejected.length - 3} autre(s)` : "");
  return { message: `${rejected.length} ${what} en charge`, description: `Seules les images sont importées. Écarté : ${names}.` };
}
