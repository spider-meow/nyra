import { LoadError } from "../../components/busy";
import { Link } from "react-router";
import { useToast } from "../../components/feedback";
import { SettingsBlock } from "../../components/SettingsBlock";
import { Checkbox, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import { useLogoMutations, useLogos } from "../../lib/siteImages";

/** The setting that leaves the images recognised as logos out of the comparison. The images themselves are listed in the Logos tab of "Images en ligne". */
export function Logos() {
  const { link } = useOrg();
  const logos = useLogos();
  const { setEnabled } = useLogoMutations();
  const toast = useToast();
  if (logos.isLoading) return <Skeleton className="h-40" />;
  if (!logos.data) return <LoadError title="Impossible de charger le réglage" error={logos.error} onRetry={() => void logos.refetch()} retrying={logos.isFetching} />;
  const { enabled, items } = logos.data;
  return (
    <SettingsBlock
      title="Logos"
      description="Nyra reconnaît une image qui est un logo à son aspect d'ensemble : une photo où un logo apparaît n'est jamais écartée. Désactivé par défaut."
    >
      <div className="grid gap-4 p-5">
        <Checkbox
          checked={enabled}
          disabled={setEnabled.isPending}
          onChange={(value) =>
            setEnabled.mutate(value, {
              onSuccess: () => toast.show({ tone: "success", message: value ? "Les logos sont mis à part" : "Les logos sont de nouveau comparés", description: "Une comparaison vient d'être programmée." }),
              onError: (error) => toast.show({ tone: "error", message: "Le réglage n'a pas été enregistré", description: errorMessage(error) }),
            })
          }
          label="Ne pas comparer les logos"
          hint="Les images reconnues comme logos ne sont plus proposées dans « À qualifier » ni comparées à la bibliothèque."
        />
        <p className="text-sm text-muted">
          {items.length.toLocaleString("fr-FR")} {items.length > 1 ? "images reconnues comme logos" : "image reconnue comme logo"} ·{" "}
          <Link className="font-medium text-ink underline underline-offset-2" to={`${link("online-images")}?view=logos`}>Les voir et en remettre une dans la comparaison</Link>
        </p>
      </div>
    </SettingsBlock>
  );
}
