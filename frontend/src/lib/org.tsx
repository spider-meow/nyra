import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import type { Brand, Organization } from "../types";
import { api } from "./api";
import { useAuth } from "./auth";
import { isRecord, readStored, storageKey, writeStored } from "./storage";

const ORGS_COPY_MAX_AGE_MS = 5 * 60_000;
const isOrgsCopy = (value: unknown): value is { savedAt: number; answer: Organization[] } =>
  isRecord(value) && typeof value.savedAt === "number" && Array.isArray(value.answer) && value.answer.every((org) => isRecord(org) && Array.isArray(org.brands));

/**
 * The organizations (and their brands) decide which page and which requests start, so every request waits for them.
 * A copy from the last minutes is shown at once and refreshed straight away (`initialDataUpdatedAt: 0`);
 * the server still checks every request.
 */
export function useOrganizations(enabled = true) {
  const { session } = useAuth();
  const key = session ? storageKey(session.user.id, "-", "orgs") : "";
  return useQuery({
    queryKey: ["orgs"],
    queryFn: async ({ signal }) => {
      const { organizations } = await api.get<{ organizations: Organization[] }>("/orgs");
      if (key && !signal.aborted) writeStored("local", key, { savedAt: Date.now(), answer: organizations });
      return organizations;
    },
    enabled,
    staleTime: 5 * 60_000,
    initialData: () => {
      const copy = key ? readStored("local", key, isOrgsCopy) : undefined;
      return copy && Date.now() - copy.savedAt <= ORGS_COPY_MAX_AGE_MS ? copy.answer : undefined;
    },
    initialDataUpdatedAt: 0,
  });
}

/** Interface path of a brand: brandLink(org, "louis-xiii", "library") -> /o/<org>/b/louis-xiii/library */
export function brandLink(org: Organization, brandSlug: string, path = ""): string {
  return `/o/${org.slug}/b/${brandSlug}${path ? `/${path}` : ""}`;
}

/** The signed-in account: whether it belongs to the Nyra team (back office). */
export function useMe(enabled = true) {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<{ user_id: string; email: string | null; staff: boolean }>("/me"),
    enabled,
    staleTime: 5 * 60_000,
  });
}

/** The space of a client opened by a platform admin who is not a member: not in `useOrganizations`, asked by its address. */
export function useStaffOpenedOrganization(slug: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["orgs", "open", slug], // under "orgs": whatever refreshes the organizations (a brand created) refreshes this one
    queryFn: async () => (await api.get<{ organization: Organization }>(`/staff/organizations/open/${encodeURIComponent(slug ?? "")}`)).organization,
    enabled: enabled && Boolean(slug),
    staleTime: 5 * 60_000,
  });
}

type OrgState = {
  org: Organization;
  /** The brand on screen: its library, its sites, its matches. */
  brand: Brand;
  admin: boolean;
  /** Nyra team ("Platform admin"): the only accounts that create and delete brands. */
  platformAdmin: boolean;
  /** Opened by the Nyra team without being a member: a banner says so, and nothing of the person's own account (notifications) applies. */
  staffView: boolean;
  /** API path under this brand: apiPath("/library") -> /orgs/<id>/brands/<id>/library */
  apiPath: (path: string) => string;
  /** API path under the organization (settings, brands): orgApiPath("/settings") -> /orgs/<id>/settings */
  orgApiPath: (path: string) => string;
  /** Interface path under this brand: link("library") -> /o/<org>/b/<brand>/library */
  link: (path?: string) => string;
};

const OrgContext = createContext<OrgState | null>(null);

export function OrgProvider(props: { org: Organization; brand: Brand; children: ReactNode }) {
  const { org, brand } = props;
  const me = useMe();
  const value: OrgState = {
    org,
    brand,
    admin: org.role === "admin" || Boolean(me.data?.staff), // the server gives a platform admin the rights of an administrator everywhere
    platformAdmin: Boolean(me.data?.staff),
    staffView: Boolean(org.staff_view),
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
