import { useState } from "react";
import { rememberRunLink } from "../lib/route";
import { supabase } from "../lib/supabase";

/** The sign-in button, with its error text. Used on the home page. */
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
    <div>
      <button className="primary" onClick={signIn}>
        Sign in with GitHub
      </button>
      <p className="muted small" style={{ marginTop: 12 }}>
        We only read your public GitHub profile. Signing in gives us no access to your repositories.
      </p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
