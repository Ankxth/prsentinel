import { lazy, Suspense, useEffect, useState } from "react";
import AccountSection from "./components/AccountSection";
import ReposPage from "./components/ReposPage";
import Home from "./components/Home";
import Logo from "./components/Logo";
import SignIn from "./components/SignIn";
import { Skeleton } from "./components/ui";
import { getMe, type Me } from "./lib/api";
import { clearCache, readCache, writeCache } from "./lib/cache";
import { closeRunPage, openHomePage, restoreRunLink, useIsHomeHash, useRunIdFromHash } from "./lib/route";
import { useTheme } from "./lib/theme";
import { supabase } from "./lib/supabase";
import { useSession } from "./lib/useSession";

// The run page is only needed when someone opens a run, so it is loaded on demand.
const RunView = lazy(() => import("./components/RunView"));

function ThemeButton({ theme, onToggle }: { theme: string; onToggle: () => void }) {
  return (
    <button className="icon-btn" onClick={onToggle} aria-label="Switch between dark and light theme">
      {theme === "dark" ? "Light theme" : "Dark theme"}
    </button>
  );
}

export default function App() {
  const { session, loading } = useSession();
  const userId = session?.user.id ?? null;
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  // True once the user's row is known to exist. With a remembered profile that is immediately
  // true, so the repository list can be fetched at the same time as the profile.
  const [ready, setReady] = useState(false);
  const runId = useRunIdFromHash();
  const isHome = useIsHomeHash();
  const [theme, toggleTheme] = useTheme();

  useEffect(() => {
    setMe(null);
    setMeError(null);
    setReady(false);
    if (!userId) return;
    restoreRunLink();
    const remembered = readCache<Me>(`me.${userId}`);
    if (remembered) {
      setMe(remembered);
      setReady(true);
    }
    let cancelled = false;
    getMe()
      .then((fresh) => {
        if (cancelled) return;
        setMe(fresh);
        setReady(true);
        writeCache(`me.${userId}`, fresh);
      })
      .catch((e: Error) => {
        if (!cancelled) setMeError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  async function signOut() {
    clearCache();
    await supabase!.auth.signOut();
  }

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

  if (loading) return <main><Skeleton lines={4} /></main>;
  if (!session) {
    return (
      <>
        <header className="topbar">
          <div className="topbar-inner">
            <span className="brand"><Logo />PRSentinel</span>
            <ThemeButton theme={theme} onToggle={toggleTheme} />
          </div>
        </header>
        <main><Home action={<SignIn />} /></main>
      </>
    );
  }

  // The top bar can be drawn from the sign-in itself, before the server has answered.
  const meta = session.user.user_metadata as { avatar_url?: string; user_name?: string };
  const avatar = me?.avatar_url ?? meta.avatar_url;
  const login = me?.github_login ?? meta.user_name ?? "";

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="row" style={{ gap: 4 }}>
            <button className="brand" onClick={closeRunPage} aria-label="PRSentinel repositories">
              <Logo />
              PRSentinel
            </button>
            <nav className="nav">
              <button className={!isHome ? "active" : ""} onClick={closeRunPage}>Repositories</button>
              <button className={isHome ? "active" : ""} onClick={openHomePage}>About</button>
            </nav>
          </div>
          <div className="who">
            <ThemeButton theme={theme} onToggle={toggleTheme} />
            {avatar && <img src={avatar} alt="" width={28} height={28} />}
            <span className="who-name">{login}</span>
            <button onClick={signOut}>Sign out</button>
          </div>
        </div>
      </header>
      <main>
        {meError ? (
          <section className="card">
            <p className="error">{meError}</p>
            <button onClick={signOut}>Sign out and try again</button>
          </section>
        ) : isHome ? (
          <Home action={<button className="primary" onClick={closeRunPage}>Go to my repositories</button>} />
        ) : runId ? (
          <Suspense fallback={<Skeleton lines={5} />}>
            <RunView runId={runId} />
          </Suspense>
        ) : (
          <>
            <ReposPage ready={ready} userId={session.user.id} />
            {me && <AccountSection login={me.github_login} />}
          </>
        )}
      </main>
    </>
  );
}
