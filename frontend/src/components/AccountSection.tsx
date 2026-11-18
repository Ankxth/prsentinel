import { useState } from "react";
import { deleteAccount } from "../lib/api";
import { supabase } from "../lib/supabase";

export default function AccountSection({ login }: { login: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onDelete() {
    const typed = window.prompt(
      `This permanently deletes your account, your repositories and all their results.\n\nType your GitHub username (${login}) to confirm:`,
    );
    if (typed === null) return;
    if (typed.trim().toLowerCase() !== login.toLowerCase()) {
      setError("That does not match your username. Nothing was deleted.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await deleteAccount();
      await supabase!.auth.signOut();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Delete my account</h2>
      <p className="muted small">
        Removes your account, your repositories and all stored results. This cannot be undone.
        You can sign in again later to start fresh.
      </p>
      <button className="danger" onClick={onDelete} disabled={busy}>
        {busy ? "Deleting…" : "Delete my account and data"}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
