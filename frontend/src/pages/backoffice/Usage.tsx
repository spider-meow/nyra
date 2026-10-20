import { useState, type FormEvent } from "react";
import { useToast } from "../../components/feedback";
import { Section, Table } from "../../components/insights";
import { UsageBar, usageText, usageTone } from "../../components/UsageLines";
import { Button, cx, EmptyState, FieldLabel, Input, PageHeader, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useStaffMutations, useStaffUsage } from "../../lib/staff";
import type { StaffUsage } from "../../types";

type Org = StaffUsage["organizations"][number];

/** The limits an organization can have, by the name the API uses. */
const LIMITS = [
  { key: "pages_per_month", label: "Pages lues par mois" },
  { key: "brands", label: "Marques" },
  { key: "sites_per_brand", label: "Adresses par marque" },
  { key: "references", label: "Visuels dans la bibliothèque" },
  { key: "storage_gb", label: "Stockage (Go)" },
  { key: "members", label: "Membres" },
  { key: "emails_per_day", label: "Invitations par jour" },
];

/** Empty = the default limit (shown as the placeholder); only the Nyra team can set a number here. */
function LimitsForm(props: { org: Org; defaults: StaffUsage["defaults"]; onDone: () => void }) {
  const { org, defaults } = props;
  const { setLimits } = useStaffMutations();
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(LIMITS.map(({ key }) => [key, String(org.overrides[key] ?? "")])));

  function submit(event: FormEvent) {
    event.preventDefault();
    const limits = Object.fromEntries(LIMITS.map(({ key }) => [key, values[key].trim() === "" ? null : Number(values[key])]));
    setLimits.mutate(
      { orgId: org.id, limits },
      {
        onSuccess: () => {
          toast.show({ tone: "success", message: `Limites de ${org.name} enregistrées` });
          props.onDone();
        },
        onError: (error) => toast.show({ tone: "error", message: "Les limites n'ont pas été enregistrées", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Section title={`Limites de ${org.name}`} description="Laissez vide pour garder la limite par défaut (config.yaml). Le client ne peut pas les modifier.">
      <form className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" onSubmit={submit}>
        {LIMITS.map(({ key, label }) => (
          <div key={key}>
            <FieldLabel htmlFor={`limit-${key}`}>{label}</FieldLabel>
            <Input id={`limit-${key}`} type="number" min={0} step={key === "storage_gb" ? "any" : 1} placeholder={String(defaults[key])} value={values[key]} onChange={(event) => setValues({ ...values, [key]: event.target.value })} />
          </div>
        ))}
        <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-4">
          <Button type="submit" variant="primary" loading={setLimits.isPending}>Enregistrer</Button>
          <Button type="button" variant="ghost" onClick={props.onDone}>Annuler</Button>
        </div>
      </form>
    </Section>
  );
}

function alignOf(columns: number): ("left" | "right")[] {
  return ["left", ...Array<"left">(columns).fill("left"), "right"];
}

function usageRow(org: Org, edit: (id: string) => void) {
  return [
    <span>{org.name}{Object.keys(org.overrides).length ? <span className="block text-xs font-normal text-muted">limites propres</span> : null}</span>,
    ...org.items.map((item) => (
      <span key={item.key} className="grid gap-1">
        <span className={cx("whitespace-nowrap", usageTone(item))}>{usageText(item)}</span>
        <UsageBar item={item} className="w-24" />
      </span>
    )),
    <Button size="sm" variant="ghost" onClick={() => edit(org.id)}>Limites</Button>,
  ];
}

/** /back-office/usage — what each organization used this month against its limits, and the limits only the Nyra team sets. */
export function Usage() {
  const usage = useStaffUsage();
  const [editing, setEditing] = useState<string | null>(null);
  const data = usage.data;
  const org = data?.organizations.find((candidate) => candidate.id === editing);
  return (
    <>
      <PageHeader title="Usage" />
      {usage.isLoading ? <Skeleton className="h-72" /> : null}
      {!data && usage.error ? <EmptyState title="Impossible de lire l'usage" body={errorMessage(usage.error)} /> : null}
      {data ? (
        <div className="grid gap-4">
          {data.truncated ? <p className="text-sm text-expired">Seules les {data.organizations.length} premières organisations (par nom) sont affichées.</p> : null}
          <Section title="Ce mois-ci" description="Pages lues et invitations du jour ; les autres lignes sont l'état actuel. Orange à partir de 80 % de la limite, rouge quand elle est atteinte.">
            <Table
              head={["Organisation", ...(data.organizations[0]?.items.map((item) => item.label) ?? []), ""]}
              align={alignOf(data.organizations[0]?.items.length ?? 0)}
              rows={data.organizations.map((candidate) => usageRow(candidate, setEditing))}
              empty="Aucune organisation."
            />
          </Section>
          {org ? <LimitsForm key={org.id} org={org} defaults={data.defaults} onDone={() => setEditing(null)} /> : null}
        </div>
      ) : null}
    </>
  );
}
