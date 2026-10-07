import { useToast } from "../../components/feedback";
import { Button, Card, Thumb } from "../../components/ui";
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
    <Card padded={false}>
      <div className="px-5 pt-5">
        <h2 className="font-semibold">Images exclues{org.brands.length > 1 ? ` · ${brand.name}` : ""}</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Faux positifs récurrents (logos, visuels génériques) qui ne sont plus jamais proposés, copies proches comprises.
          On exclut une image depuis l'écran « À traiter ».
        </p>
      </div>
      {items.length ? (
        <ul className="mt-3 divide-y divide-line border-t border-line">
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
        <p className="px-5 pt-2 pb-5 text-sm text-muted">Aucune image exclue.</p>
      )}
    </Card>
  );
}
