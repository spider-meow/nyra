import { Link } from "react-router";
import { LoadError } from "../../components/busy";
import { useToast } from "../../components/feedback";
import { SettingsBlock } from "../../components/SettingsBlock";
import { Checkbox, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import { usePackshotMutations, usePackshots } from "../../lib/siteImages";

/** The setting of the Packshots category of "Images en ligne" (AXE-527). Nothing is hidden from the comparison: the library is compared with every image whatever this says. */
export function Packshots() {
  const { link } = useOrg();
  const packshots = usePackshots();
  const { setEnabled } = usePackshotMutations();
  const toast = useToast();
  if (packshots.isLoading) return <Skeleton className="h-40" />;
  if (!packshots.data) return <LoadError title="Impossible de charger le réglage" error={packshots.error} onRetry={() => void packshots.refetch()} retrying={packshots.isFetching} />;
  const { available, enabled, detected } = packshots.data;
  return (
    <SettingsBlock
      title="Packshots"
      description="Un packshot est une photo de produit seul sur un fond uni, comme celles d'un catalogue. Nyra les repère avec deux signaux : l'aspect d'ensemble de l'image et le bord de l'image d'une seule couleur. Désactivé par défaut."
    >
      <div className="grid gap-4 p-5">
        <Checkbox
          checked={enabled}
          disabled={!available || setEnabled.isPending}
          onChange={(value) =>
            setEnabled.mutate(value, {
              onSuccess: () => toast.show({
                tone: "success",
                message: value ? "Les packshots ont leur onglet" : "Les packshots ne sont plus à part",
                description: value ? "Une analyse des images déjà lues vient d'être programmée : les packshots apparaissent au fur et à mesure." : undefined,
              }),
              onError: (error) => toast.show({ tone: "error", message: "Le réglage n'a pas été enregistré", description: errorMessage(error) }),
            })
          }
          label="Ranger les packshots dans leur propre onglet"
          hint="Ils quittent « À qualifier » mais restent dans « Toutes » et restent comparés à la bibliothèque : rien n'est masqué ni ignoré."
        />
        {!available ? <p className="text-sm text-muted">Ce réglage demande une mise à jour de la base (migration des packshots) qui n'est pas encore appliquée.</p> : null}
        {enabled ? (
          <p className="text-sm text-muted">
            {detected.toLocaleString("fr-FR")} {detected > 1 ? "images détectées" : "image détectée"} ·{" "}
            <Link className="font-medium text-ink underline underline-offset-2" to={`${link("online-images")}?view=packshots`}>Les voir</Link>
          </p>
        ) : null}
      </div>
    </SettingsBlock>
  );
}
