import { Suspense, lazy, useRef, useState } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { plural } from "../../lib/format";
import { useLibraryMutations } from "../../lib/queries";
import type { LabelInfo, LibraryItem } from "../../types";
import type { CollectionIndex } from "./collections";

/** Asks for confirmation, then deletes visuels by filename. `onRemoved` runs once they are gone. */
export function useRemoveReferences(onRemoved: (names: string[]) => void) {
  const { remove } = useLibraryMutations();
  const toast = useToast();
  const confirm = useConfirm();

  async function removeNames(names: string[]) {
    const ok = await confirm({
      title: names.length > 1 ? `Supprimer ${names.length} visuels ?` : "Supprimer ce visuel ?",
      body: (
        <>
          <p>{names.length > 1 ? "Ils seront retirés" : `« ${names[0]} » sera retiré`} de la surveillance, avec les correspondances et décisions associées.</p>
          <p className="mt-2 font-medium">Cette action est définitive.</p>
        </>
      ),
      confirm: "Supprimer",
      danger: true,
    });
    if (!ok) return;
    const pending = toast.loading(names.length > 1 ? `Suppression de ${names.length} visuels…` : "Suppression du visuel…");
    remove.mutate(names, {
      onSuccess: (result) => {
        toast.update(pending, { tone: "success", message: result.deleted > 1 ? `${result.deleted} visuels supprimés` : "Visuel supprimé" });
        onRemoved(names);
      },
      onError: (error) => toast.update(pending, { tone: "error", message: "La suppression n'a pas abouti", description: errorMessage(error) }),
    });
  }

  return { removeNames, removing: remove.isPending };
}

/** Loaded when the Actions menu is first opened: the menu and its dialogs are not part of the library page's first load. */
const SelectionActions = lazy(() => import("./SelectionActions").then((module) => ({ default: module.SelectionActions })));

type HeaderProps = {
  items: LibraryItem[];
  /** How many visuals the filters leave: what "Tout sélectionner" takes. */
  visibleCount: number;
  folders: CollectionIndex;
  labels: LabelInfo[];
  onSelectAll: () => void;
  onClear: () => void;
  removing: boolean;
  onRemove: (names: string[]) => Promise<void>;
};

/**
 * The selection is a mode: this header takes the place of the page title (same height, so nothing moves and nothing floats over the
 * cards), says how many visuals are ticked, and has one button, Actions, for everything that can be done to them.
 */
export function SelectionHeader({ items, visibleCount, folders, labels, onSelectAll, onClear, removing, onRemove }: HeaderProps) {
  const [menu, setMenu] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const closeMenu = () => {
    setMenu(false);
    button.current?.focus();
  };
  return (
    <div className="sticky top-2 z-30 mb-6 flex min-h-16 items-center gap-2 rounded-2xl border border-ink bg-paper px-2 py-2 shadow-lift sm:px-3" role="region" aria-label="Sélection">
      <button type="button" className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-ink hover:bg-sunk" aria-label="Quitter la sélection" onClick={onClear}>
        <Icon name="close" size={20} />
      </button>
      <div className="min-w-0 flex-1 leading-tight">
        <p className="truncate font-display text-xl sm:text-2xl" aria-live="polite">{plural(items.length, "sélectionné")}</p>
        {items.length < visibleCount ? (
          <button type="button" className="whitespace-nowrap text-[13px] text-ink-soft underline underline-offset-2" onClick={onSelectAll}>Tout sélectionner ({visibleCount.toLocaleString("fr-FR")})</button>
        ) : null}
      </div>
      <div className="relative">
        <button
          ref={button}
          type="button"
          aria-haspopup="menu"
          aria-expanded={menu}
          className={cx("flex h-11 items-center gap-2 rounded-xl px-4 text-[14px] font-medium", menu ? "bg-yellow text-ink" : "bg-ink text-white hover:bg-accent-hover")}
          onClick={() => {
            setLoaded(true);
            setMenu(!menu);
          }}
        >
          Actions <Icon name="down" size={16} />
        </button>
        {loaded ? (
          <Suspense fallback={null}>
            <SelectionActions open={menu} onClose={closeMenu} items={items} folders={folders} labels={labels} onDone={onClear} removing={removing} onRemove={onRemove} />
          </Suspense>
        ) : null}
      </div>
    </div>
  );
}
