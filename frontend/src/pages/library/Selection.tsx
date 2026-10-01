import { useState } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { errorMessage } from "../../lib/api";
import { formatDate, plural, splitTags } from "../../lib/format";
import { useLibraryMutations } from "../../lib/queries";

/** Date and tag edits applied to the selected visuals. Lives in the page, not in the bar: the typed values survive a deselection. */
export function useBulkEdit(selected: Set<string>, clear: () => void) {
  const { setExpiry, setTags } = useLibraryMutations();
  const toast = useToast();
  const [date, setDate] = useState("");
  const [tag, setTag] = useState("");

  function applyDate() {
    setExpiry.mutate(
      { filenames: [...selected], expiry_date: date },
      {
        onSuccess: (result) => {
          toast.show({
            tone: "success",
            message: date ? `Échéance fixée au ${formatDate(date)}` : "Échéance retirée",
            description: plural(result.updated, "visuel mis à jour", "visuels mis à jour"),
          });
          clear();
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  function applyTags(mode: "add" | "remove") {
    const tags = splitTags(tag);
    if (!tags.length) return;
    setTags.mutate(
      { filenames: [...selected], [mode]: tags },
      {
        onSuccess: (result) => {
          toast.show({
            tone: "success",
            message: mode === "add" ? "Tags ajoutés" : "Tags retirés",
            description: plural(result.updated, "visuel mis à jour", "visuels mis à jour"),
          });
          setTag("");
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  return { date, setDate, tag, setTag, applyDate, applyTags, dateBusy: setExpiry.isPending, tagsBusy: setTags.isPending };
}

/** Asks for confirmation, then deletes visuels by filename. `onRemoved` runs once they are gone. */
export function useRemoveReferences(onRemoved: () => void) {
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
        onRemoved();
      },
      onError: (error) => toast.update(pending, { tone: "error", message: "La suppression n'a pas abouti", description: errorMessage(error) }),
    });
  }

  return { removeNames, removing: remove.isPending };
}

type Props = {
  selected: Set<string>;
  bulk: ReturnType<typeof useBulkEdit>;
  removing: boolean;
  onRemove: (names: string[]) => Promise<void>;
  onClear: () => void;
};

/** Floating bar over the selected visuels: deadline, tags, delete. */
export function SelectionBar({ selected, bulk, removing, onRemove, onClear }: Props) {
  return (
    <div className="fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-3xl flex-wrap items-center gap-3 rounded-2xl bg-ink px-4 py-2.5 text-sm text-paper shadow-float md:left-[calc(256px+3.5rem)]" role="region" aria-label="Actions sur la sélection">
      <span className="font-medium">{plural(selected.size, "sélectionné")}</span>
      <span className="flex items-center gap-2">
        <label htmlFor="bulk-date" className="text-white/70">Échéance</label>
        <input id="bulk-date" type="date" value={bulk.date} onChange={(event) => bulk.setDate(event.target.value)} className="h-9 rounded-[10px] border border-white/20 bg-white/10 px-2.5 text-paper [color-scheme:dark]" />
        <button type="button" className="h-9 rounded-[10px] bg-peach px-3.5 text-[13.5px] font-medium text-bark-800 hover:bg-[#f9bd98] disabled:cursor-progress disabled:opacity-60" onClick={bulk.applyDate} disabled={bulk.dateBusy}>
          {bulk.dateBusy ? "Application…" : "Appliquer"}
        </button>
      </span>
      <span className="flex items-center gap-2">
        <label htmlFor="bulk-tag" className="text-white/70">Tags</label>
        <input
          id="bulk-tag"
          list="library-tags"
          value={bulk.tag}
          maxLength={120}
          placeholder="magnum, classic"
          onChange={(event) => bulk.setTag(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") bulk.applyTags("add"); }}
          className="h-9 w-40 rounded-[10px] border border-white/20 bg-white/10 px-2.5 text-paper placeholder:text-white/40"
        />
        <button type="button" className="h-9 rounded-[10px] bg-peach px-3.5 text-[13.5px] font-medium text-bark-800 hover:bg-[#f9bd98] disabled:cursor-not-allowed disabled:opacity-50" onClick={() => bulk.applyTags("add")} disabled={bulk.tagsBusy || !splitTags(bulk.tag).length}>
          Ajouter
        </button>
        <button type="button" className="h-9 rounded-[10px] px-3 text-[13.5px] text-paper hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => bulk.applyTags("remove")} disabled={bulk.tagsBusy || !splitTags(bulk.tag).length}>
          Retirer
        </button>
      </span>
      <button type="button" className="h-9 rounded-[10px] px-3 text-[13.5px] text-[#f4b3a8] hover:bg-white/10 disabled:cursor-progress disabled:opacity-60" disabled={removing} onClick={() => void onRemove([...selected])}>
        {removing ? "Suppression…" : "Supprimer"}
      </button>
      <button type="button" className="ml-auto text-white/70 hover:text-white" onClick={onClear}>Tout désélectionner</button>
    </div>
  );
}
