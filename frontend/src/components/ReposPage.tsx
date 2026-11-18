import { useCallback, useEffect, useState, type FormEvent } from "react";
import { addRepo, deleteRepo, listRepos, type Repo } from "../lib/api";
import RepoPanel from "./RepoPanel";

export default function ReposPage() {
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRepos(await listRepos());
      setLoadError(null);
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onAdd(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await addRepo(name.trim());
      setName("");
      setAgree(false);
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
      <section className="card">
        <h2>Add a repository</h2>
        <form onSubmit={onAdd}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="owner/name or a GitHub link"
            aria-label="Repository"
          />
          <label className="check">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
            <span>
              I agree that this repository&apos;s code is sent to AI services (Groq, Gemini) to
              generate tests and explain findings.
            </span>
          </label>
          <button className="primary" disabled={busy || !agree || name.trim() === ""}>
            {busy ? "Checking…" : "Add repository"}
          </button>
          {formError && <p className="error">{formError}</p>}
        </form>
        <p className="muted small">Public repositories only.</p>
      </section>

      <section className="card">
        <h2>Your repositories</h2>
        {loadError && <p className="error">{loadError}</p>}
        {repos === null && !loadError && <p className="muted">Loading…</p>}
        {repos?.length === 0 && <p className="muted">None yet. Add one above.</p>}
        <ul className="repos">
          {repos?.map((repo) => (
            <li key={repo.id}>
              <div className="row">
                <div>
                  <a href={`https://github.com/${repo.full_name}`} target="_blank" rel="noreferrer">
                    {repo.full_name}
                  </a>
                  <span className="muted small"> · branch {repo.default_branch}</span>
                </div>
                <div className="actions">
                  <button onClick={() => setOpenId(openId === repo.id ? null : repo.id)}>
                    {openId === repo.id ? "Hide" : "Runs and setup"}
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
      </section>
    </>
  );
}
