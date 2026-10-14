import { Navigate, useLocation, useParams } from "react-router";
import { brandLink } from "../lib/org";
import { currentPage, legacySearch, legacyTarget } from "../lib/paths";
import { FirstBrand, useShellOrg } from "./Access";

// Fetched only when an old address is opened: the table of old names stays out of the first load.

/** An address from before English paths (`/connexion`, `/o/x/m/y/bibliotheque`): its new one, fragment included (see paths.ts). */
export function LegacyRedirect() {
  const { pathname, search, hash } = useLocation();
  return <Navigate to={legacyTarget(pathname, search, hash) ?? "/"} replace />;
}

/** /o/:slug/<page>, a link from before brands (`/o/x/library`, or the old `/o/x/bibliotheque`): that page of the first brand. */
export function FirstBrandPage() {
  const org = useShellOrg();
  const params = useParams();
  const location = useLocation();
  const first = org.brands[0];
  if (!first) return <FirstBrand />;
  const [page = "dashboard", ...rest] = (params["*"] ?? "").split("/").filter(Boolean);
  return <Navigate to={`${brandLink(org, first.slug, [currentPage(page), ...rest].join("/"))}${legacySearch(location.search)}${location.hash}`} replace />;
}
