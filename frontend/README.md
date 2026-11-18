# frontend

React + Vite + TypeScript dashboard.

```bash
cd frontend
cp .env.example .env.local     # VITE_API_URL points at the backend
npm install
npm run dev                    # http://localhost:5173
npm run build                  # type-check and production build
```

Deployed as a static site (Render Static Site or Vercel); `npm run build` output is `dist/`.
