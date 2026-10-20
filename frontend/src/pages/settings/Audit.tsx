import { AuditLog } from "../../components/AuditLog";
import { SettingsBlock } from "../../components/SettingsBlock";
import { useOrg } from "../../lib/org";

/** Who did what in the organization (administrators only): library, decisions, settings, team. */
export function Audit() {
  const { org, orgApiPath } = useOrg();
  return (
    <SettingsBlock title="Journal d'activité" description="Les actions des personnes de l'organisation : ajouts et suppressions de visuels, décisions, images ignorées, réglages, équipe. Aucun contenu n'y est copié. Conservé 12 mois.">
      <AuditLog path={orgApiPath("/audit")} cacheKey={org.org_id} />
    </SettingsBlock>
  );
}
