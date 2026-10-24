import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { Button, Checkbox, EmptyState, fieldClass, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import { useSchedule, useSetSchedule } from "../../lib/queries";

const DAYS = Array.from({ length: 28 }, (_, index) => index + 1);
// The reads start between 02:00 and 05:00 UTC: the same calendar day in Paris, winter and summer.
const dayFormat = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Paris" });

function frenchDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dayFormat.format(date);
}

/** « le 3 novembre 2026, dans la nuit »; a date already past means the read goes at the next pass of the robot. */
function nextText(iso: string): string {
  return new Date(iso).getTime() > Date.now() ? `le ${frenchDay(iso)}, dans la nuit` : "très prochainement";
}

/** The brand's monthly read: everyone sees it, the administrators change it. */
export function Schedule() {
  const { admin } = useOrg();
  const query = useSchedule();
  const save = useSetSchedule();
  const data = query.data;

  if (query.isLoading) return <Skeleton className="h-40" />;
  if (query.error || !data) {
    return <EmptyState title="Lecture mensuelle indisponible" body={errorMessage(query.error)} action={<Button size="sm" onClick={() => void query.refetch()}>Réessayer</Button>} />;
  }
  const locked = !admin || save.isPending;
  return (
    <SettingsBlock title="Lecture mensuelle automatique">
      <div className="grid gap-4 p-5" aria-busy={save.isPending}>
        <Checkbox
          checked={data.enabled}
          disabled={locked}
          onChange={(enabled) => save.mutate({ enabled, day_of_month: data.day_of_month })}
          label="Lire les sites de cette marque chaque mois"
          hint="Nyra relit les sites de la marque une fois par mois, la nuit, puis envoie aux administrateurs un e-mail de résumé : nouveaux visuels à traiter et visuels qui expirent dans 30 et 90 jours."
        />
        {admin ? null : <p className="text-[13px] text-muted">Seuls les administrateurs peuvent modifier ce réglage.</p>}
        {save.error ? <p role="alert" className="text-sm text-terracotta">{errorMessage(save.error)}</p> : null}
      </div>
      <SettingsRow label="Jour du mois" hint="Du 1 au 28, pour que chaque mois ait ce jour.">
        <select
          aria-label="Jour du mois"
          className={`${fieldClass} w-24`}
          value={data.day_of_month}
          disabled={locked || !data.enabled}
          onChange={(event) => save.mutate({ enabled: data.enabled, day_of_month: Number(event.target.value) })}
        >
          {DAYS.map((day) => <option key={day} value={day}>{day}</option>)}
        </select>
      </SettingsRow>
      {data.next_run_at ? <SettingsRow label={`Prochaine lecture : ${nextText(data.next_run_at)}`} /> : null}
      {data.last_read_at ? <SettingsRow label={`Dernière lecture automatique : ${frenchDay(data.last_read_at)}`} /> : null}
    </SettingsBlock>
  );
}
