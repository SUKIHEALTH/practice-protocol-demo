# Practice Protocol Assistant — Public Demo Backend (v2: own-document upload)

FastAPI backend: try 5 free questions on example protocols, then leave your
details to unlock uploading your own documents (up to 10) and testing the
assistant against your real use case. Uploaded documents are isolated per
visitor and auto-deleted after 24 hours.

## What's new vs v1

- **`POST /upload`** — accepts up to 10 files (PDF/DOCX/TXT, 5MB each) per
  session, gated behind lead capture (`has_lead` must be true first).
- **Per-session isolation** — each visitor's documents go into their own
  Chroma collection (`session_<id>`), never mixed with anyone else's.
- **Auto-expiry** — uploaded documents and their vector index are deleted
  24 hours after upload (`OWN_DOCS_EXPIRY_HOURS` env var, default 24).
  Cleanup runs lazily on every `/chat` and `/upload` call — no separate
  worker needed.
- **`/chat` now branches** — if the session has uploaded its own documents,
  it answers from those; otherwise it falls back to the demo protocols.

## 1. Push to GitHub

```
git init
git add .
git commit -m "Demo backend v2 - own document upload"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

## 2. Deploy on Railway

Same as before — Volume mounted at `/data`, env vars `ANTHROPIC_API_KEY`,
`FREE_QUESTIONS`, `ALLOWED_ORIGIN`. New optional env vars:

- `OWN_DOCS_QUESTION_LIMIT` — questions allowed once own docs are uploaded
  (default 30, controls cost from a single visitor's session)
- `OWN_DOCS_EXPIRY_HOURS` — how long uploaded documents persist (default 24)

## 3. Frontend flow your V0 update needs

1. Demo chat as before (5 free questions, no email needed).
2. On limit reached, show: "Try it with your own protocols" → lead form.
3. On lead form success, show an upload step: drag-and-drop or file picker,
   max 10 files, with the consent text below shown *before* the upload
   button is enabled.
4. `POST /upload` as multipart form data: `session_id` + `files[]`.
5. On success, switch the chat into "your documents" mode and show the
   `expires_at` timestamp somewhere visible — visitors should know their
   data isn't kept forever.

## Required consent text before upload (do not skip this)

"By uploading, you confirm these documents do not contain patient-identifiable
data. Documents are processed only to power this trial and are automatically
deleted after 24 hours. Do not upload anything you wouldn't want processed by
a third-party AI service."

## Before you promote the upload feature

- This is a bigger liability step than the demo alone — you're now receiving
  other organisations' real internal documents on your infrastructure, even
  temporarily. The 24-hour auto-delete and consent text are the minimum, not
  the ceiling — if uptake is real, get a proper look at your data handling
  setup before this scales past a handful of testers.
- Watch Railway cost closely once this is live — embedding 10 documents per
  visitor is meaningfully more compute than 5 chat questions.
