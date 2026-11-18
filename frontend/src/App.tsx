import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type Health = { status: string; service: string; version: string; env: string };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 560, margin: "4rem auto" }}>
      <h1>PRSentinel</h1>
      <p>Backend status:</p>
      {health && (
        <p>
          ✅ {health.service} v{health.version} ({health.env})
        </p>
      )}
      {error && <p>❌ Cannot reach the backend: {error}</p>}
      {!health && !error && <p>Checking…</p>}
    </main>
  );
}
