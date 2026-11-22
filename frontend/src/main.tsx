import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { API_URL } from "./lib/api";
import "./index.css";

// The free API server sleeps when idle and takes about a minute to wake. Knock on its door now,
// before the sign-in check and the first screen, so the waking happens while the page loads.
// The answer is not used; "no-cors" keeps this from needing any permission.
try {
  void fetch(`${API_URL}/health`, { mode: "no-cors", cache: "no-store" }).catch(() => undefined);
} catch {
  // Warming up is only a speed-up.
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
