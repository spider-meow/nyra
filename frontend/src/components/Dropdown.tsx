import { Children, isValidElement, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Icon } from "./icons";
import { cx, fieldClass } from "./ui";

type Option = { value: string; label: ReactNode };

type DropdownProps = {
  options: Option[];
  value: string;
  onChange: (value: string) => void;
  /** Replaces the selected label inside the button (the organization and brand switchers show more than a label). */
  trigger?: ReactNode;
  id?: string;
  "aria-label"?: string;
  disabled?: boolean;
  /** Classes of the button. */
  className?: string;
};

/** Open state, the highlighted option and the keyboard, so the component below only draws. */
function useListbox(props: Pick<DropdownProps, "options" | "value" | "onChange">) {
  const { options, value, onChange } = props;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const show = () => {
    setActive(Math.max(0, options.findIndex((option) => option.value === value)));
    setOpen(true);
  };
  const pick = (index: number) => {
    setOpen(false);
    if (options[index] && options[index].value !== value) onChange(options[index].value);
  };

  function onKeyDown(event: KeyboardEvent) {
    const move: Record<string, number> = { ArrowDown: active + 1, ArrowUp: active - 1, Home: 0, End: options.length - 1 };
    if (event.key === "Escape" || event.key === "Tab") return setOpen(false);
    if (event.key in move) {
      event.preventDefault();
      if (!open) return show();
      return setActive(Math.min(options.length - 1, Math.max(0, move[event.key])));
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) pick(active);
      else show();
    }
  }

  return { open, active, setActive, root, show, pick, onKeyDown, toggle: () => (open ? setOpen(false) : show()) };
}

/** A list that opens under its button, in the colours of the app (the browser's own list cannot be styled). */
export function Dropdown(props: DropdownProps) {
  const list = useListbox(props);
  const listId = useId();
  const selected = props.options.find((option) => option.value === props.value);
  return (
    <div ref={list.root} className="relative" onKeyDown={list.onKeyDown}>
      <button
        type="button"
        id={props.id}
        aria-label={props["aria-label"]}
        aria-haspopup="listbox"
        aria-expanded={list.open}
        aria-controls={list.open ? listId : undefined}
        disabled={props.disabled}
        onClick={list.toggle}
        className={cx("flex w-full items-center gap-2 text-left disabled:cursor-not-allowed", props.className)}
      >
        <span className="min-w-0 flex-1 truncate">{props.trigger ?? selected?.label}</span>
        <Icon name="chevrons" size={14} className="shrink-0 text-muted" />
      </button>
      {list.open ? (
        <ul id={listId} role="listbox" className="absolute top-full right-0 left-0 z-50 mt-1 max-h-64 min-w-max overflow-auto rounded-md border border-ink bg-paper p-1 shadow-float">
          {props.options.map((option, index) => (
            <li
              key={option.value}
              role="option"
              aria-selected={option.value === props.value}
              onMouseEnter={() => list.setActive(index)}
              onClick={() => list.pick(index)}
              className={cx(
                "flex cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-sm",
                index === list.active && "bg-sunk",
                option.value === props.value && "bg-yellow font-medium",
              )}
            >
              <span className="min-w-0 flex-1">{option.label}</span>
              {option.value === props.value ? <Icon name="check" size={14} strokeWidth={2} /> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Same call as a native select: <option> children, `onChange` receiving an event-like `{ target: { value } }`. */
export function Select(props: {
  value: string;
  onChange: (event: { target: { value: string } }) => void;
  children: ReactNode;
  id?: string;
  "aria-label"?: string;
  disabled?: boolean;
  className?: string;
}) {
  const options = Children.toArray(props.children).flatMap((child): Option[] =>
    isValidElement<{ value?: string; children?: ReactNode }>(child) && child.type === "option"
      ? [{ value: String(child.props.value ?? ""), label: child.props.children }]
      : [],
  );
  return (
    <Dropdown
      options={options}
      value={props.value}
      id={props.id}
      aria-label={props["aria-label"]}
      disabled={props.disabled}
      onChange={(value) => props.onChange({ target: { value } })}
      className={cx(fieldClass.replace("h-10 ", ""), "min-h-10 py-2 pr-3", props.className)}
    />
  );
}
