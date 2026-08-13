Paste this into V0 (vercel.com/v0):

---

Build a single-page landing + chat demo for a healthcare product called
"Practice Protocol Assistant" — an AI tool that answers staff questions
instantly from a practice's own protocols instead of them having to ask a
colleague or dig through folders.

**Sections, top to bottom:**

1. **Hero** — headline: "Stop hunting for the protocol. Just ask."
   Subheading: "Your practice's SOPs and guidelines, answered instantly —
   built for busy clinical teams." Clean, professional healthcare aesthetic,
   not clinical/sterile — warm neutral tones, not blue-and-white stock
   medical imagery. Short note: "Try it below with 5 free example
   protocols, or upload your own to test it on your real documents."

2. **Live chat demo widget** — a chat interface. On page load, generate a
   session ID (crypto.randomUUID()) and hold it in memory for the session.
   Send POST requests to `https://YOUR-RAILWAY-URL/chat` with body
   `{ session_id, message }`. Display the returned `answer`. Show a counter:
   "X of 5 free questions remaining" from `questions_remaining`.

3. **Lead capture gate** — when `/chat` returns `limit_reached: true`, show
   a form instead of the chat input: Name, Email, Practice name (optional),
   and a required checkbox: "I agree to be contacted about this tool and
   understand my details will be stored for that purpose." Submit to
   `POST https://YOUR-RAILWAY-URL/leads` with
   `{ session_id, name, email, practice_name, consent }`.

4. **Upload step (unlocks after lead capture succeeds)** — a drag-and-drop
   or click-to-browse file picker, max 10 files, accepting .pdf, .docx, .txt
   only. Show a required checkbox before the upload button activates:
   "By uploading, I confirm these documents do not contain patient-identifiable
   data. Documents are processed only to power this trial and are
   automatically deleted after 24 hours." Submit as multipart form data to
   `POST https://YOUR-RAILWAY-URL/upload` with fields `session_id` and
   `files`. Show a loading state during upload/processing (this can take
   10-30 seconds for 10 files). On success, show the returned `expires_at`
   time clearly ("Your documents will be available until [time] — ask away")
   and switch the chat into "your documents" mode.

5. **Trust footer** — small text: "Example protocols shown by default are
   demo content, not real patient data. Documents you upload are yours,
   processed temporarily, and deleted automatically."

**Technical requirements:**
- Single React component, Tailwind for styling, no external UI library
  dependencies beyond what's built in.
- Mobile responsive — most LinkedIn traffic will be on phone.
- Handle loading and error states on chat, lead form, and upload (including
  file-too-large and wrong-file-type errors from the backend).
- Leave `YOUR-RAILWAY-URL` as an easy-to-find constant at the top of the
  file so I can swap in my real backend URL after deploying.
