import { useState } from "react";
import { Select } from "../../components/Dropdown";
import { Section, Table } from "../../components/insights";
import { Button, EmptyState, PageHeader, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { useStaffOrgs } from "../../lib/staff";

type Line = { brand: string; org: string; createdAt: string; by: string };

const csvCell = (value: string) => `"${value.replaceAll('"', '""')}"`;

/** The lines as a file the accountant opens in a spreadsheet (a BOM so that accents survive). */
function download(lines: Line[], month: string) {
  const rows = [["Marque", "Client", "Créée le", "Créée par"], ...lines.map((line) => [line.brand, line.org, line.createdAt.slice(0, 10), line.by])];
  const blob = new Blob(["﻿" + rows.map((row) => row.map(csvCell).join(";")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `marques-${month || "toutes"}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

/** /interne/facturation — the brands created, by month: what to invoice. */
export function Billing() {
  const orgs = useStaffOrgs();
  const [month, setMonth] = useState("");
  const all: Line[] = (orgs.data?.organizations ?? []).flatMap((org) =>
    org.brands.map((brand) => ({ brand: brand.name, org: org.name, createdAt: brand.created_at, by: brand.created_by ?? "" })),
  ).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const months = [...new Set(all.map((line) => line.createdAt.slice(0, 7)))];
  const lines = month ? all.filter((line) => line.createdAt.startsWith(month)) : all;
  const perOrg = new Map<string, number>();
  for (const line of lines) perOrg.set(line.org, (perOrg.get(line.org) ?? 0) + 1);

  return (
    <>
      <PageHeader
        title="Facturation"
        actions={<Button variant="primary" disabled={!lines.length} onClick={() => download(lines, month)}>Exporter en CSV</Button>}
      />
      {orgs.isLoading ? <Skeleton className="h-60" /> : null}
      {orgs.error ? <EmptyState title="Impossible de charger la facturation" body={errorMessage(orgs.error)} /> : null}
      {orgs.data ? (
        <div className="grid gap-4">
          <Section
            title={`${lines.length} ${lines.length > 1 ? "marques" : "marque"} à facturer`}
            description={[...perOrg].map(([org, count]) => `${org} : ${count}`).join(" · ") || undefined}
            aside={
              <Select aria-label="Mois" value={month} onChange={(event) => setMonth(event.target.value)} className="w-44">
                <option value="">Tous les mois</option>
                {months.map((value) => <option key={value} value={value}>{value}</option>)}
              </Select>
            }
          >
            <Table
              head={["Marque", "Client", "Créée le", "Par"]}
              align={["left", "left", "left", "left"]}
              rows={lines.map((line) => [line.brand, line.org, formatDate(line.createdAt), line.by || "·"])}
              empty="Aucune marque sur cette période."
            />
          </Section>
        </div>
      ) : null}
    </>
  );
}
