# frontend

React + Vite + TypeScript dashboard.

```bash
cd frontend
cp .env.example .env.local     # fill in the three VITE_ values
npm install
npm run dev                    # http://localhost:5173
npm run build                  # type-check and production build
```

`.env.local` values (only `VITE_` values reach the browser, so no secrets):
- `VITE_API_URL`: the backend address (`http://localhost:8000` locally)
- `VITE_SUPABASE_URL`: the Supabase project URL
- `VITE_SUPABASE_PUBLISHABLE_KEY`: the publishable (anon) key. Never the secret key.

## Layout

```
src/
  App.tsx                  signed-out / signed-in switch, header
  components/              SignIn.tsx, ReposPage.tsx
  lib/supabase.ts          Supabase client (null if not configured)
  lib/api.ts               backend calls; sends the sign-in token automatically
  lib/useSession.ts        current sign-in
```

Deployed as a static site (Render Static Site or Vercel); `npm run build` output is `dist/`.
