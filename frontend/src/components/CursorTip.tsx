import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { usePresence } from "./feedback";
import { cx } from "./ui";

/**
 * Explains, next to the pointer, why what it is over cannot be used. `tip` empty = no bubble.
 * A disabled button swallows mouse events, so the wrapper takes them instead.
 */
export function CursorTip(props: { tip?: ReactNode; children: ReactNode; className?: string }) {
  const [shown, setShown] = useState(false);
  const presence = usePresence(shown);
  const bubble = useRef<HTMLDivElement>(null);
  const pointer = useRef({ x: 0, y: 0 });
  const active = Boolean(props.tip);

  function place() {
    const el = bubble.current;
    if (!el) return;
    const margin = 8;
    const x = Math.min(Math.max(pointer.current.x, margin + el.offsetWidth / 2), window.innerWidth - margin - el.offsetWidth / 2);
    // Above the pointer; below it when the top of the window is too close.
    const above = pointer.current.y - el.offsetHeight - 16 >= margin;
    const y = above ? pointer.current.y - el.offsetHeight - 16 : pointer.current.y + 24;
    el.style.transform = `translate(${x - el.offsetWidth / 2}px, ${y}px)`;
  }

  useLayoutEffect(() => {
    if (shown) place();
  }, [shown]);

  if (!active) return <>{props.children}</>;
  return (
    <div
      className={cx("flex flex-col [&_button:disabled]:pointer-events-none", props.className)}
      style={{ cursor: "not-allowed" }}
      onMouseEnter={(event) => {
        pointer.current = { x: event.clientX, y: event.clientY };
        setShown(true);
      }}
      onMouseMove={(event) => {
        pointer.current = { x: event.clientX, y: event.clientY };
        place();
      }}
      onMouseLeave={() => setShown(false)}
    >
      {props.children}
      {presence.mounted
        ? createPortal(
            <div ref={bubble} role="tooltip" className="pointer-events-none fixed top-0 left-0 z-50 max-w-[260px]">
              <div onAnimationEnd={presence.onAnimationEnd} className={cx(presence.leaving ? "fade-out" : "tip-in", "rounded-lg bg-ink px-3 py-2 text-[13px] leading-snug text-paper shadow-float")}>{props.tip}</div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
