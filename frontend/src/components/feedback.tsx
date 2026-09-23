import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Button, cx } from "./ui";

// --- toasts -------------------------------------------------------------------

type Toast = { id: number; message: string; tone: "info" | "error" | "success" };

const ToastContext = createContext<(message: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider(props: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const push = useCallback((message: string, tone: Toast["tone"] = "info") => {
    const id = nextId.current++;
    setToasts((current) => [...current.slice(-3), { id, message, tone }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), tone === "error" ? 8000 : 4500);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {props.children}
      <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4" aria-live="polite">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.tone === "error" ? "alert" : "status"}
            className={cx(
              "toast-in pointer-events-auto flex max-w-lg items-start gap-3 rounded-lg px-4 py-2.5 text-sm shadow-lg",
              toast.tone === "error" ? "bg-expired text-white" : "bg-ink text-white",
            )}
          >
            <span className="min-w-0 flex-1">{toast.message}</span>
            <button
              type="button"
              className="text-white/70 hover:text-white"
              aria-label="Fermer"
              onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
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
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onMouseDown={props.onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={props.title}
        className={cx("flex max-h-[90vh] w-full flex-col rounded-xl bg-paper shadow-xl", props.wide ? "max-w-3xl" : "max-w-md")}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="font-semibold">{props.title}</h2>
          <button type="button" className="rounded p-1 text-muted hover:text-ink" aria-label="Fermer" onClick={props.onClose}>
            ×
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{props.children}</div>
        {props.footer ? <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{props.footer}</div> : null}
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
