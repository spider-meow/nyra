import { useState } from "react";
import { LineHistory, SERIES, type LineMark, type LineSeries } from "../../components/charts";
import { Section } from "../../components/insights";
import { EmptyState, Segmented, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { approxDuration, bytes, duration, num, percent } from "../../lib/format";
import { useServerHistory } from "../../lib/staff";
import type { ServerHistory } from "../../types";

const RANGES: { value: ServerHistory["range"]; label: string; seconds: number }[] = [
  { value: "1h", label: "1 h", seconds: 3600 },
  { value: "24h", label: "24 h", seconds: 86_400 },
  { value: "7d", label: "7 j", seconds: 7 * 86_400 },
  { value: "30d", label: "30 j", seconds: 30 * 86_400 },
];

type Container = ServerHistory["containers"][number];

/** The time of a point: the hour alone within an hour's view, with the date beyond (a day's view spans two dates). */
function timeLabel(range: ServerHistory["range"]) {
  const options: Intl.DateTimeFormatOptions = range === "1h" ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" };
  return (t: number) => new Date(t * 1000).toLocaleString("fr-FR", options);
}

/** One line per container (the web process, each worker), in the order of the palette. */
function perContainer(history: ServerHistory, pick: (container: Container) => number | null, format: (value: number) => string): LineSeries[] {
  const names = [...new Set(history.containers.map((container) => container.instance))];
  const roles = names.map((instance) => history.containers.find((container) => container.instance === instance)?.role);
  return names.map((instance, index) => {
    const rows = history.containers.filter((container) => container.instance === instance);
    const values = rows.map(pick).filter((value): value is number => value !== null);
    const worker = rows[0].role === "worker";
    return {
      key: instance,
      label: `${worker ? "Worker" : "Interface et API"}${roles.filter((role) => role === rows[0].role).length > 1 ? ` ${instance.slice(0, 6)}` : ""}`,
      color: SERIES[index % SERIES.length],
      points: rows.map((row) => ({ t: row.t, v: pick(row) })),
      summary: values.length ? `moy. ${format(values.reduce((sum, value) => sum + value, 0) / values.length)} · pic ${format(Math.max(...values))}` : undefined,
    };
  });
}

function line(key: string, label: string, color: string, points: { t: number; v: number | null }[], format: (value: number) => string): LineSeries {
  const values = points.map((point) => point.v).filter((value): value is number => value !== null);
  return { key, label, color, points, summary: values.length ? `pic ${format(Math.max(...values))}` : undefined };
}

function workerLimit(history: ServerHistory, pick: (container: Container) => number | null): number | null {
  const limits = history.containers.filter((container) => container.role === "worker").map(pick).filter((value): value is number => value !== null);
  return limits.length ? Math.max(...limits) : null;
}

function marksOf(...entries: [number | null, string][]): LineMark[] {
  return entries.flatMap(([value, label]) => (value === null ? [] : [{ value, label }]));
}

/** A count axis with whole ticks: an even top, at least 2. */
function countMax(queue: ServerHistory["queue"]): number {
  return Math.max(2, Math.ceil(Math.max(...queue.map((point) => Math.max(point.queued, point.running))) / 2) * 2);
}

const cpuText = (value: number) => `${num(value, 0)} %`;

function Charts(props: { history: ServerHistory; range: ServerHistory["range"] }) {
  const { history } = props;
  const to = Math.max(...history.host.map((point) => point.t), history.queue.at(-1)?.t ?? 0) + history.step;
  const domain: [number, number] = [to - (RANGES.find((range) => range.value === props.range)?.seconds ?? 0), to];
  const shared = { step: history.step, domain, tFormat: timeLabel(props.range) };
  const memoryLimit = workerLimit(history, (container) => container.memory_limit);
  const cpuLimit = workerLimit(history, (container) => container.cpu_limit);
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <h3 className="mb-2 text-sm font-semibold">Processeur</h3>
        <LineHistory {...shared} title="Processeur par conteneur" format={cpuText} series={perContainer(history, (c) => c.cpu, cpuText)} marks={marksOf([cpuLimit === null ? null : cpuLimit * 100, "limite du worker"])} />
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Mémoire</h3>
        <LineHistory {...shared} title="Mémoire par conteneur" format={bytes} series={perContainer(history, (c) => c.memory, bytes)}
          marks={marksOf([memoryLimit === null ? null : memoryLimit * history.thresholds.worker_memory, `alerte du worker (${percent(history.thresholds.worker_memory)})`], [memoryLimit, "limite du worker"])} />
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Disque de la machine</h3>
        <LineHistory {...shared} title="Disque utilisé" format={(v) => percent(v)} max={1} marks={marksOf([history.thresholds.disk, `alerte (${percent(history.thresholds.disk)})`])}
          series={[line("disk", "Disque utilisé", SERIES[2], history.host.map((point) => ({ t: point.t, v: point.disk })), (v) => percent(v, 1))]} />
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">File d'attente</h3>
        <LineHistory {...shared} title="Tâches en attente et en cours" format={(v) => num(v)} max={countMax(history.queue)}
          series={[line("queued", "En attente", SERIES[1], history.queue.map((p) => ({ t: p.t, v: p.queued })), (v) => num(v)), line("running", "En cours", SERIES[0], history.queue.map((p) => ({ t: p.t, v: p.running })), (v) => num(v))]} />
      </div>
      <div className="lg:col-span-2">
        <h3 className="mb-2 text-sm font-semibold">Temps d'attente avant le démarrage d'une tâche</h3>
        <LineHistory {...shared} title="Temps d'attente" format={(v) => duration(v)} axisFormat={approxDuration}
          series={[line("wait", "Moyen", SERIES[0], history.queue.map((p) => ({ t: p.t, v: p.wait })), duration), line("wait_max", "Le plus long", SERIES[1], history.queue.map((p) => ({ t: p.t, v: p.wait_max })), duration)]} />
      </div>
    </div>
  );
}

/** The curves of the Serveur page: what the containers used, the disk, and the queue, over a period of the team's choice. */
export function HistorySection() {
  const [range, setRange] = useState<ServerHistory["range"]>("24h");
  const history = useServerHistory(range);
  return (
    <Section
      className="mt-4"
      title="Usage dans le temps"
      description="Un relevé par minute et par conteneur, moyenné par pas de temps. Les lignes pointillées sont les limites et les seuils d'alerte ; une coupure veut dire que le conteneur n'a rien publié."
      aside={<Segmented label="Période" value={range} options={RANGES.map(({ value, label }) => ({ value, label }))} onChange={setRange} />}
    >
      {history.isLoading ? <Skeleton className="h-72" /> : null}
      {!history.data && history.error ? <EmptyState title="Impossible de lire l'historique" body={errorMessage(history.error)} /> : null}
      {history.data ? <Charts history={history.data} range={range} /> : null}
    </Section>
  );
}
