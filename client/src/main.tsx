import "@fontsource-variable/inter";
import "@fontsource/lilita-one";
import "./index.css";
import { App } from "@/App";
import { applyStoredTheme } from "@/components/theme-toggle";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

applyStoredTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
