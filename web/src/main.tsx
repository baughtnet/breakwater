import "@fontsource-variable/archivo/wdth.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// A remembered light/dark choice wins over the system setting (see System, Account).
try {
  const theme = localStorage.getItem("bw-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch {
  /* storage unavailable: follow the system setting */
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
