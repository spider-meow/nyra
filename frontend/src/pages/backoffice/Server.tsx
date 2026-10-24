import { useQuery } from "@tanstack/react-query";
import { ShareBar } from "../../components/charts";
import { Section, Table } from "../../components/insights";
import { EmptyState, PageHeader, Skeleton, Stat, StatusBadge, cx } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { bytes, duration, num, percent, plural } from "../../lib/format";
import { toneClass } from "../BackOfficeSections";
import type { ServerUsage, ServiceReading } from "../../types";

/** A worker publishes every 15 s: past a minute without news it is taken for stopped. */
const SILENT_SECONDS = 60;

function Usage(props: { ratio: number | null; children: string }) {
  if (props.ratio === null) return <span className="text-muted">{props.children}</span>;
  return <ShareBar ratio={props.ratio}><span className="w-28 text-right tabular">{props.children}</span></ShareBar>;
}

function cpuCell(service: ServiceReading) {
  const { cpu_percent: used, cpu_limit: limit, host } = service.reading;
  if (used === null) return <Usage ratio={null}>…</Usage>;
  return <Usage ratio={used / ((limit ?? host.cpus) * 100)}>{`${num(used, 1)} %`}</Usage>;
}

function memoryCell(service: ServiceReading) {
  const { memory, memory_limit: limit, host } = service.reading;
  const total = limit ?? host.memory_total;
  return <Usage ratio={total ? memory / total : null}>{limit ? `${bytes(memory)} / ${bytes(limit)}` : bytes(memory)}</Usage>;
}

function serviceRow(service: ServiceReading) {
  const silent = service.age_seconds > SILENT_SECONDS;
  return [
    <span>
      {service.role === "web" ? "Interface et API" : "Worker"}
      <span className="block text-xs font-normal text-muted">{service.instance}</span>
    </span>,
    silent ? <StatusBadge status="expire" label={`Muet depuis ${duration(service.age_seconds)}`} /> : <StatusBadge status="ok" label="En ligne" />,
    silent ? "—" : cpuCell(service),
    silent ? "—" : memoryCell(service),
  ];
}

/** The machine's figures come from any container: they are the host's, the same for all. */
function HostStats(props: { host: ServiceReading["reading"]["host"] }) {
  const { host } = props;
  const memoryUsed = host.memory_total && host.memory_available ? host.memory_total - host.memory_available : null;
  return (
    <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Stat tint="yellow" value={num(host.load[0], 2)} label="charge de la machine" hint={`sur ${num(host.cpus)} cœur(s) · 5 min ${num(host.load[1], 2)} · 15 min ${num(host.load[2], 2)}`} />
      <Stat tint="mint" value={memoryUsed && host.memory_total ? percent(memoryUsed / host.memory_total) : "—"} label="mémoire de la machine" hint={`${bytes(memoryUsed)} sur ${bytes(host.memory_total)}`} />
      <Stat tint="blush" value={percent(host.disk_used / host.disk_total)} label="disque" hint={`${bytes(host.disk_used)} sur ${bytes(host.disk_total)}`} />
      <Stat tint="teal" value={num(host.cpus)} label="cœurs" hint="100 % = un cœur, comme docker stats" />
    </div>
  );
}

/** The thresholds crossed right now (computed by the server: disk, worker memory, queue, absent worker), or all clear. */
function Alerts(props: { alerts: ServerUsage["alerts"] }) {
  const { alerts } = props;
  const tone = alerts.some((alert) => alert.severity === "critical") ? "alert" : alerts.length ? "warn" : "ok";
  return (
    <section className={cx("mb-4 rounded-xl border p-5", toneClass[tone])}>
      <p className="text-lg font-semibold">
        <span aria-hidden>{tone === "ok" ? "● " : "▲ "}</span>
        {alerts.length ? plural(alerts.length, "alerte", "alertes") : "Tout va bien"}
      </p>
      {alerts.length ? (
        <ul className="mt-1 list-disc pl-5 text-sm text-ink">{alerts.map((alert) => <li key={`${alert.code}/${alert.instance ?? ""}`}>{alert.message}{alert.instance ? ` (${alert.instance})` : ""}</li>)}</ul>
      ) : (
        <p className="text-sm text-ink-soft">Un worker publie, la file avance, le disque et la mémoire sont sous leurs seuils.</p>
      )}
    </section>
  );
}

/** /back-office/server — what the web process and each worker use, like `docker stats`. */
export function Server() {
  const usage = useQuery({
    queryKey: ["staff", "server"],
    queryFn: () => api.get<ServerUsage>("/staff/server"),
    refetchInterval: 5_000,
  });
  const services = usage.data?.services ?? [];
  return (
    <>
      <PageHeader title="Serveur" />
      {usage.isLoading ? <Skeleton className="h-72" /> : null}
      {!usage.data && usage.error ? <EmptyState title="Impossible de lire le serveur" body={errorMessage(usage.error)} /> : null}
      {usage.data ? <Alerts alerts={usage.data.alerts} /> : null}
      {services.length ? (
        <>
          <HostStats host={services[0].reading.host} />
          <Section title="Conteneurs" description="Mis à jour toutes les 5 s. Un worker publie ses chiffres toutes les 15 s, même pendant une lecture.">
            <Table head={["Service", "État", "CPU", "Mémoire"]} align={["left", "left", "left", "left"]} rows={services.map(serviceRow)} />
          </Section>
        </>
      ) : null}
    </>
  );
}
