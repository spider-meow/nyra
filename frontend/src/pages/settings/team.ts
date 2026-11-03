import type { Member } from "../../types";

export const ROLE_LABEL: Record<Member["role"], string> = { admin: "Administrateur", client: "Lecture et validation" };
export const ROLE_HELP: Record<Member["role"], string> = {
  admin: "Gère la bibliothèque, les réglages et l'équipe.",
  client: "Consulte tout et traite les correspondances, sans modifier la bibliothèque ni l'équipe.",
};
/** The search box appears when the team is longer than this. */
export const SEARCH_ABOVE = 8;

/** "adrien.duchemin@maison.fr" gives "Adrien Duchemin"; an address with no usable word is shown as it is. */
export function displayName(email: string): string {
  const words = email.split("@")[0].split(/[._-]+/).filter(Boolean);
  return words.map((word) => word[0].toLocaleUpperCase("fr") + word.slice(1)).join(" ") || email;
}

/** Always the same order: administrators first, then by name, then by address (two people can share a name). */
export function orderMembers(members: Member[]): Member[] {
  const byName = (a: Member, b: Member) => displayName(a.email).localeCompare(displayName(b.email), "fr") || a.email.localeCompare(b.email, "fr");
  return [...members].sort((a, b) => Number(b.role === "admin") - Number(a.role === "admin") || byName(a, b));
}

const plain = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

/** Every word typed is found in the name or the address, without caring for case or accents. */
export function matches(member: Member, query: string): boolean {
  const haystack = plain(`${displayName(member.email)} ${member.email}`);
  return plain(query).split(/\s+/).filter(Boolean).every((word) => haystack.includes(word));
}

/** The people in use, and the invitations nobody opened yet, each in the order of `orderMembers`. */
export function splitPending(members: Member[]): { active: Member[]; waiting: Member[] } {
  const ordered = orderMembers(members);
  return { active: ordered.filter((member) => !member.pending), waiting: ordered.filter((member) => member.pending) };
}

/** The only administrator: the server refuses to demote or remove them, so the page says so before the click. */
export function isLastAdmin(members: Member[], member: Member): boolean {
  return member.role === "admin" && members.filter((other) => other.role === "admin").length <= 1;
}
