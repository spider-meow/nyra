import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon } from "./icons";
import { Button, cx } from "./ui";

// --- toasts -------------------------------------------------------------------

type Tone = "info" | "success" | "error" | "loading";

export type ToastInput = {
  message: string;
  /** A second, quieter line: what happens next, or why it failed. */
  description?: string;
  tone?: Tone;
  /** 0 to 1: a thin bar under the message; null: work in progress of unknown length. */
  progress?: number | null;
  /** `keep`: the toast stays after the click (it will be updated). */
  action?: { label: string; onClick: () => void; keep?: boolean; disabled?: boolean };
  /** Milliseconds before it goes away; loading toasts stay until updated. */
  duration?: number;
};

type Toast = ToastInput & { id: number; tone: Tone };

export type Toaster = {
  /** toast("Adresse ajoutée.", "success") */
  (message: string, tone?: Exclude<Tone, "loading">): number;
  show: (input: ToastInput) => number;
  /** Stays on screen, with a spinner, until `update` gives it a final tone. */
  loading: (message: string, description?: string) => number;
  update: (id: number, input: Partial<ToastInput>) => void;
  dismiss: (id: number) => void;
};

const ToastContext = createContext<Toaster | null>(null);

function defaultDuration(toast: Toast): number | null {
  if (toast.tone === "loading") return null;
  if (toast.duration) return toast.duration;
  if (toast.tone === "error") return 9000;
  return toast.action ? 7000 : 4500;
}

export function ToastProvider(props: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());
  const current = useRef<Toast[]>([]);
  current.current = toasts;

  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    current.current = current.current.filter((toast) => toast.id !== id);
    setToasts(current.current);
  }, []);

  const schedule = useCallback(
    (toast: Toast) => {
      window.clearTimeout(timers.current.get(toast.id));
      const delay = defaultDuration(toast);
      if (delay !== null) timers.current.set(toast.id, window.setTimeout(() => dismiss(toast.id), delay));
    },
    [dismiss],
  );

  const toaster = useMemo<Toaster>(() => {
    const show = (input: ToastInput) => {
      const toast: Toast = { ...input, id: nextId.current++, tone: input.tone ?? "info" };
      // At most four on screen: the oldest finished one makes room, never a task still in progress.
      let list = current.current;
      if (list.length >= 4) {
        const oldest = list.find((item) => item.tone !== "loading");
        if (oldest) list = list.filter((item) => item !== oldest);
      }
      current.current = [...list, toast];
      setToasts(current.current);
      schedule(toast);
      return toast.id;
    };
    const fn = ((message: string, tone: Exclude<Tone, "loading"> = "info") => show({ message, tone })) as Toaster;
    fn.show = show;
    fn.loading = (message, description) => show({ message, description, tone: "loading" });
    fn.update = (id, input) => {
      const existing = current.current.find((toast) => toast.id === id);
      const next: Toast = existing ? { ...existing, ...input } : { message: "", ...input, id, tone: input.tone ?? "info" };
      const list = existing ? current.current.map((toast) => (toast.id === id ? next : toast)) : [...current.current.slice(-3), next];
      // Two updates in the same tick must build on each other.
      current.current = list;
      setToasts(list);
      if (!existing || input.tone) schedule(next);
    };
    fn.dismiss = dismiss;
    return fn;
  }, [dismiss, schedule]);

  return (
    <ToastContext.Provider value={toaster}>
      {props.children}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4 sm:inset-x-auto sm:right-4 sm:items-end"
        aria-live="polite"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.tone === "error" ? "alert" : "status"}
            onMouseEnter={() => window.clearTimeout(timers.current.get(toast.id))}
            onMouseLeave={() => schedule(toast)}
            className={cx(
              "toast-in pointer-events-auto w-full max-w-sm overflow-hidden rounded-xl text-sm shadow-float",
              toast.tone === "error" ? "border border-expired/20 bg-expired-soft text-expired" : "bg-ink text-paper",
            )}
          >
            <div className="flex items-start gap-3 px-4 py-3">
              <ToastIcon tone={toast.tone} />
              <div className="min-w-0 flex-1">
                <p className="font-medium leading-5">{toast.message}</p>
                {toast.description ? <p className="mt-0.5 text-[13px] leading-5 opacity-75">{toast.description}</p> : null}
                {toast.action ? (
                  <button
                    type="button"
                    className="mt-1.5 text-[13px] font-medium underline underline-offset-2 hover:opacity-80 disabled:cursor-default disabled:no-underline disabled:opacity-60"
                    disabled={toast.action.disabled}
                    onClick={() => {
                      toast.action?.onClick();
                      if (!toast.action?.keep) dismiss(toast.id);
                    }}
                  >
                    {toast.action.label}
                  </button>
                ) : null}
              </div>
              {toast.tone !== "loading" ? (
                <button type="button" className="mt-0.5 opacity-60 hover:opacity-100" aria-label="Fermer" onClick={() => dismiss(toast.id)}>
                  <Icon name="close" size={14} strokeWidth={2} />
                </button>
              ) : null}
            </div>
            {toast.progress === null ? (
              <div className="h-1 overflow-hidden bg-paper/10" role="progressbar" aria-valuemin={0} aria-valuemax={100}>
                <div className="progress-indeterminate h-full w-1/4 bg-peach" />
              </div>
            ) : toast.progress !== undefined ? (
              <div
                className="h-1 bg-paper/10"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(toast.progress * 100)}
              >
                <div className="h-full bg-peach transition-[width] duration-300" style={{ width: `${Math.max(3, Math.min(100, toast.progress * 100))}%` }} />
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastIcon(props: { tone: Tone }) {
  if (props.tone === "loading") {
    return <span aria-hidden className="mt-0.5 h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-paper/30 border-t-peach" />;
  }
  if (props.tone === "success") return <Icon name="check" size={16} strokeWidth={2} className="mt-0.5 shrink-0 text-peach" />;
  if (props.tone === "error") return <Icon name="alert" size={16} strokeWidth={2} className="mt-0.5 shrink-0" />;
  return <Icon name="alert" size={16} strokeWidth={2} className="mt-0.5 shrink-0 opacity-70" />;
}

// Without a provider (a hot reload that recreated this module, a test), toasts
// fall back to the console instead of taking the page down.
const consoleToaster: Toaster = Object.assign((message: string) => (console.info(message), 0), {
  show: (input: ToastInput) => (console.info(input.message, input.description ?? ""), 0),
  loading: (message: string) => (console.info(message), 0),
  update: () => {},
  dismiss: () => {},
});

export function useToast(): Toaster {
  return useContext(ToastContext) ?? consoleToaster;
}

// --- dialogs ---------------------------------------------------------------------

export function Modal(props: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!props.open) return;
    const previous = document.activeElement as HTMLElement | null;
    const target =
      panel.current?.querySelector<HTMLElement>("[data-autofocus]") ??
      panel.current?.querySelector<HTMLElement>(".overflow-y-auto input, .overflow-y-auto select, .overflow-y-auto button");
    target?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") props.onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      previous?.focus();
    };
    // Only re-run on open/close.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open]);

  if (!props.open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-[#1f1a15]/45 p-4 backdrop-blur-[2px]" onMouseDown={props.onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={props.title}
        className={cx("flex max-h-[90vh] w-full flex-col rounded-[20px] bg-paper shadow-float", props.wide ? "max-w-3xl" : "max-w-md")}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-4 px-6 pt-5 pb-3">
          <h2 className="min-w-0 truncate text-lg font-semibold tracking-tight">{props.title}</h2>
          <button type="button" className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-muted hover:bg-side hover:text-ink" aria-label="Fermer" onClick={props.onClose}>
            <Icon name="close" size={16} strokeWidth={2} />
          </button>
        </div>
        <div className="overflow-y-auto px-6 pb-5">{props.children}</div>
        {props.footer ? <div className="flex justify-end gap-2 border-t border-line bg-sunk px-6 py-3.5 rounded-b-[20px]">{props.footer}</div> : null}
      </div>
    </div>
  );
}

type ConfirmRequest = { title: string; body: ReactNode; confirm: string; danger?: boolean; resolve: (ok: boolean) => void };

const ConfirmContext = createContext<(request: Omit<ConfirmRequest, "resolve">) => Promise<boolean>>(async () => false);

export function ConfirmProvider(props: { children: ReactNode }) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const ask = useCallback(
    (input: Omit<ConfirmRequest, "resolve">) => new Promise<boolean>((resolve) => setRequest({ ...input, resolve })),
    [],
  );
  const close = (ok: boolean) => {
    request?.resolve(ok);
    setRequest(null);
  };
  return (
    <ConfirmContext.Provider value={ask}>
      {props.children}
      <Modal
        open={request !== null}
        onClose={() => close(false)}
        title={request?.title ?? ""}
        footer={
          <>
            <Button onClick={() => close(false)}>Annuler</Button>
            <Button variant={request?.danger ? "danger" : "primary"} data-autofocus onClick={() => close(true)}>
              {request?.confirm}
            </Button>
          </>
        }
      >
        <div className="text-sm text-ink-soft">{request?.body}</div>
      </Modal>
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  return useContext(ConfirmContext);
}
