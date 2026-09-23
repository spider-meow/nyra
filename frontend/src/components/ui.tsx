import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { Link } from "react-router";
import { confidenceHelp, confidenceLabel, decisionLabel, statusLabel } from "../lib/format";
import type { Confidence, Decision, Status } from "../types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function buttonClass(variant: Variant = "secondary", size: "sm" | "md" = "md", className?: string): string {
  return cx(
    "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-45",
    size === "sm" ? "h-8 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
    variant === "primary" && "bg-ink text-white hover:bg-ink-soft",
    variant === "secondary" && "border border-line-strong bg-paper text-ink hover:bg-canvas",
    variant === "ghost" && "text-ink-soft hover:bg-canvas hover:text-ink",
    variant === "danger" && "border border-line-strong bg-paper text-expired hover:bg-expired-soft",
    className,
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className, type = "button", ...rest },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...rest} />;
});

/** A navigation link that looks like a button. */
export function LinkButton(props: { to: string; children: ReactNode; variant?: Variant; size?: "sm" | "md"; className?: string }) {
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

export function StatusBadge(props: { status: Status; label?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", statusTone[props.status])}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {props.label ?? statusLabel[props.status]}
    </span>
  );
}

export function ConfidenceBadge(props: { confidence: Confidence }) {
  const tone = props.confidence === "a_verifier" ? "border-dashed text-muted" : "text-ink-soft";
  return (
    <span title={confidenceHelp[props.confidence]} className={cx("inline-flex rounded-full border border-line-strong px-2 py-0.5 text-xs whitespace-nowrap", tone)}>
      {confidenceLabel[props.confidence]}
    </span>
  );
}

const decisionTone: Record<Decision, string> = {
  retenu: "bg-urgent-soft text-urgent",
  ecarte: "bg-unknown-soft text-muted line-through decoration-1",
  traite: "bg-ok-soft text-ok",
};

export function DecisionBadge(props: { decision: Decision | null }) {
  if (!props.decision) return <span className="text-xs text-faint">À décider</span>;
  return <span className={cx("inline-flex rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", decisionTone[props.decision])}>{decisionLabel[props.decision]}</span>;
}

export function Card(props: { children: ReactNode; className?: string; padded?: boolean }) {
  return (
    <section className={cx("rounded-xl border border-line bg-paper", props.padded !== false && "p-5", props.className)}>
      {props.children}
    </section>
  );
}

export function PageHeader(props: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{props.title}</h1>
        {props.description ? <p className="mt-1 max-w-2xl text-sm text-muted">{props.description}</p> : null}
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

const fieldClass =
  "h-9 w-full rounded-lg border border-line-strong bg-paper px-3 text-sm text-ink placeholder:text-faint outline-none focus:border-focus focus:ring-2 focus:ring-focus-soft disabled:bg-canvas disabled:text-muted";

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
        className="mt-0.5 h-4 w-4 accent-ink"
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
    <div className="rounded-xl border border-dashed border-line-strong bg-paper px-6 py-10 text-center">
      <p className="font-medium">{props.title}</p>
      {props.body ? <p className="mx-auto mt-1 max-w-md text-sm text-muted">{props.body}</p> : null}
      {props.action ? <div className="mt-4 flex justify-center">{props.action}</div> : null}
    </div>
  );
}

export function Spinner(props: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-strong border-t-ink" aria-hidden />
      {props.label ?? "Chargement…"}
    </span>
  );
}

export function Skeleton(props: { className?: string }) {
  return <div className={cx("animate-pulse rounded-lg bg-line/70", props.className)} aria-hidden />;
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
      className={cx("shrink-0 rounded-md border border-line bg-canvas object-cover", props.className)}
    />
  ) : (
    <span style={{ width: size, height: size }} className={cx("block shrink-0 rounded-md border border-line bg-canvas", props.className)} aria-hidden />
  );
}

export function Kbd(props: { children: ReactNode }) {
  return <kbd className="rounded border border-line-strong bg-canvas px-1.5 py-px font-sans text-[11px] text-muted">{props.children}</kbd>;
}

export function Stat(props: { value: ReactNode; label: string; tone?: "alert" | "warn"; hint?: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-paper p-4">
      <p className={cx("text-3xl font-semibold tracking-tight tabular", props.tone === "alert" && "text-expired", props.tone === "warn" && "text-urgent")}>
        {props.value}
      </p>
      <p className="mt-1 text-sm text-ink-soft">{props.label}</p>
      {props.hint ? <p className="mt-0.5 text-xs text-muted">{props.hint}</p> : null}
    </div>
  );
}
