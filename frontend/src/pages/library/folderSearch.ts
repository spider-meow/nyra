import { plain } from "../../lib/text";
import type { CollectionIndex } from "./collections";

/** One collection of the picker: its own name, the path above it, and how deep it is (0 at the top). */
export type FolderRow = { id: string; name: string; parent: string; path: string; depth: number };

const LEVELS_GUARD = 10; // the API allows 3 levels: this only keeps a corrupted tree from looping

/** Every collection in the order of the tree: a parent, then its children, siblings in the order chosen. */
export function folderRows(folders: CollectionIndex): FolderRow[] {
  const rows: FolderRow[] = [];
  const walk = (parentId: string | null, depth: number) => {
    if (depth >= LEVELS_GUARD) return;
    for (const collection of folders.children.get(parentId) ?? []) {
      rows.push({ id: collection.id, name: collection.name, parent: collection.parent_id ? folders.path(collection.parent_id) : "", path: folders.path(collection.id), depth });
      walk(collection.id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

const wordsOf = (query: string) => plain(query).split(/\s+/).filter(Boolean);

/** The rows whose whole path holds every word typed (case and accents ignored, any order); every row when nothing is typed. */
export function searchFolders(rows: FolderRow[], query: string): FolderRow[] {
  const words = wordsOf(query);
  return words.length ? rows.filter((row) => words.every((word) => plain(row.path).includes(word))) : rows;
}

/** `text` in pieces, `hit` true for the parts a typed word was found in (the same comparison as `searchFolders`). */
export function highlight(text: string, query: string): { text: string; hit: boolean }[] {
  const words = wordsOf(query);
  if (!words.length) return [{ text, hit: false }];
  // `plain` can change how many characters a letter takes: fold one character at a time and remember where each folded one came from.
  const origin: number[] = [];
  let folded = "";
  for (let at = 0; at < text.length; at += 1) {
    const piece = plain(text[at]);
    folded += piece;
    for (let k = 0; k < piece.length; k += 1) origin.push(at);
  }
  const marked = new Array<boolean>(text.length).fill(false);
  for (const word of words) {
    for (let found = folded.indexOf(word); found >= 0; found = folded.indexOf(word, found + word.length)) {
      for (let k = found; k < found + word.length; k += 1) marked[origin[k]] = true;
    }
  }
  const pieces: { text: string; hit: boolean }[] = [];
  for (let at = 0; at < text.length; at += 1) {
    const last = pieces[pieces.length - 1];
    if (last && last.hit === marked[at]) last.text += text[at];
    else pieces.push({ text: text[at], hit: marked[at] });
  }
  return pieces;
}
