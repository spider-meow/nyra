import type { Brand } from "../types";
import { cx } from "./ui";

/** Two letters from a name or an e-mail: "Louis XIII" gives LX, "adrien.duchemin@…" gives AD. */
export function initials(text: string): string {
  const words = text.split("@")[0].split(/[\s._-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}

/** The brand's logo, or its initials on a soft tile when it has none. */
export function BrandMark(props: { brand: Pick<Brand, "name" | "logo">; size?: number; className?: string }) {
  const size = props.size ?? 32;
  if (props.brand.logo) {
    return <img src={props.brand.logo} alt="" width={size} height={size} className={cx("shrink-0 rounded-lg object-contain", props.className)} style={{ width: size, height: size }} />;
  }
  return (
    <span
      aria-hidden
      className={cx("grid shrink-0 place-items-center rounded-lg bg-peach text-[13px] font-semibold text-bark-800", props.className)}
      style={{ width: size, height: size }}
    >
      {initials(props.brand.name)}
    </span>
  );
}
