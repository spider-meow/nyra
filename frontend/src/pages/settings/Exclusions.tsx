import { LoadError } from "../../components/busy";
import { useToast } from "../../components/feedback";
import { SettingsBlock } from "../../components/SettingsBlock";
import { Button, Skeleton, Thumb } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate, hostOf } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useExclusionMutations, useExclusions } from "../../lib/queries";

/** The images set aside for the brand on screen, which an administrator can bring back. */
export function Exclusions() {
  const { admin, brand, org } = useOrg();
  const exclusions = useExclusions();
  const { remove } = useExclusionMutations();
  const toast = useToast();
  const items = exclusions.data?.exclusions ?? [];
  return (
    <SettingsBlock
      title={`Images exclues${org.brands.length > 1 ? ` · ${brand.name}` : ""}`}
      description="Faux positifs récurrents (logos, visuels génériques) qui ne sont plus jamais proposés, copies proches comprises. On exclut une image depuis l'écran « À traiter »."
    >
      {exclusions.isLoading ? (
        <div className="p-5" aria-busy="true"><Skeleton className="h-16" /></div>
      ) : exclusions.error && !exclusions.data ? (
        <div className="p-5"><LoadError title="Impossible de charger les images exclues" error={exclusions.error} onRetry={() => void exclusions.refetch()} retrying={exclusions.isFetching} /></div>
      ) : items.length ? (
        <ul className="divide-y divide-line">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
              <Thumb src={item.thumb_url} size={40} />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{item.reason || "Sans raison indiquée"}</span>
                <span className="block truncate text-xs text-muted">
                  {item.site_url ? `${hostOf(item.site_url)} · ` : ""}exclue le {formatDate(item.created_at)}
                </span>
              </span>
              {admin ? (
                <Button
                  size="sm"
                  variant="ghost"
                  loading={remove.isPending && remove.variables === item.id}
                  disabled={remove.isPending}
                  onClick={() =>
                    remove.mutate(item.id, {
                      onSuccess: () =>
                        toast.show({ tone: "success", message: "Image réintégrée", description: "Elle est de nouveau comparée : une comparaison vient d'être programmée." }),
                      onError: (error) => toast.show({ tone: "error", message: "L'image n'a pas été réintégrée", description: errorMessage(error) }),
                    })
                  }
                >
                  Réintégrer
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-5 py-4 text-sm text-muted">Aucune image exclue.</p>
      )}
    </SettingsBlock>
  );
}
