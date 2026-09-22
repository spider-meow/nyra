import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AuthGate } from "./auth";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Racine introuvable.");
createRoot(root).render(
  <StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </StrictMode>,
);
