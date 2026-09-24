import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./icons";
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
              "toast-in pointer-events-auto flex max-w-lg items-start gap-3 rounded-xl px-4 py-3 text-sm shadow-float",
              toast.tone === "error" ? "border border-expired/20 bg-expired-soft text-expired" : "bg-ink text-paper",
            )}
          >
            {toast.tone === "success" ? <Icon name="check" size={16} strokeWidth={2} className="mt-0.5 shrink-0 text-peach" /> : null}
            {toast.tone === "error" ? <Icon name="alert" size={16} strokeWidth={2} className="mt-0.5 shrink-0" /> : null}
            <span className="min-w-0 flex-1">{toast.message}</span>
            <button
              type="button"
              className="opacity-60 hover:opacity-100"
              aria-label="Fermer"
              onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}
            >
              <Icon name="close" size={14} strokeWidth={2} />
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
