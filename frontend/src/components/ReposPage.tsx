import { useCallback, useEffect, useState, type FormEvent } from "react";
import { addRepo, deleteRepo, listRepos, type Repo } from "../lib/api";
import { readCache, writeCache } from "../lib/cache";
import RepoPanel from "./RepoPanel";
import { Skeleton } from "./ui";

export default function ReposPage({ ready, userId }: { ready: boolean; userId: string }) {
  const cacheKey = `repos.${userId}`;
  // Show last time's list straight away, then replace it with the fresh one.
  const [repos, setRepos] = useState<Repo[] | null>(() => readCache<Repo[]>(cacheKey));
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const fresh = await listRepos();
      setRepos(fresh);
      writeCache(cacheKey, fresh);
      setLoadError(null);
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, [cacheKey]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  async function onAdd(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await addRepo(name.trim());
      setName("");
      setAgree(false);
      setAdding(false);
      await load();
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(repo: Repo) {
    if (!window.confirm(`Remove ${repo.full_name}? Its results will be deleted.`)) return;
    try {
      await deleteRepo(repo.id);
      await load();
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Repositories</h1>
          <p className="muted">Public Python repositories checked on every push and pull request.</p>
        </div>
        <button className={adding ? "" : "primary"} onClick={() => setAdding(!adding)}>
          {adding ? "Cancel" : "Add repository"}
        </button>
      </div>

      {adding && (
        <section className="card">
          <form onSubmit={onAdd}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="owner/name or a GitHub link"
              aria-label="Repository"
              autoFocus
            />
            <label className="check">
              <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              <span>
                I agree that this repository&apos;s code is sent to AI services (Groq, Gemini) to
                generate tests and explain findings.
              </span>
            </label>
            <div className="actions">
              <button className="primary" disabled={busy || !agree || name.trim() === ""}>
                {busy ? "Checking…" : "Add repository"}
              </button>
            </div>
            {formError && <p className="error">{formError}</p>}
          </form>
          <p className="muted small" style={{ marginTop: 12 }}>Public repositories only.</p>
        </section>
      )}

      {loadError && <p className="error">{loadError}</p>}
      {repos === null && !loadError && (
        <section className="card"><Skeleton lines={3} /></section>
      )}
      {repos?.length === 0 && (
        <section className="card">
          <p className="muted" style={{ margin: 0 }}>No repositories yet. Use “Add repository” to connect one.</p>
        </section>
      )}
      <ul className="repos">
        {repos?.map((repo) => (
          <li key={repo.id}>
            <div className="repo-head">
              <div>
                <a className="repo-name" href={`https://github.com/${repo.full_name}`} target="_blank" rel="noreferrer">
                  {repo.full_name}
                </a>
                <span className="muted small"> · branch {repo.default_branch}</span>
              </div>
              <div className="actions">
                <button onClick={() => setOpenId(openId === repo.id ? null : repo.id)}>
                  {openId === repo.id ? "Hide" : "Open"}
                </button>
                <button className="danger" onClick={() => onDelete(repo)}>
                  Remove
                </button>
              </div>
            </div>
            {openId === repo.id && <RepoPanel repo={repo} />}
          </li>
        ))}
      </ul>
    </>
  );
}
