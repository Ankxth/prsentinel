import { useEffect, useState } from "react";

// The dashboard has no page router. A run's page lives behind the part of the address after
// the "#", for example https://dashboard/#/runs/<run id>. That part is never sent to a server,
// so it works on any hosting and survives a refresh.

const RUN_HASH = /^#\/runs\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

/** The run id in an address fragment, or null if it is not a run link. */
export function runIdFromHash(hash: string): string | null {
  const match = RUN_HASH.exec(hash);
  return match ? match[1].toLowerCase() : null;
}

/** The full address of one run's page, for copying. */
export function runLink(runId: string): string {
  return `${window.location.origin}${window.location.pathname}#/runs/${runId}`;
}

export function openRunPage(runId: string): void {
  window.location.hash = `#/runs/${runId}`;
}

export function closeRunPage(): void {
  window.location.hash = "";
}

export function openHomePage(): void {
  window.location.hash = "#/home";
}

/** True when the address is the home (about) page. */
export function useIsHomeHash(): boolean {
  const [home, setHome] = useState(() => window.location.hash === "#/home");
  useEffect(() => {
    const update = () => setHome(window.location.hash === "#/home");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return home;
}

/** The run id in the current address, kept up to date when the address changes. */
export function useRunIdFromHash(): string | null {
  const [runId, setRunId] = useState<string | null>(() => runIdFromHash(window.location.hash));
  useEffect(() => {
    const update = () => setRunId(runIdFromHash(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return runId;
}

// Signing in sends the browser to GitHub and back, and the way back drops the "#..." part.
// So a run link is remembered for the trip and put back afterwards.
const AFTER_SIGN_IN = "prsentinel.afterSignIn";

export function rememberRunLink(): void {
  try {
    if (runIdFromHash(window.location.hash)) {
      sessionStorage.setItem(AFTER_SIGN_IN, window.location.hash);
    }
  } catch {
    // Storage can be blocked; then the person simply lands on the repositories page.
  }
}

export function restoreRunLink(): void {
  try {
    const saved = sessionStorage.getItem(AFTER_SIGN_IN);
    if (!saved) return;
    sessionStorage.removeItem(AFTER_SIGN_IN);
    if (runIdFromHash(saved)) window.location.hash = saved;
  } catch {
    // ignore
  }
}
