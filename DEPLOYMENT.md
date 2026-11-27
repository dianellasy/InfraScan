# Deploying InfraScan to Vercel

The MVP runs as two Vercel projects built from this one repository, with
Cloudflare R2 for media and a hosted PostgreSQL database. There is no
RabbitMQ and no always-on worker: Celery tasks run on Vercel Queues.

```
Browser ──► infrascan-web (Vite, static)          Vercel project 1
   │  ▲
   │  └── signed PUT URL ──► Cloudflare R2 (photos, videos, evidence)
   ▼
infrascan-api (FastAPI)                            Vercel project 2
   ├── POST /api/v1/inference/uploads   sign an R2 upload URL
   ├── POST /api/v1/inference/jobs      validate the upload, create the job,
   │                                    publish it (Celery → Vercel Queues)
   └── worker (private function)        Celery task: ONNX model, geolocation,
                                        issue linking, evidence to R2
PostgreSQL (Supabase) ◄── both functions          Supabase Auth ◄── login
```

## What changed from the Docker Compose setup

| Compose (local) | Vercel |
| --- | --- |
| RabbitMQ + `celery worker` container | Celery with `broker="vercel://"`. `worker.py` becomes a private, queue-triggered function. Chosen automatically when `VERCEL=1`. |
| Ultralytics + PyTorch (about 1 GB) | ONNX Runtime with `weights/best.onnx` (same boxes as Ultralytics, about 0.3 s per photo) |
| Upload goes through the API (multipart) | Browser uploads straight to R2 with a signed URL. Vercel caps request bodies at 4.5 MB. |
| `postgres:16` container | Hosted PostgreSQL through a transaction pooler |
| ffmpeg from apt | Pinned static ffmpeg 7.0.2, bundled by `scripts/vercel_build.py` |

Docker Compose still works for local development, unchanged apart from the
lighter worker image.

## 1. Database (Supabase Postgres)

The team already has a Supabase project for login, and its free Postgres is
the simplest database. (Neon from the Vercel Marketplace works the same way.)

1. In Supabase: **Project Settings → Database → Connection string**.
2. Copy two URLs:
   - **Transaction pooler** (port `6543`): this is `DATABASE_URL` on Vercel.
   - **Direct connection** or **Session pooler** (port `5432`): used only to
     run migrations from your computer.
3. Create the tables once, and again after every new migration:

   ```powershell
   cd backend
   $env:DATABASE_URL = "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
   alembic upgrade head
   ```

## 2. Cloudflare R2

1. Use the existing bucket. Create an R2 API token with **Object Read & Write**
   on it for the app.
2. Allow the browser to upload to it. This needs a token with **Admin Read &
   Write**, used once from your computer:

   ```powershell
   cd backend
   python scripts/configure_r2_cors.py https://<web-project>.vercel.app http://localhost:5173
   ```

   Add every origin the frontend is served from, such as preview URLs you
   want to test uploads on.
3. Optional: add an R2 lifecycle rule that deletes `input/` objects after 30
   days, to clean up abandoned uploads.

## 3. Vercel project: API

**Add New → Project**, import the repository, then:

- **Root Directory:** `backend`
- **Framework Preset:** FastAPI (detected from `pyproject.toml`)
- **Environment Variables:**

| Name | Value |
| --- | --- |
| `DATABASE_URL` | Supabase transaction pooler URL (port 6543) |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `CORS_ALLOWED_ORIGINS` | `https://<web-project>.vercel.app` (comma-separate several) |
| `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Same as in `.env` |
| `INFRASCAN_DIRECT_UPLOAD_MAX_VIDEO_BYTES` | `250000000` (see "Limits" below) |

Do not set `CELERY_BROKER_URL` or `CELERY_RESULT_BACKEND`: on Vercel they
default to Vercel Queues and Vercel's runtime cache.

Deploy, then open `https://<api-project>.vercel.app/test`. It should say
`Hello InfraScan`. `/docs` shows the API.

## 4. Vercel project: web

Import the same repository again as a second project:

- **Root Directory:** `frontend/InfraScan frontend`
- **Framework Preset:** Vite
- **Environment Variables:**

| Name | Value |
| --- | --- |
| `VITE_API_BASE_URL` | `https://<api-project>.vercel.app` |
| `VITE_SUPABASE_URL` | `https://<ref>.supabase.co` |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable (anon) key |

Then, in Supabase, go to **Authentication → URL Configuration** and add the web
URL to the allowed redirect URLs.

## 5. Check it works

1. Sign in on the web app and submit one of the demo photos with a location.
2. The job should go `pending → processing → completed` within a few seconds,
   and the issue should appear on the map.
3. If it stays `pending`, open the API project's **Logs** and filter for the
   worker function. If the upload step fails with a CORS error in the browser
   console, re-run step 2.2 with the exact web origin.

## Limits on the Hobby plan

- **5 minutes per function call.** A photo job takes seconds. A video job
  downloads the MP4, then decodes and analyses up to 60 frames, so keep
  dashcam clips short (1–3 minute loop recording) and leave
  `INFRASCAN_DIRECT_UPLOAD_MAX_VIDEO_BYTES` at about 250 MB.
- **Function size.** The API function is about 500 MB with ffmpeg bundled.
  New projects switch to "large functions" automatically when a function is
  over 500 MB. If a deploy still fails on size, set
  `INFRASCAN_BUNDLE_FFMPEG=0`: video jobs then download ffmpeg (42 MB, checked
  against a pinned SHA-256) to `/tmp` on first use. Photos never need it.
- **4 CPU-hours a month** of function time: tens of thousands of photos.
- Vercel Queues, Services and large functions are in beta.

## Updating the model

1. Put the new `best.pt` in `backend/inference/weights/`.
2. Export it with Ultralytics:
   `YOLO("best.pt").export(format="onnx", imgsz=640, dynamic=True, simplify=True, opset=17)`
3. Check it against Ultralytics on real photos:
   `python scripts/check_onnx_parity.py inference/weights/best.pt <photos...>`
4. Update `MODEL_NAME` and `MODEL_VERSION` in `inference/service.py`.
