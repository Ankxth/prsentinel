import { useState } from "react";
import { rememberRunLink } from "../lib/route";
import { supabase } from "../lib/supabase";

export default function SignIn() {
  const [error, setError] = useState<string | null>(null);

  async function signIn() {
    setError(null);
    rememberRunLink();
    const { error } = await supabase!.auth.signInWithOAuth({
      provider: "github",
      options: { redirectTo: window.location.origin },
    });
    if (error) setError(error.message);
  }

  return (
    <section className="card center">
      <h1>PRSentinel</h1>
      <p className="muted">
        Connects to your public GitHub repositories and checks every change with generated
        tests and vulnerability checks.
      </p>
      <button className="primary" onClick={signIn}>
        Sign in with GitHub
      </button>
      <p className="muted small">
        We only read your public GitHub profile. We never get access to your repositories
        through sign-in.
      </p>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
