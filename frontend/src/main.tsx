import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useEffect, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider, useMatches } from "react-router";
import { ConfirmProvider, ToastProvider } from "./components/feedback";
import { ApiError } from "./lib/api";
import { AuthProvider } from "./lib/auth";
import { Login, NotFound, OrgHome, OrgShell, RequireSession, SetPassword } from "./pages/Access";
import { Dashboard } from "./pages/Dashboard";
import { Library } from "./pages/Library";
import { Reports } from "./pages/Reports";
import { Review } from "./pages/Review";
import { Scans } from "./pages/Scans";
import { Settings } from "./pages/Settings";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

type Handle = { title: string };

/** Sets the tab title from the deepest route's `handle.title`. */
function Titled(props: { children: ReactNode }) {
  const matches = useMatches();
  const title = [...matches].reverse().find((match) => (match.handle as Handle | undefined)?.title)?.handle as Handle | undefined;
  useEffect(() => {
    document.title = title ? `${title.title} · Nyra` : "Nyra";
  }, [title]);
  return <>{props.children}</>;
}

const page = (element: ReactNode) => <Titled>{element}</Titled>;

const router = createBrowserRouter([
  { path: "/connexion", element: page(<Login />), handle: { title: "Connexion" } },
  { path: "/mot-de-passe", element: page(<SetPassword />), handle: { title: "Mot de passe" } },
  {
    element: <RequireSession />,
    children: [
      { path: "/", element: page(<OrgHome />) },
      {
        path: "/o/:slug",
        element: <OrgShell />,
        children: [
          { index: true, element: <Navigate to="tableau-de-bord" replace /> },
          { path: "tableau-de-bord", element: page(<Dashboard />), handle: { title: "Tableau de bord" } },
          { path: "a-traiter", element: page(<Review />), handle: { title: "À traiter" } },
          { path: "bibliotheque", element: page(<Library />), handle: { title: "Bibliothèque" } },
          { path: "lectures", element: page(<Scans />), handle: { title: "Lectures du site" } },
          { path: "rapports", element: page(<Reports />), handle: { title: "Rapports" } },
          { path: "reglages", element: page(<Settings />), handle: { title: "Réglages" } },
          { path: "*", element: page(<NotFound />), handle: { title: "Page introuvable" } },
        ],
      },
    ],
  },
  { path: "*", element: page(<NotFound />), handle: { title: "Page introuvable" } },
]);

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
