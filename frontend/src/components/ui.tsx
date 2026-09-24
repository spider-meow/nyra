import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { Link } from "react-router";
import { confidenceHelp, confidenceLabel, decisionLabel, statusLabel } from "../lib/format";
import type { Confidence, Decision, Status } from "../types";
import { Icon } from "./icons";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

export function buttonClass(variant: Variant = "secondary", size: Size = "md", className?: string): string {
  return cx(
    "inline-flex shrink-0 items-center justify-center gap-2 font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-40",
    size === "sm" && "h-9 rounded-[10px] px-3 text-[13.5px]",
    size === "md" && "h-10 rounded-xl px-4 text-sm",
    size === "lg" && "h-11 rounded-xl px-5 text-[14.5px]",
    variant === "primary" && "bg-ink text-paper hover:bg-ink-soft",
    variant === "secondary" && "border border-line-strong bg-paper text-ink hover:border-faint hover:bg-sunk",
    variant === "ghost" && "text-ink-soft hover:bg-side hover:text-ink",
    variant === "danger" && "border border-expired/25 bg-paper text-expired hover:bg-expired-soft",
    className,
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className, type = "button", ...rest },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...rest} />;
});

/** A navigation link that looks like a button. */
export function LinkButton(props: { to: string; children: ReactNode; variant?: Variant; size?: Size; className?: string }) {
  return (
    <Link to={props.to} className={buttonClass(props.variant, props.size, props.className)}>
      {props.children}
    </Link>
  );
}

const statusTone: Record<Status, string> = {
  expire: "bg-expired-soft text-expired",
  "<30j": "bg-urgent-soft text-urgent",
  "<90j": "bg-soon-soft text-soon",
  ok: "bg-ok-soft text-ok",
  inconnue: "bg-unknown-soft text-unknown",
};

export const statusDot: Record<Status, string> = {
  expire: "bg-expired",
  "<30j": "bg-urgent",
  "<90j": "bg-soon",
  ok: "bg-ok",
  inconnue: "bg-unknown",
};

export const statusText: Record<Status, string> = {
  expire: "text-expired",
  "<30j": "text-urgent",
  "<90j": "text-soon",
  ok: "text-ok",
  inconnue: "text-unknown",
};

/** Expiry: round and coloured. The only badge that uses the urgency ladder. */
export function StatusBadge(props: { status: Status; label?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap", statusTone[props.status])}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {props.label ?? statusLabel[props.status]}
    </span>
  );
}

const confidenceBars: Record<Confidence, number> = { haut: 3, moyen: 2, a_verifier: 1 };

/** Confidence: an outline with a three-bar gauge. */
export function ConfidenceBadge(props: { confidence: Confidence; detail?: string }) {
  const bars = confidenceBars[props.confidence];
  return (
    <span
      title={confidenceHelp[props.confidence]}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs whitespace-nowrap",
        props.confidence === "a_verifier" ? "border-dashed border-faint text-ink-soft" : "border-line-strong text-ink",
      )}
    >
      <span className="flex gap-0.5" aria-hidden>
        {[1, 2, 3].map((bar) => <span key={bar} className={cx("h-2.5 w-[3px] rounded-sm", bar <= bars ? "bg-bark" : "bg-line")} />)}
      </span>
      {confidenceLabel[props.confidence]}
      {props.detail ? <span className="text-muted">· {props.detail}</span> : null}
    </span>
  );
}

const decisionTone: Record<Decision, string> = {
  retenu: "bg-ink text-paper",
  ecarte: "bg-unknown-soft text-muted line-through decoration-1",
  traite: "bg-ok-soft text-ok",
};

/** Decision: a square-cornered block. */
export function DecisionBadge(props: { decision: Decision | null }) {
  if (!props.decision) return <span className="inline-flex rounded-md border border-dashed border-line-strong px-2 py-px text-xs whitespace-nowrap text-muted">À décider</span>;
  return <span className={cx("inline-flex rounded-md px-2 py-px text-xs font-medium whitespace-nowrap", decisionTone[props.decision])}>{decisionLabel[props.decision]}</span>;
}

export function Card(props: { children: ReactNode; className?: string; padded?: boolean }) {
  return (
    <section className={cx("rounded-2xl border border-line bg-paper", props.padded !== false && "p-6", props.className)}>
      {props.children}
    </section>
  );
}

export function PageHeader(props: { title: string; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {props.eyebrow ? <p className="mb-1.5 text-[13px] text-muted">{props.eyebrow}</p> : null}
        <h1 className="font-display text-[40px] leading-none md:text-5xl">{props.title}</h1>
        {props.description ? <p className="mt-2.5 max-w-2xl text-[14.5px] leading-relaxed text-muted">{props.description}</p> : null}
      </div>
      {props.actions ? <div className="flex flex-wrap items-center gap-2">{props.actions}</div> : null}
    </header>
  );
}

export function FieldLabel(props: { children: ReactNode; htmlFor?: string; hint?: ReactNode }) {
  return (
    <label htmlFor={props.htmlFor} className="mb-1.5 block text-[13px] font-medium text-ink-soft">
      {props.children}
      {props.hint ? <span className="ml-1 font-normal text-muted">{props.hint}</span> : null}
    </label>
  );
}

export const fieldClass =
  "h-10 w-full rounded-[10px] border border-line-strong bg-paper px-3.5 text-sm text-ink placeholder:text-faint outline-none transition-shadow focus:border-focus focus:ring-4 focus:ring-focus-soft disabled:bg-canvas disabled:text-muted";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cx(fieldClass, className)} {...rest} />;
});

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  const { className, ...rest } = props;
  return <select className={cx(fieldClass, "pr-8", className)} {...rest} />;
}

export function Checkbox(props: { checked: boolean; onChange: (value: boolean) => void; label: ReactNode; disabled?: boolean; hint?: ReactNode }) {
  return (
    <label className="flex items-start gap-2.5 text-sm">
      <input
        type="checkbox"
        className="mt-0.5 h-[18px] w-[18px] shrink-0 accent-ink"
        checked={props.checked}
        disabled={props.disabled}
        onChange={(event) => props.onChange(event.target.checked)}
      />
      <span>
        {props.label}
        {props.hint ? <span className="block text-[13px] text-muted">{props.hint}</span> : null}
      </span>
    </label>
  );
}

export function EmptyState(props: { title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-line-strong bg-sunk px-6 py-12 text-center">
      <img src="/nyra-logo.png" alt="" width={32} height={32} className="mx-auto mb-3 opacity-90" />
      <p className="font-display text-2xl">{props.title}</p>
      {props.body ? <p className="mx-auto mt-1 max-w-md text-sm text-muted">{props.body}</p> : null}
      {props.action ? <div className="mt-4 flex justify-center">{props.action}</div> : null}
    </div>
  );
}

export function Spinner(props: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-strong border-t-bark" aria-hidden />
      {props.label ?? "Chargement…"}
    </span>
  );
}

export function Skeleton(props: { className?: string }) {
  return <div className={cx("animate-pulse rounded-xl bg-line/70", props.className)} aria-hidden />;
}

export function Thumb(props: { src: string; size?: number; className?: string; alt?: string }) {
  const size = props.size ?? 44;
  return props.src ? (
    <img
      src={props.src}
      alt={props.alt ?? ""}
      loading="lazy"
      width={size}
      height={size}
      style={{ width: size, height: size }}
      className={cx("shrink-0 rounded-[9px] bg-side object-cover", props.className)}
    />
  ) : (
    <span style={{ width: size, height: size }} className={cx("block shrink-0 rounded-[9px] bg-side", props.className)} aria-hidden />
  );
}

export function Kbd(props: { children: ReactNode }) {
  return <kbd className="inline-flex min-w-5 items-center justify-center rounded-md border border-line-strong bg-paper px-1.5 py-px font-sans text-[11px] text-muted">{props.children}</kbd>;
}

export function Stat(props: { value: ReactNode; label: string; tone?: "alert" | "warn"; hint?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-col rounded-2xl border border-line bg-paper p-5">
      <p className={cx("font-display text-5xl leading-none tabular", props.tone === "alert" && "text-expired", props.tone === "warn" && "text-urgent")}>
        {props.value}
      </p>
      <p className="mt-2 text-sm text-ink-soft">{props.label}</p>
      {props.children}
      {props.hint ? <p className="mt-1 text-[13px] text-muted">{props.hint}</p> : null}
    </div>
  );
}

/** A filter pill. The dot, when given, is an urgency colour. */
export function Chip(props: { active: boolean; onClick: () => void; children: ReactNode; dot?: Status; count?: number }) {
  return (
    <button
      type="button"
      aria-pressed={props.active}
      onClick={props.onClick}
      className={cx(
        "inline-flex h-[34px] shrink-0 items-center gap-2 rounded-full px-3.5 text-[13px] whitespace-nowrap transition-colors",
        props.active ? "bg-ink font-medium text-paper" : "border border-line-strong bg-paper text-ink hover:border-faint",
      )}
    >
      {props.dot ? <span className={cx("h-1.5 w-1.5 rounded-full", statusDot[props.dot])} aria-hidden /> : null}
      {props.children}
      {props.count !== undefined ? <span className={cx("tabular", props.active ? "text-paper/70" : "text-muted")}>{props.count}</span> : null}
    </button>
  );
}

/** A two- or three-way toggle on a sunken track. */
export function Segmented<T extends string>(props: { value: T; options: { value: T; label: string }[]; onChange: (value: T) => void; label: string }) {
  return (
    <div className="inline-flex rounded-[10px] bg-side p-[3px]" role="group" aria-label={props.label}>
      {props.options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={props.value === option.value}
          onClick={() => props.onChange(option.value)}
          className={cx(
            "h-8 rounded-lg px-3 text-[13px] whitespace-nowrap",
            props.value === option.value ? "bg-paper font-medium text-ink shadow-lift" : "text-muted hover:text-ink",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** A rounded search field with a leading magnifier. */
export function SearchField(props: { id: string; value: string; onChange: (value: string) => void; placeholder: string; className?: string }) {
  return (
    <label htmlFor={props.id} className={cx("flex h-10 items-center gap-2.5 rounded-full border border-line-strong bg-paper px-4 transition-shadow focus-within:border-focus focus-within:ring-4 focus-within:ring-focus-soft", props.className)}>
      <Icon name="search" size={16} className="shrink-0 text-muted" />
      <span className="sr-only">{props.placeholder}</span>
      <input
        id={props.id}
        type="search"
        placeholder={props.placeholder}
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
        className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-faint"
      />
    </label>
  );
}
