import { useToast } from "../../components/feedback";
import { SettingsBlock } from "../../components/SettingsBlock";
import { Button, Checkbox, EmptyState, Skeleton, Thumb } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { hostOf } from "../../lib/format";
import { useLogoMutations, useLogos, type RecognisedLogo } from "../../lib/queries";

/** The last part of an address, readable: "LXIII-NEW-LOGO.png" out of "https://…/LXIII-NEW-LOGO.png?v=1". */
function fileName(url: string): string {
  try {
    return decodeURIComponent(new URL(url).pathname.split("/").filter(Boolean).pop() ?? "") || hostOf(url);
  } catch {
    return url; // an address that does not parse is shown as it is
  }
}

/** Logos: leave the images recognised as logos out of the comparison and out of "Droits non vérifiés", and put one back. */
export function Logos() {
  const logos = useLogos();
  const { setEnabled, keep } = useLogoMutations();
  const toast = useToast();
  const fail = (message: string) => (error: unknown) => toast.show({ tone: "error", message, description: errorMessage(error) });
  if (logos.isLoading) return <Skeleton className="h-40" />;
  if (logos.error || !logos.data) return <EmptyState title="Impossible de charger les logos" body={errorMessage(logos.error)} />;
  const { enabled, items } = logos.data;

  return (
    <>
      <SettingsBlock
        title="Mettre les logos à part"
        description="Nyra reconnaît une image qui est un logo à son aspect d'ensemble : une photo où un logo apparaît n'est jamais écartée. Désactivé par défaut."
      >
        <div className="grid gap-4 p-5">
          <Checkbox
            checked={enabled}
            disabled={setEnabled.isPending}
            onChange={(value) =>
              setEnabled.mutate(value, {
                onSuccess: () => toast.show({ tone: "success", message: value ? "Les logos sont mis à part" : "Les logos sont de nouveau comparés", description: "Une comparaison vient d'être programmée." }),
                onError: fail("Le réglage n'a pas été enregistré"),
              })
            }
            label="Ne pas comparer les logos"
            hint="Les images reconnues comme logos ne sont plus proposées dans « Droits non vérifiés » ni comparées à la bibliothèque. Vous pouvez en remettre une dans la comparaison ci-dessous."
          />
        </div>
      </SettingsBlock>

      <SettingsBlock title={`Images reconnues comme logos · ${items.length.toLocaleString("fr-FR")}`}>
        {items.length ? (
          <ul className="divide-y divide-line">
            {items.map((item) => (
              <LogoRow
                key={item.id}
                item={item}
                enabled={enabled}
                busy={keep.isPending && keep.variables?.id === item.id}
                onToggle={() => keep.mutate({ id: item.id, kept: !item.kept }, { onError: fail("L'image n'a pas été modifiée") })}
              />
            ))}
          </ul>
        ) : (
          <p className="px-5 py-4 text-sm text-muted">Aucune image reconnue comme logo pour l'instant. La liste se remplit quand les images lues sont indexées.</p>
        )}
      </SettingsBlock>
    </>
  );
}

function LogoRow(props: { item: RecognisedLogo; enabled: boolean; busy: boolean; onToggle: () => void }) {
  const { item } = props;
  const state = !props.enabled ? "Pas écartée : le réglage est désactivé" : item.kept ? "Remise dans la comparaison" : "Mise à part";
  return (
    <li className="flex items-center gap-3 px-5 py-2.5 text-sm">
      <Thumb src={item.thumb_url} size={48} />
      <span className="min-w-0 flex-1">
        <span className="block truncate" title={item.url}>{fileName(item.url)}</span>
        <span className="block truncate text-xs text-muted">
          {hostOf(item.url)} · {item.pages.toLocaleString("fr-FR")} {item.pages > 1 ? "pages" : "page"} · reconnue à {Math.round(item.score * 100)} %
        </span>
        <span className="block text-xs text-ink-soft">{state}</span>
      </span>
      <Button size="sm" variant="ghost" loading={props.busy} onClick={props.onToggle} aria-label={`${item.kept ? "Mettre à part" : "Remettre dans la comparaison"} : ${fileName(item.url)}`}>
        {item.kept ? "Mettre à part" : "Remettre dans la comparaison"}
      </Button>
    </li>
  );
}
