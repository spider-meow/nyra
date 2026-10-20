import { bytes, num } from "../lib/format";
import type { UsageItem } from "../types";
import { cx } from "./ui";

const BAR = { ok: "bg-ok", warning: "bg-soon", reached: "bg-expired" };
const TEXT = { ok: "text-ink", warning: "text-soon", reached: "text-expired" };

/** "1 240 sur 5 000" (storage in bytes: "3,2 Go sur 20 Go"). */
export const usageText = (item: UsageItem) => (item.key === "storage" ? `${bytes(item.used)} sur ${bytes(item.limit)}` : `${num(item.used)} sur ${num(item.limit)}`);

/** A thin bar of how much of a limit is used: green, amber from 80 %, red when it is reached. Hidden from screen readers: the text "X sur Y" says it. */
export function UsageBar(props: { item: UsageItem; className?: string }) {
  const { item } = props;
  const ratio = item.limit ? Math.min(1, item.used / item.limit) : 1;
  return (
    <div aria-hidden className={cx("h-1.5 w-32 overflow-hidden rounded-full bg-canvas", props.className)}>
      <div className={cx("h-full rounded-full", BAR[item.level])} style={{ width: `${ratio * 100}%` }} />
    </div>
  );
}

export const usageTone = (item: UsageItem) => TEXT[item.level];
