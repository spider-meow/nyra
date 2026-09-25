import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import type { Brand, Organization } from "../types";
import { api } from "./api";

export function useOrganizations(enabled = true) {
  return useQuery({
    queryKey: ["orgs"],
    queryFn: () => api.get<{ organizations: Organization[] }>("/orgs").then((data) => data.organizations),
    enabled,
    staleTime: 5 * 60_000,
  });
}

/** Interface path of a brand: brandLink(org, "louis-xiii", "bibliotheque") -> /o/<org>/m/louis-xiii/bibliotheque */
export function brandLink(org: Organization, brandSlug: string, path = ""): string {
  return `/o/${org.slug}/m/${brandSlug}${path ? `/${path}` : ""}`;
}

type OrgState = {
  org: Organization;
  /** The brand on screen: its library, its sites, its matches. */
  brand: Brand;
  admin: boolean;
  /** API path under this brand: apiPath("/library") -> /orgs/<id>/brands/<id>/library */
  apiPath: (path: string) => string;
  /** API path under the organization (settings, brands): orgApiPath("/settings") -> /orgs/<id>/settings */
  orgApiPath: (path: string) => string;
  /** Interface path under this brand: link("bibliotheque") -> /o/<org>/m/<brand>/bibliotheque */
  link: (path?: string) => string;
};

const OrgContext = createContext<OrgState | null>(null);

export function OrgProvider(props: { org: Organization; brand: Brand; children: ReactNode }) {
  const { org, brand } = props;
  const value: OrgState = {
    org,
    brand,
    admin: org.role === "admin",
    apiPath: (path) => `/orgs/${org.org_id}/brands/${brand.id}${path}`,
    orgApiPath: (path) => `/orgs/${org.org_id}${path}`,
    link: (path = "") => brandLink(org, brand.slug, path),
  };
  return <OrgContext.Provider value={value}>{props.children}</OrgContext.Provider>;
}

export function useOrg(): OrgState {
  const value = useContext(OrgContext);
  if (!value) throw new Error("OrgProvider manquant.");
  return value;
}
