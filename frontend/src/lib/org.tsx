import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import type { Organization } from "../types";
import { api } from "./api";

export function useOrganizations(enabled = true) {
  return useQuery({
    queryKey: ["orgs"],
    queryFn: () => api.get<{ organizations: Organization[] }>("/orgs").then((data) => data.organizations),
    enabled,
    staleTime: 5 * 60_000,
  });
}

type OrgState = {
  org: Organization;
  admin: boolean;
  /** API path under this organization: apiPath("/library") -> /orgs/<id>/library */
  apiPath: (path: string) => string;
  /** Interface path under this organization: link("bibliotheque") -> /o/<slug>/bibliotheque */
  link: (path?: string) => string;
};

const OrgContext = createContext<OrgState | null>(null);

export function OrgProvider(props: { org: Organization; children: ReactNode }) {
  const { org } = props;
  const value: OrgState = {
    org,
    admin: org.role === "admin",
    apiPath: (path) => `/orgs/${org.org_id}${path}`,
    link: (path = "") => `/o/${org.slug}${path ? `/${path}` : ""}`,
  };
  return <OrgContext.Provider value={value}>{props.children}</OrgContext.Provider>;
}

export function useOrg(): OrgState {
  const value = useContext(OrgContext);
  if (!value) throw new Error("OrgProvider manquant.");
  return value;
}
