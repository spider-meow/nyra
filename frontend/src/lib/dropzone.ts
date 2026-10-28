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
