import { useQuery } from "@tanstack/react-query";
import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { usageText, UsageBar, usageTone } from "../../components/UsageLines";
import { cx, EmptyState, Skeleton } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import type { OrgUsage, UsageItem } from "../../types";

function hint(item: UsageItem): string | undefined {
  if (item.level === "reached") return "Limite atteinte : contactez l'équipe Nyra pour la relever.";
  if (item.level === "warning") return "Plus de 80 % de la limite est utilisé.";
  if (item.key === "pages") return "Le compteur repart à zéro au début de chaque mois.";
  return item.key === "emails" ? "Le compteur repart à zéro chaque jour." : undefined;
}

/** Réglages > Usage — what the organization has used this month against its limits (set by the Nyra team). */
export function Usage() {
  const { org, orgApiPath } = useOrg();
  const usage = useQuery({ queryKey: ["usage", org.org_id], queryFn: () => api.get<OrgUsage>(orgApiPath("/usage")) });
  if (usage.isLoading) return <Skeleton className="h-72" />;
  if (!usage.data) return <EmptyState title="Usage indisponible" body={errorMessage(usage.error)} />;
  return (
    <SettingsBlock title="Usage et limites" description="Ce que votre organisation a utilisé, sur l'ensemble de ses marques. Une lecture qui atteindrait la limite du mois s'arrête avant la fin ; au-delà, ajouter est refusé jusqu'au mois suivant ou jusqu'à une nouvelle limite.">
      {usage.data.items.map((item) => (
        <SettingsRow key={item.key} label={item.label} hint={hint(item)}>
          <span className={cx("text-sm tabular", usageTone(item))}>{usageText(item)}</span>
          <UsageBar item={item} />
        </SettingsRow>
      ))}
    </SettingsBlock>
  );
}
