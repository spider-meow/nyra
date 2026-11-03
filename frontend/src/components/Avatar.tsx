import { useMemo } from "react";
import { avatarSrc } from "../lib/avatar";
import { cx } from "./ui";

/** The avatar of a person, drawn from their id (never their e-mail): the same on every screen. Decorative: the name stays written beside it. */
export function Avatar({ seed, size = 32, className }: { seed: string; size?: number; className?: string }) {
  const src = useMemo(() => avatarSrc(seed), [seed]);
  return <img src={src} alt="" aria-hidden width={size} height={size} className={cx("shrink-0 rounded-full", className)} />;
}
