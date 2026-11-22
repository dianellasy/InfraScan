# InfraScan frontend

The React app provides authenticated road-image and video submission, job
polling, and Issue map / detail / review flows.

## Authentication

Sign-in uses **Supabase Auth** (email/password) through `@supabase/supabase-js`.
Configure only:

- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_PUBLISHABLE_KEY`

Never put a Supabase secret or service-role key in Vite variables. The shared
API helper (`src/api/request.ts`) attaches `Authorization: Bearer <access
token>` to InfraScan API calls. Evidence images cannot use a plain `<img src>`
(browsers omit the header), so `EvidenceImage` fetches with the token and shows
a short-lived object URL.

`AuthGate` restores the session on reload, shows Esther's login page when signed
out, and returns the user to sign-in on HTTP 401. Sign-out lives in the top bar.

Roles are displayed from `app_metadata` for UX only; the backend enforces access.

## Run locally

From this directory:

```bash
cp .env.example .env   # fill VITE_SUPABASE_* and optional VITE_API_BASE_URL
npm install
npm run dev
```

By default the app calls `http://127.0.0.1:8000`. The API must allow the Vite
origin (`CORS_ALLOWED_ORIGINS`, default `http://localhost:5173`) and have
`SUPABASE_URL` set so tokens verify.

```bash
npm test
npm run typecheck
npm run build
npm run lint
```
