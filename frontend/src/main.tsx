import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, Suspense, lazy, useEffect, type ReactNode } from "react";
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
// every other page is fetched the first time it is opened.
const BackOffice = lazy(() => import("./pages/BackOffice").then((m) => ({ default: m.BackOffice })));
const Library = lazy(() => import("./pages/Library").then((m) => ({ default: m.Library })));
const Reports = lazy(() => import("./pages/Reports").then((m) => ({ default: m.Reports })));
const Review = lazy(() => import("./pages/Review").then((m) => ({ default: m.Review })));
const Scans = lazy(() => import("./pages/Scans").then((m) => ({ default: m.Scans })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));
const SiteImages = lazy(() => import("./pages/SiteImages").then((m) => ({ default: m.SiteImages })));
const Statistics = lazy(() => import("./pages/Statistics").then((m) => ({ default: m.Statistics })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

const hasTitle = (handle: unknown): handle is { title: string } => isRecord(handle) && typeof handle.title === "string";

/**
 * Sets the tab title from the deepest route's `handle.title`. The Suspense sits
 * inside the route element, so under the layout only the page area waits.
 */
function Titled(props: { children: ReactNode }) {
  const matches = useMatches();
  const title = [...matches].reverse().map((match) => match.handle).find(hasTitle)?.title;
  useEffect(() => {
    document.title = title ? `${title} · Nyra` : "Nyra";
  }, [title]);
  return (
    <Suspense fallback={<div className="p-8"><Spinner label="Chargement de la page…" /></div>}>
      {props.children}
    </Suspense>
  );
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

const router = createBrowserRouter([
  {
    errorElement: <PageError />,
    children: [
      { path: "/connexion", element: page(<Login />), handle: { title: "Connexion" } },
      { path: "/mot-de-passe", element: page(<SetPassword />), handle: { title: "Mot de passe" } },
      {
        element: <RequireSession />,
        children: [
          { path: "/", element: page(<OrgHome />) },
          { path: "/interne", element: page(<BackOffice />), handle: { title: "Back office" } },
          {
            path: "/o/:slug",
            element: <OrgShell />,
            children: [
              { index: true, element: <FirstBrand /> },
              {
                path: "m/:brand",
                element: <BrandShell />,
                children: [
                  { index: true, element: <Navigate to="tableau-de-bord" replace /> },
                  { path: "tableau-de-bord", element: page(<Dashboard />), handle: { title: "Tableau de bord" } },
                  { path: "a-traiter", element: page(<Review />), handle: { title: "À traiter" } },
                  { path: "bibliotheque", element: page(<Library />), handle: { title: "Bibliothèque" } },
                  { path: "images-du-site", element: page(<SiteImages />), handle: { title: "Droits non vérifiés" } },
                  { path: "lectures", element: page(<Scans />), handle: { title: "Sites et lectures" } },
                  { path: "rapports", element: page(<Reports />), handle: { title: "Rapports" } },
                  { path: "statistiques", element: page(<Statistics />), handle: { title: "Statistiques" } },
                  { path: "reglages", element: page(<Settings />), handle: { title: "Réglages" } },
                  { path: "*", element: page(<NotFound />), handle: { title: "Page introuvable" } },
                ],
              },
              // Links from before brands: /o/:slug/bibliotheque -> the first brand's library.
              { path: "*", element: <FirstBrand /> },
            ],
          },
        ],
      },
      { path: "*", element: page(<NotFound />), handle: { title: "Page introuvable" } },
    ],
  },
]);

void startMonitoring(router); // after the first render: it waits for the page to load and for the server's configuration

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
