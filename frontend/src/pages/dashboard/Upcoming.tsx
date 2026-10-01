import { Link } from "react-router";
import { Card, StatusBadge, cx } from "../../components/ui";
import { daysText, formatDate } from "../../lib/format";
import { useOrg } from "../../lib/org";
import type { Upcoming as UpcomingItem } from "../../types";

const monthFormat = new Intl.DateTimeFormat("fr-FR", { month: "short" });

function dayMonth(iso: string): [string, string] {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return ["·", ""];
  return [String(date.getDate()).padStart(2, "0"), monthFormat.format(date).replace(".", "")];
}

/** Rights expiring in the next 90 days, and whether each visual is still online. */
export function Upcoming(props: { items: UpcomingItem[] }) {
  const { link } = useOrg();
  return (
    <Card padded={false} className="mt-6 overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-6 py-4">
        <h2 className="text-[17px] font-semibold tracking-tight">Échéances des 90 prochains jours</h2>
        <Link to={link("bibliotheque")} className="text-[13.5px] text-bark-700 hover:underline">Toute la bibliothèque</Link>
      </div>
      {props.items.length ? (
        <ul className="divide-y divide-side">
          {props.items.map((item) => {
            const [day, month] = dayMonth(item.expiry_date);
            return (
              <li key={item.reference_id} className="flex items-center gap-4 px-6 py-3 text-sm">
                <span className="flex w-[52px] shrink-0 flex-col items-center rounded-[10px] bg-canvas py-1" aria-label={formatDate(item.expiry_date)}>
                  <span className="text-lg leading-tight font-semibold tabular">{day}</span>
                  <span className="text-[11px] tracking-wide text-muted uppercase">{month}</span>
                </span>
                <span className="min-w-0 flex-1 truncate">{item.filename}</span>
                <StatusBadge status={item.days_left < 0 ? "expire" : item.days_left < 30 ? "<30j" : "<90j"} label={daysText(item.days_left)} />
                <span className={cx("hidden w-24 shrink-0 text-right text-[13px] sm:block", item.online ? "font-medium text-expired" : "text-muted")}>
                  {item.online ? "En ligne" : "Pas trouvé"}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="px-6 py-8 text-sm text-muted">Aucune échéance dans les 90 prochains jours.</p>
      )}
    </Card>
  );
}
