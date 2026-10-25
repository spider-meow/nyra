import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Member, StaffAdmin, StaffOrg, StaffUsage, StaffUser } from "../types";
import { api } from "./api";

/** The back office's lists and what changes them. Any change refreshes every list, and the sidebar's organizations. */
export function useStaffOrgs() {
  return useQuery({ queryKey: ["staff", "orgs"], queryFn: () => api.get<{ organizations: StaffOrg[] }>("/staff/organizations") });
}

export function useStaffUsers() {
  return useQuery({ queryKey: ["staff", "users"], queryFn: () => api.get<{ users: StaffUser[] }>("/staff/users") });
}

export function useStaffAdmins() {
  return useQuery({ queryKey: ["staff", "admins"], queryFn: () => api.get<{ admins: StaffAdmin[] }>("/staff/admins") });
}

export function useStaffUsage() {
  return useQuery({ queryKey: ["staff", "usage"], queryFn: () => api.get<StaffUsage>("/staff/usage") });
}

export function useStaffMutations() {
  const client = useQueryClient();
  const done = () => {
    void client.invalidateQueries({ queryKey: ["staff"] });
    void client.invalidateQueries({ queryKey: ["orgs"] });
  };
  const options = { onSuccess: done };
  return {
    createOrg: useMutation({ mutationFn: (input: { name: string; admin_email: string }) => api.post<{ invited: boolean }>("/staff/organizations", input), ...options }),
    createBrand: useMutation({ mutationFn: (input: { orgId: string; name: string }) => api.post(`/orgs/${input.orgId}/brands`, { name: input.name }), ...options }),
    deleteBrand: useMutation({ mutationFn: (input: { orgId: string; brandId: string }) => api.del(`/orgs/${input.orgId}/brands/${input.brandId}`), ...options }),
    addMember: useMutation({ mutationFn: (input: { orgId: string; email: string; role: Member["role"] }) => api.post<{ invited: boolean }>(`/orgs/${input.orgId}/members`, { email: input.email, role: input.role }), ...options }),
    setRole: useMutation({ mutationFn: (input: { orgId: string; userId: string; role: Member["role"] }) => api.put(`/orgs/${input.orgId}/members/${input.userId}`, { role: input.role }), ...options }),
    removeMember: useMutation({ mutationFn: (input: { orgId: string; userId: string }) => api.del(`/orgs/${input.orgId}/members/${input.userId}`), ...options }),
    setMonthlyEmail: useMutation({
      mutationFn: (input: { orgId: string; userId: string; allowed: boolean }) =>
        api.put(`/staff/organizations/${input.orgId}/members/${input.userId}/notifications`, { monthly_email_allowed: input.allowed }),
      ...options,
    }),
    setLimits: useMutation({ mutationFn: (input: { orgId: string; limits: Record<string, number | null> }) => api.put(`/staff/organizations/${input.orgId}/quotas`, { limits: input.limits }), ...options }),
    addAdmin: useMutation({ mutationFn: (email: string) => api.post<{ invited: boolean }>("/staff/admins", { email }), ...options }),
    removeAdmin: useMutation({ mutationFn: (userId: string) => api.del(`/staff/admins/${userId}`), ...options }),
  };
}
