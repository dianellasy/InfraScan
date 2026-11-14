# InfraScan frontend

The React app provides a simple image submission and inference workflow:

- Select and submit one JPEG road image (`.jpg` or `.jpeg`) with required capture
  date/time, latitude, and longitude.
- Enforce the 5,000,000-byte upload limit before submission. The backend validates
  image content and the dimension limits (longest side 4032 px, shortest side
  3024 px).
- Poll the accepted inference job until it completes or fails.
- Review the job's detection classification, severity, confidence, location, and timestamp.

## Run locally

From this directory:

```bash
npm install
npm run dev
```

By default, the app calls the API at `http://127.0.0.1:8000`. Set
`VITE_API_BASE_URL` in a `.env` file to use a different API origin. The API
allows the Vite development origin `http://localhost:5173`; its application
service must be configured for inference and job requests to succeed.

Run `npm run build` and `npm run lint` to validate the frontend.
