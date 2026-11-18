import { useEffect, useState } from "react";
import AccountSection from "./components/AccountSection";
import ReposPage from "./components/ReposPage";
import SignIn from "./components/SignIn";
import { getMe, type Me } from "./lib/api";
import { supabase } from "./lib/supabase";
import { useSession } from "./lib/useSession";

export default function App() {
  const { session, loading } = useSession();
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<string | null>(null);

  // Once signed in, ask the backend who we are. This also creates the user row on first visit.
  useEffect(() => {
    setMe(null);
    setMeError(null);
    if (!session) return;
    getMe()
      .then(setMe)
      .catch((e: Error) => setMeError(e.message));
  }, [session]);

  if (!supabase) {
    return (
      <main>
        <section className="card">
          <h1>PRSentinel</h1>
          <p className="error">
            Login is not configured. Fill in VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY
            in frontend/.env.local, then restart the dev server.
          </p>
        </section>
      </main>
    );
  }

  if (loading) return <main><p className="muted">Loading…</p></main>;
  if (!session) return <main><SignIn /></main>;

  return (
    <main>
      <header>
        <strong>PRSentinel</strong>
        <div className="who">
          {me?.avatar_url && <img src={me.avatar_url} alt="" width={28} height={28} />}
          <span>{me?.github_login}</span>
          <button onClick={() => supabase!.auth.signOut()}>Sign out</button>
        </div>
      </header>
      {meError ? (
        <section className="card">
          <p className="error">{meError}</p>
          <button onClick={() => supabase!.auth.signOut()}>Sign out and try again</button>
        </section>
      ) : (
        me && (
          <>
            <ReposPage />
            <AccountSection login={me.github_login} />
          </>
        )
      )}
    </main>
  );
}
