-- Who created a brand. Only the Nyra team (platform_staff, "Platform admin" in the interface) creates
-- brands, one billable act each: this column is the record behind the invoice.
--
-- Additive: safe to apply before deploying the code that fills it. Existing brands stay without an author.

alter table public.brands
    add column if not exists created_by uuid references auth.users(id) on delete set null;
