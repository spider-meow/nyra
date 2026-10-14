import { useId, useState, type ReactNode } from "react";
import { Card, cx } from "./ui";

/**
 * A small "i" in a circle that explains what is next to it. Opens on hover, on keyboard focus and on a tap;
 * Escape closes it; the pointer can move onto the bubble. Never put what a person must know in here.
 */
export function InfoTip(props: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onKeyDown={(event) => event.key === "Escape" && setOpen(false)}>
      <button
        type="button"
        aria-label={props.label ?? "Plus d'informations"}
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen(true)}
        className="grid size-[18px] place-items-center rounded-full border border-line-strong text-[11px] leading-none font-semibold text-muted transition-colors hover:border-ink hover:text-ink focus-visible:border-ink focus-visible:text-ink focus-visible:outline-none"
      >
        i
      </button>
      {open ? (
        <span id={id} role="tooltip" className="tip-in absolute top-full left-0 z-40 mt-2 w-64 rounded-lg bg-ink px-3 py-2 text-left text-[13px] leading-snug font-normal text-paper shadow-float">
          {props.children}
        </span>
      ) : null}
    </span>
  );
}

/** One titled group of a settings section: its title (with an "i" for the explanation) above a quiet card. */
export function SettingsBlock(props: { title: ReactNode; description?: ReactNode; children: ReactNode; danger?: boolean; aside?: ReactNode }) {
  return (
    <section className="mt-8 first:mt-0">
      <div className="mb-3 flex min-h-[18px] items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          {props.title}
          {props.description ? <InfoTip>{props.description}</InfoTip> : null}
        </h3>
        {props.aside}
      </div>
      <Card padded={false} tone={props.danger ? "danger" : "quiet"}>{props.children}</Card>
    </section>
  );
}

/** A line of a settings card: what it is on the left (a short explanation under it), its control on the right. */
export function SettingsRow(props: { label: ReactNode; hint?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div className={cx("flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t border-line px-5 py-4 text-sm first:border-t-0", props.className)}>
      <div className="min-w-0 flex-1 basis-56">
        <p className="font-medium">{props.label}</p>
        {props.hint ? <p className="mt-0.5 text-[13px] leading-snug text-muted">{props.hint}</p> : null}
      </div>
      {props.children ? <div className="flex flex-wrap items-center gap-2">{props.children}</div> : null}
    </div>
  );
}
