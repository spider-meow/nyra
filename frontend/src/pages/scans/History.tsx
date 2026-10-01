import { Card, EmptyState, Skeleton, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDateTime, hostOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useScans } from "../../lib/queries";
import type { Scan } from "../../types";

const scanStatus: Record<Scan["status"], { label: string; tone: string }> = {
  running: { label: "En cours", tone: "text-focus" },
  done: { label: "Terminée", tone: "text-ok" },
  error: { label: "Échec", tone: "text-expired" },
  cancelled: { label: "Arrêtée", tone: "text-muted" },
};

function siteName(site: { url: string; label: string }): string {
  return site.label ? `${site.label} · ${hostOf(site.url)}` : hostOf(site.url);
}

export function ScanHistory() {
  const { admin } = useOrg();
  const scans = useScans();
  if (scans.isLoading) return <Skeleton className="h-40" />;
  if (scans.error) return <EmptyState title="Historique indisponible" body={errorMessage(scans.error)} />;
  if (!scans.data?.scans.length) {
    return <EmptyState title="Aucune lecture pour l'instant" body={admin ? "Ajoutez une adresse puis lancez la lecture." : "Un administrateur doit lancer la première lecture."} />;
  }
  return (
    <Card padded={false} className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th className="px-4 py-2.5 font-medium">Site</th>
            <th className="px-2 py-2.5 font-medium">Début</th>
            <th className="px-2 py-2.5 font-medium">État</th>
            <th className="px-2 py-2.5 text-right font-medium">Pages</th>
            <th className="px-2 py-2.5 text-right font-medium">Nouvelles images</th>
            <th className="px-4 py-2.5 font-medium">Incidents</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {scans.data.scans.map((scan) => (
            <tr key={scan.id} className="align-top">
              <td className="px-4 py-2.5 font-medium">{siteName({ url: scan.site_url, label: scan.site_label })}</td>
              <td className="px-2 py-2.5 text-muted">{formatDateTime(scan.started_at)}</td>
              <td className={cx("px-2 py-2.5", scanStatus[scan.status].tone)}>{scanStatus[scan.status].label}</td>
              <td className="px-2 py-2.5 text-right tabular">{scan.pages_visited}</td>
              <td className="px-2 py-2.5 text-right tabular">{scan.images_new}</td>
              <td className="px-4 py-2.5 text-[13px] text-muted"><Incidents scan={scan} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Incidents(props: { scan: Scan }) {
  const { scan } = props;
  if (scan.error_count || scan.blocked_by_robots) {
    return (
      <details>
        <summary className="cursor-pointer">
          {[scan.error_count ? plural(scan.error_count, "page en erreur", "pages en erreur") : "", scan.blocked_by_robots ? `${scan.blocked_by_robots} bloquée(s) par robots.txt` : ""].filter(Boolean).join(" · ")}
        </summary>
        <ul className="mt-1 grid gap-1">
          {scan.errors.map((line, index) => <li key={`${index}:${line}`} className="break-all">{line}</li>)}
        </ul>
      </details>
    );
  }
  return <>{scan.status === "done" && scan.pages_visited > 0 && scan.images_found === 0 ? "Aucune image assez grande trouvée" : "·"}</>;
}
