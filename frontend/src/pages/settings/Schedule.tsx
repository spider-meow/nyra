import { LoadError } from "../../components/busy";
import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { Checkbox, fieldClass, SettingsSkeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import { useSchedule, useSetSchedule } from "../../lib/queries";

const DAYS = [1, 2, 3]; // the day of the first read; the second one falls between the 13th and the 16th (`second_day`)
// The reads start between 02:00 and 05:00 UTC: the same calendar day in Paris, winter and summer.
const dayFormat = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Paris" });

function frenchDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dayFormat.format(date);
}

/** « le 3 novembre 2026, tôt le matin »; a date already past means the read goes at the next pass of the robot. */
function nextText(iso: string): string {
  return new Date(iso).getTime() > Date.now() ? `le ${frenchDay(iso)}, tôt le matin` : "très prochainement";
}

/** The brand's scheduled reads, twice a month: the team changes it (the card is hidden from the others). */
export function Schedule() {
  const { admin } = useOrg();
  const query = useSchedule();
  const save = useSetSchedule();
  const data = query.data;

  if (query.isLoading) return <SettingsSkeleton groups={[3]} />;
  if (!data) return <LoadError title="Lectures automatiques indisponibles" error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} />;
  const locked = !admin || save.isPending;
  return (
    <SettingsBlock title="Lectures automatiques">
      <div className="grid gap-4 p-5" aria-busy={save.isPending}>
        <Checkbox
          checked={data.enabled}
          disabled={locked}
          onChange={(enabled) => save.mutate(enabled ? { enabled, day_of_month: data.day_of_month } : { enabled })}
          label="Lire les sites de cette marque deux fois par mois"
          hint="Deux lectures par mois, tôt le matin. Les administrateurs reçoivent un résumé par e-mail chaque mois."
        />
        {admin ? null : <p className="text-[13px] text-muted">Seuls les administrateurs peuvent modifier ce réglage.</p>}
        {save.error ? <p role="alert" className="text-sm text-terracotta">{errorMessage(save.error)}</p> : null}
      </div>
      <SettingsRow label="Première lecture" hint="Le 1er, le 2 ou le 3 de chaque mois.">
        <select
          aria-label="Jour de la première lecture"
          className={`${fieldClass} w-24`}
          value={data.day_of_month}
          disabled={locked || !data.enabled}
          onChange={(event) => save.mutate({ enabled: data.enabled, day_of_month: Number(event.target.value) })}
        >
          {DAYS.map((day) => <option key={day} value={day}>{day}</option>)}
        </select>
      </SettingsRow>
      <SettingsRow label={`Seconde lecture : le ${data.second_day} de chaque mois`} hint="Fixée pour cette marque, entre le 13 et le 16." />
      {data.next_run_at ? <SettingsRow label={`Prochaine lecture : ${nextText(data.next_run_at)}`} /> : null}
      {data.last_read_at ? <SettingsRow label={`Dernière lecture automatique : ${frenchDay(data.last_read_at)}`} /> : null}
    </SettingsBlock>
  );
}
