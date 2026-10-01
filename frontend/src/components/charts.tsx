import { useState, type ReactNode } from "react";
import { cx } from "./ui";

/*
 * Small charts for the statistics pages. Colors come from a validated
 * categorical order (blue, orange, aqua, yellow, magenta; adjacent pairs
 * pass the colorblind checks on white). Three of them sit under 3:1
 * against the page, so every chart also prints its values as text: the
 * legend or a table next to it, never color alone.
 */

export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"] as const;

export type Bar = {
  key: string;
  value: number | null;
  /** Short label under the bar (a date). */
  label: string;
  /** Fuller label for the tooltip (date and time). Defaults to `label`. */
  longLabel?: string;
  /** Tooltip lines: value first, then context. */
  details: { label: string; value: string }[];
};

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((candidate) => candidate * power >= value) ?? 10;
  return step * power;
}

/** One bar of the history chart; hover and focus make it the active one. */
function BarColumn(props: { bar: Bar; index: number; max: number; active: number | null; format: (value: number) => string; setActive: (index: number | null) => void }) {
  const { bar, index, max, active, setActive } = props;
  const ratio = bar.value === null ? 0 : bar.value / max;
  return (
    <button
      type="button"
      role="listitem"
      aria-label={`${bar.label} : ${bar.value === null ? "non mesuré" : props.format(bar.value)}`}
      className="group relative flex h-full min-w-0 flex-1 items-end justify-center outline-none"
      onMouseEnter={() => setActive(index)}
      onFocus={() => setActive(index)}
      onBlur={() => setActive(null)}
    >
      <span
        className={cx("block w-full max-w-7 rounded-t-[4px] transition-opacity", active !== null && active !== index && "opacity-45")}
        style={{ height: `${Math.max(bar.value ? 1.5 : 0, ratio * 100)}%`, background: bar.value === null ? "var(--color-line-strong)" : SERIES[0] }}
      />
    </button>
  );
}

/** The hover card of the active bar, on the side with room for it. */
function BarTooltip(props: { bar: Bar; index: number; count: number; format: (value: number) => string }) {
  const { bar, index, count } = props;
  return (
    <div
      className="pointer-events-none absolute top-0 z-10 w-max max-w-60 rounded-lg border border-line bg-paper px-3 py-2 text-xs shadow-lg"
      style={{
        left: `${((index + 0.5) / count) * 100}%`,
        transform: index > count / 2 ? "translateX(calc(-100% - 8px))" : "translateX(8px)",
      }}
      role="status"
    >
      <p className="font-semibold text-ink tabular">{bar.value === null ? "Non mesuré" : props.format(bar.value)}</p>
      <p className="text-muted">{bar.longLabel ?? bar.label}</p>
      <dl className="mt-1.5 grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5">
        {bar.details.map((detail) => (
          <div key={detail.label} className="contents">
            <dt className="text-muted">{detail.label}</dt>
            <dd className="text-right font-medium text-ink tabular">{detail.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** One series over time: a bar per run, a crosshair-style tooltip on hover and focus. */
export function BarHistory(props: { bars: Bar[]; format: (value: number) => string; title: string; height?: number }) {
  const [active, setActive] = useState<number | null>(null);
  const height = props.height ?? 160;
  const values = props.bars.map((bar) => bar.value ?? 0);
  const max = niceMax(Math.max(0, ...values));
  const ticks = [0, max / 2, max];
  const current = active === null ? null : props.bars[active];

  if (!props.bars.length) return <p className="py-8 text-center text-sm text-muted">Pas encore de lecture mesurée.</p>;

  return (
    <figure className="m-0">
      <div className="relative flex gap-2">
        {/* y axis */}
        <div className="relative w-12 shrink-0 text-right text-[11px] text-muted tabular" style={{ height }} aria-hidden>
          {ticks.map((tick) => (
            <span key={tick} className="absolute right-0 -translate-y-1/2" style={{ top: `${100 - (tick / max) * 100}%` }}>
              {tick === 0 ? "0" : props.format(tick)}
            </span>
          ))}
        </div>
        <div className="relative min-w-0 flex-1" style={{ height }}>
          {ticks.map((tick) => (
            <div key={tick} className={cx("absolute inset-x-0 border-t", tick === 0 ? "border-line-strong" : "border-line")} style={{ top: `${100 - (tick / max) * 100}%` }} aria-hidden />
          ))}
          <div className="absolute inset-0 flex items-end gap-[2px]" role="list" aria-label={props.title} onMouseLeave={() => setActive(null)}>
            {props.bars.map((bar, index) => (
              <BarColumn key={bar.key} bar={bar} index={index} max={max} active={active} format={props.format} setActive={setActive} />
            ))}
          </div>
          {current && active !== null ? <BarTooltip bar={current} index={active} count={props.bars.length} format={props.format} /> : null}
        </div>
      </div>
      <div className="ml-14 mt-1.5 flex justify-between text-[11px] text-muted" aria-hidden>
        <span>{props.bars[0].label}</span>
        {props.bars.length > 1 ? <span>{props.bars[props.bars.length - 1].label}</span> : null}
      </div>
    </figure>
  );
}

export type Segment = { key: string; label: string; value: number };

/** Parts of a whole on one horizontal bar, with a legend that carries every value as text. */
export function SplitBar(props: { segments: Segment[]; format: (value: number) => string; title: string }) {
  const [active, setActive] = useState<string | null>(null);
  const total = props.segments.reduce((sum, segment) => sum + segment.value, 0);
  if (total <= 0) return <p className="py-4 text-sm text-muted">Pas encore mesuré.</p>;
  return (
    <figure className="m-0">
      <div className="flex h-5 gap-[2px] overflow-hidden rounded-[4px]" role="img" aria-label={props.title}>
        {props.segments.map((segment, index) =>
          segment.value > 0 ? (
            <span
              key={segment.key}
              title={`${segment.label} : ${props.format(segment.value)}`}
              className={cx("h-full transition-opacity", active && active !== segment.key && "opacity-40")}
              style={{ width: `${(segment.value / total) * 100}%`, background: SERIES[index % SERIES.length] }}
              onMouseEnter={() => setActive(segment.key)}
              onMouseLeave={() => setActive(null)}
            />
          ) : null,
        )}
      </div>
      <ul className="mt-3 grid gap-1.5 text-sm">
        {props.segments.map((segment, index) => (
          <li
            key={segment.key}
            className={cx("flex min-w-0 items-center gap-2 transition-opacity", active && active !== segment.key && "opacity-50")}
            onMouseEnter={() => setActive(segment.key)}
            onMouseLeave={() => setActive(null)}
          >
            <span className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ background: SERIES[index % SERIES.length] }} aria-hidden />
            <span className="min-w-0 flex-1 truncate text-ink-soft">{segment.label}</span>
            <span className="font-medium tabular">{props.format(segment.value)}</span>
            <span className="w-11 text-right text-xs text-muted tabular">{Math.round((segment.value / total) * 100)} %</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

/** A thin proportion bar for table cells (one hue: it shows size, not identity). */
export function ShareBar(props: { ratio: number; children?: ReactNode }) {
  const ratio = Math.max(0, Math.min(1, props.ratio));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 min-w-12 flex-1 overflow-hidden rounded-full bg-canvas" aria-hidden>
        <div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: SERIES[0] }} />
      </div>
      {props.children}
    </div>
  );
}
