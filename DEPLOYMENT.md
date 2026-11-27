# Deploying InfraScan to Vercel

The MVP runs as two Vercel projects built from this one repository, with
Cloudflare R2 for media and Supabase for login and the PostgreSQL database.
There is no RabbitMQ and no worker to host: on Vercel the API processes each
inference job itself, right after answering the upload.

Live (Hobby, account `atulchahar`):
- Web: https://infrascan-web.vercel.app (project `infrascan-web`)
- API: https://infrascan-api.vercel.app (project `infrascan-api`, `/docs` for the API)

```
Browser ──► infrascan-web (Vite, static)          Vercel project 1
   │  ▲
   │  └── signed PUT URL ──► Cloudflare R2 (photos, videos, evidence)
   ▼
infrascan-api (FastAPI)                            Vercel project 2
   ├── POST /api/v1/inference/uploads   sign an R2 upload URL
   └── POST /api/v1/inference/jobs      validate the upload, create the job,
                                        answer 202, then run the job in the
                                        same function with wait_until:
                                        ONNX model, geolocation, issue
                                        linking, evidence to R2
PostgreSQL (Supabase) ◄── API                     Supabase Auth ◄── login
```

## What changed from the Docker Compose setup

| Compose (local) | Vercel |
| --- | --- |
| RabbitMQ + `celery worker` container | No Celery. `app/adapters/background_queue.py` runs the same `InferenceJobProcessor` after the 202 response with Vercel's `wait_until`. Chosen automatically when `VERCEL=1`. Not durable: a job whose function is killed is not retried. |
| Ultralytics + PyTorch (about 1 GB) | ONNX Runtime with `weights/best.onnx` (same boxes as Ultralytics, about 0.3 s per photo) |
| Upload goes through the API (multipart) | Browser uploads straight to R2 with a signed URL. Vercel caps request bodies at 4.5 MB. |
| `postgres:16` container | Supabase Postgres through its transaction pooler |
| ffmpeg from apt | Pinned static ffmpeg 7.0.2, downloaded to `/tmp` on the first video job |

Docker Compose still works for local development with Celery and RabbitMQ.
`backend/requirements.txt` is for Docker and local runs; Vercel installs from
`backend/pyproject.toml`, which leaves Celery out on purpose (the Vercel
runtime tries to start its own Celery integration whenever Celery is
installed, and fails).

## 1. Supabase (login and database)

1. Create a free project at supabase.com (or use the team's).
2. **Project Settings → API**: copy the **Project URL** and the
   **publishable (anon) key**.
3. **Project Settings → Database → Connection string**, copy two URLs:
   - **Transaction pooler** (port `6543`): `DATABASE_URL` on Vercel.
   - **Session pooler** (port `5432`): only to create the tables.
4. Create the tables (again after every new migration):

   ```powershell
   cd backend
   $env:DATABASE_URL = "<session pooler URL, port 5432>"; alembic upgrade head
   ```

5. Block Supabase's public Data API from the app tables. The browser key can
   read any table in `public` that does not have row-level security, and the
   API connects as the table owner, so it is unaffected. In the SQL Editor:

   ```sql
   alter table public.issue_types       enable row level security;
   alter table public.issues            enable row level security;
   alter table public.inference_jobs    enable row level security;
   alter table public.input_media       enable row level security;
   alter table public.evidence_media    enable row level security;
   alter table public.detection_records enable row level security;
   alter table public.alembic_version   enable row level security;
   ```

6. **Authentication → URL Configuration**: set the Site URL to the web URL.
7. **Authentication → Users**: add users. For admin pages (Detections, review
   decisions) set the user's `app_metadata` to `{"role": "developer"}` or
   `{"role": "municipal_administrator"}`, then sign in again.

## 2. Cloudflare R2

1. The app's R2 token needs **Object Read & Write** on the bucket.
2. Allow browsers to upload. Either run
   `python scripts/configure_r2_cors.py https://infrascan-web.vercel.app http://localhost:5173`
   with an **Admin Read & Write** token, or in the Cloudflare dashboard open
   **R2 → the bucket → Settings → CORS policy** and paste:

   ```json
   [
     {
       "AllowedOrigins": ["https://infrascan-web.vercel.app", "http://localhost:5173"],
       "AllowedMethods": ["PUT"],
       "AllowedHeaders": ["Content-Type"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

3. Optional: a lifecycle rule deleting `input/` objects after 30 days cleans
   up abandoned uploads.

## 3. Deploying with the Vercel CLI

Both projects are linked from their folders (`vercel link`), so a deploy is:

```powershell
cd backend; vercel deploy --prod
```
```powershell
cd "frontend/InfraScan frontend"; vercel deploy --prod
```

The CLI uploads the local folder, so commit first and deploy from a clean
checkout of the branch you mean to ship. `.vercelignore` keeps `.env*` files,
tests and the `.pt` weights out of the upload.

### Environment variables

API (`infrascan-api`, Production):

| Name | Value |
| --- | --- |
| `DATABASE_URL` | Supabase transaction pooler URL (port 6543) |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `CORS_ALLOWED_ORIGINS` | `https://infrascan-web.vercel.app,http://localhost:5173` |
| `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Same as in `.env` |
| `INFRASCAN_DIRECT_UPLOAD_MAX_VIDEO_BYTES` | `250000000` |

Web (`infrascan-web`, Production; read at build time, so redeploy after a change):

| Name | Value |
| --- | --- |
| `VITE_API_BASE_URL` | `https://infrascan-api.vercel.app` |
| `VITE_SUPABASE_URL` | `https://<ref>.supabase.co` |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable (anon) key |

Add values with `vercel env add NAME production`. On Windows, do **not** pipe
values from Windows PowerShell 5.1 (`"x" | vercel env add ...`): it prepends
an invisible byte-order mark to the value. Type the value at the prompt, use
the dashboard, or pipe from Git Bash with `printf %s "value" | vercel env add ...`.

## 4. Check it works

1. `https://infrascan-api.vercel.app/test` says `Hello InfraScan`.
2. Sign in on the web app and submit one of the demo photos with a location.
3. The job should go `pending → processing → completed` within a few seconds,
   and show the annotated image; the issue appears on the map.
4. Stuck on `pending`: open the API project's **Logs**. A CORS error on the
   upload step in the browser console means step 2.2 is missing the origin.

## Limits on the Hobby plan

- **5 minutes per function call**, and the job runs inside the upload's call.
  A photo takes seconds. A video job downloads the MP4 (and, on a fresh
  instance, ffmpeg), then decodes and analyses up to 60 frames, so keep
  dashcam clips short (1–3 minute loop recording).
- **Function size.** Vercel allows 225 MB of project files per function
  (dependencies are packaged separately). The app and model are about 65 MB,
  so ffmpeg (160 MB) is downloaded at runtime instead of bundled.
- **4 CPU-hours a month** of function time: tens of thousands of photos.

## Updating the model

1. Put the new `best.pt` in `backend/inference/weights/`.
2. Export it with Ultralytics:
   `YOLO("best.pt").export(format="onnx", imgsz=640, dynamic=True, simplify=True, opset=17)`
3. Check it against Ultralytics on real photos:
   `python scripts/check_onnx_parity.py inference/weights/best.pt <photos...>`
4. Update `MODEL_NAME` and `MODEL_VERSION` in `inference/service.py`.
