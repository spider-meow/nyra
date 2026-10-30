import { useToast } from "../../components/feedback";

/** Puts an address on the clipboard and says so; where the clipboard is blocked, says that and leaves the browser's own address bar to copy from. */
export function useCopyLink() {
  const toast = useToast();
  return async (url: string, message = "Lien copié") => {
    try {
      await navigator.clipboard.writeText(url);
      toast.show({ tone: "success", message, duration: 2500 });
    } catch {
      // The page was opened where the clipboard is blocked: nothing was copied, and the person is told so instead of believing it was.
      toast.show({ tone: "error", message: "Copie impossible", description: "Copiez l'adresse dans la barre du navigateur." });
    }
  };
}
