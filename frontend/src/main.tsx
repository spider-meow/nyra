import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useEffect, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider, useMatches, useRouteError } from "react-router";
import { ConfirmProvider, ToastProvider } from "./components/feedback";
import { ApiError } from "./lib/api";
import { AuthProvider } from "./lib/auth";
import { reportError, startMonitoring } from "./lib/monitoring";
import { isRecord } from "./lib/storage";
import { BrandShell, FirstBrand, Login, NotFound, OrgHome, OrgShell, RequireSession, SetPassword } from "./pages/Access";
import { Dashboard } from "./pages/Dashboard";
import { Button, Spinner } from "./components/ui";
import "./index.css";

// Dashboard (the landing page) and the access screens stay in the entry chunk;
// every other page is fetched when its address is opened (`lazyPage` below).

// The font stylesheet is third-party: it must not hold back the first paint (the text swaps once it has loaded).
document.querySelector("link[data-fonts]")?.setAttribute("media", "all");

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A page opened right after another one reuses what the shell just fetched (overview, jobs) instead of asking again.
      staleTime: 20_000,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

const hasTitle = (handle: unknown): handle is { title: string } => isRecord(handle) && typeof handle.title === "string";

/** Sets the tab title from the deepest route's `handle.title`. */
function Titled(props: { children: ReactNode }) {
  const matches = useMatches();
  const title = [...matches].reverse().map((match) => match.handle).find(hasTitle)?.title;
  useEffect(() => {
    document.title = title ? `${title} · Nyra` : "Nyra";
  }, [title]);
  return props.children;
}

/** A page that cannot load (typically a file replaced by a new deploy): offer a reload instead of the router's developer screen. */
function PageError() {
  const error = useRouteError();
  useEffect(() => reportError(error), [error]);
  return (
    <div className="grid min-h-screen place-items-center p-8 text-center">
      <div>
        <p className="font-display text-2xl">Cette page n'a pas pu se charger</p>
        <p className="mt-2 text-sm text-muted">Nyra a peut-être été mis à jour. Rechargez pour continuer.</p>
        <Button variant="primary" className="mt-5" onClick={() => window.location.reload()}>Recharger</Button>
      </div>
    </div>
  );
}

const page = (element: ReactNode) => <Titled>{element}</Titled>;

/** An address from before English paths (`/connexion`, `/o/x/m/y/bibliotheque`): its new one (pages/Legacy.tsx, fetched only then). */
const legacy = () => import("./pages/Legacy").then((module) => ({ Component: module.LegacyRedirect }));

/**
 * A page fetched when its address is opened. The router waits for the file before it shows the route, in parallel
 * with the rest of the start-up: a `React.lazy` page would first show a fallback, and React holds the real page
 * back for about 300 ms after a fallback was shown.
 */
const lazyPage = (load: () => Promise<ComponentType>) => async () => {
  const Page = await load();
  return { element: page(<Page />) };
};

const router = createBrowserRouter([
  {
    errorElement: <PageError />,
    HydrateFallback: () => <div className="p-8"><Spinner label="Chargement…" /></div>,
    children: [
      { path: "/login", element: page(<Login />), handle: { title: "Connexion" } },
      { path: "/set-password", element: page(<SetPassword />), handle: { title: "Mot de passe" } },
      // Public and linked from nowhere but the robot's signature (config.yaml `crawl.user_agent`); the server marks it noindex.
      { path: "/robot", lazy: lazyPage(() => import("./pages/Robot").then((module) => module.Robot)), handle: { title: "Le robot Nyra" } },
      // The French addresses of before: bookmarks and e-mails already sent keep working (see paths.ts).
      { path: "/connexion", lazy: legacy },
      { path: "/mot-de-passe", lazy: legacy },
      { path: "/interne/*", lazy: legacy },
      { path: "/o/:slug/m/*", lazy: legacy },
      {
        element: <RequireSession />,
        children: [
          { path: "/", element: page(<OrgHome />) },
          {
            path: "/back-office",
            lazy: lazyPage(() => import("./pages/BackOffice").then((module) => module.BackOffice)),
            handle: { title: "Back office" },
            children: [
              { index: true, lazy: lazyPage(() => import("./pages/BackOffice").then((module) => module.Overview)), handle: { title: "Back office" } },
              { path: "organizations", lazy: lazyPage(() => import("./pages/backoffice/Organizations").then((module) => module.Organizations)), handle: { title: "Organisations et marques" } },
              { path: "billing", lazy: lazyPage(() => import("./pages/backoffice/Billing").then((module) => module.Billing)), handle: { title: "Facturation" } },
              { path: "accounts", lazy: lazyPage(() => import("./pages/backoffice/Accounts").then((module) => module.Accounts)), handle: { title: "Comptes" } },
              { path: "platform-admins", lazy: lazyPage(() => import("./pages/backoffice/Admins").then((module) => module.Admins)), handle: { title: "Platform admins" } },
            ],
          },
          {
            path: "/o/:slug",
            element: <OrgShell />,
            children: [
              { index: true, element: <FirstBrand /> },
              {
                path: "b/:brand",
                element: <BrandShell />,
                children: [
                  { index: true, element: <Navigate to="dashboard" replace /> },
                  { path: "dashboard", element: page(<Dashboard />), handle: { title: "Tableau de bord" } },
                  { path: "review", lazy: lazyPage(() => import("./pages/Review").then((module) => module.Review)), handle: { title: "À traiter" } },
                  { path: "visuals", lazy: lazyPage(() => import("./pages/Visuals").then((module) => module.Visuals)), handle: { title: "Visuels à remplacer" } },
                  { path: "visuals/:referenceId", lazy: lazyPage(() => import("./pages/VisualPage").then((module) => module.VisualPage)), handle: { title: "Visuel" } },
                  { path: "library", lazy: lazyPage(() => import("./pages/Library").then((module) => module.Library)), handle: { title: "Bibliothèque" } },
                  { path: "online-images", lazy: lazyPage(() => import("./pages/SiteImages").then((module) => module.SiteImages)), handle: { title: "Images en ligne" } },
                  { path: "site-images", lazy: legacy },
                  { path: "scans", lazy: lazyPage(() => import("./pages/Scans").then((module) => module.Scans)), handle: { title: "Sites et lectures" } },
                  { path: "reports", lazy: lazyPage(() => import("./pages/Reports").then((module) => module.Reports)), handle: { title: "Rapports" } },
                  { path: "statistics", lazy: lazyPage(() => import("./pages/Statistics").then((module) => module.Statistics)), handle: { title: "Statistiques" } },
                  { path: "settings", lazy: lazyPage(() => import("./pages/Settings").then((module) => module.Settings)), handle: { title: "Réglages" } },
                  { path: "*", element: page(<NotFound />), handle: { title: "Page introuvable" } },
                ],
              },
              // Links from before brands (/o/:slug/library, or the old /o/:slug/bibliotheque) -> that page of the first brand.
              { path: "*", lazy: () => import("./pages/Legacy").then((module) => ({ Component: module.FirstBrandPage })) },
            ],
          },
        ],
      },
      { path: "*", element: page(<NotFound />), handle: { title: "Page introuvable" } },
    ],
  },
]);

void startMonitoring(router, queryClient); // starts before the first render but waits for the page to load and for the server's configuration

const root = document.getElementById("root");
if (!root) throw new Error("Racine introuvable.");
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToastProvider>
          <ConfirmProvider>
            <RouterProvider router={router} />
          </ConfirmProvider>
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
