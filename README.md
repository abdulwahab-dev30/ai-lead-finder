# AI Lead Finder

A Vercel-ready, single-repository React + Vite + FastAPI application with Supabase Auth and Postgres persistence.

## What it does
- Premium prospecting dashboard for software development agencies.
- Search-link generator for USA, UK, Canada and Australia.
- Manual company URL import (paste lines or CSV in the UI).
- Public website scanning for generic, role-based business inboxes only.
- Robots.txt-aware crawling, same-domain page discovery and basic SSRF protections.
- Simple transparent lead scoring, source-page tracking, duplicate-safe upsert.
- Lead status management and CSV export.
- Supabase authentication; backend admin allowlist; per-user lead ownership.
- No paid search API, paid LLM API, email-enrichment service, or auto-email sending.

## Important product limitations
There is no dependable, unrestricted, free global company-search API. The app generates targeted search links; the operator selects official company websites and pastes them into the scanner. This avoids brittle search-engine scraping and paid API dependencies.
A free-tier-compatible scan request processes at most 3 websites. To process 20–30 leads, the UI sends sequential small batches. Sites can block crawlers or hide email addresses, so results are not guaranteed.
The lead score is keyword-based, not an AI/LLM judgement in this first release. A public email does not mean consent to marketing. Review all contacts and comply with applicable anti-spam/privacy laws.

## GitHub and Vercel setup

### 1. Create the GitHub repository
Create a new **private** or public repository named `ai-lead-finder` on GitHub. Upload/push the complete contents of this folder to the repository root (not the parent folder).

### 2. Create Supabase project
1. Create a Supabase project.
2. Open SQL Editor and run `supabase/schema.sql`.
3. In Project Settings -> API, copy Project URL, publishable/anon key, and service-role/secret key. In API settings, ensure the `public` schema is exposed to the Data API (new Supabase projects may not expose it by default).
4. In Auth -> Users, create your admin user with email/password.
5. Add that exact email to Vercel's `ADMIN_EMAILS`.
6. Keep public signups disabled if only you should use this app.

The schema enables RLS. The frontend only uses the publishable key for Supabase Auth. The service-role key is used only by the server-side FastAPI function and must never be prefixed with `VITE_` or committed to GitHub.

### 3. Deploy to Vercel
1. Import the GitHub repository in Vercel.
2. Framework preset: Other (or let Vercel detect the Python entrypoint).
3. Root directory: `./`.
4. Add environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `ADMIN_EMAILS`
   - `VITE_SUPABASE_URL`
   - `VITE_SUPABASE_ANON_KEY`
5. Deploy. `build.py` installs the frontend packages and builds Vite; FastAPI serves the built frontend and `/api/*` routes.

### 4. Local development
Backend:
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload
```
Frontend (second terminal):
```bash
cd frontend
npm install
npm run dev
```
For local frontend development, set `VITE_API_BASE=http://127.0.0.1:8000` in `frontend/.env.local` and configure the backend environment variables in your shell.

## Scanning workflow
1. Sign in with the Supabase admin account.
2. Choose a country and niche.
3. Open the generated public search link.
4. Copy official agency website URLs into the scan box (up to 25 at once).
5. The dashboard sends them to FastAPI in batches of 3.
6. Review source pages and email addresses, then export CSV or update lead status.

## Security and responsible use
- Only generic public business inboxes are extracted; named personal email addresses are excluded.
- No email sending or bulk outreach is implemented.
- Robots.txt is checked; non-public/private IP destinations and cross-domain redirects are blocked.
- Admin access requires a valid Supabase Auth session and an `ADMIN_EMAILS` allowlist match.
- Service-role credentials are server-only.
- Leads are scoped by `owner_id`; RLS is enabled.

## Free-tier notes
The code uses open-source libraries and no paid AI API. Supabase and Vercel free plans have quotas and policy limits that can change. Vercel Hobby is intended for personal/non-commercial use; do not use it for a commercial lead-generation service unless your plan terms permit that. Check current terms before monetized use. Scanning multiple websites is network-intensive and websites may rate-limit requests.


Deployment uses Vercel's FastAPI runtime with the `app:app` entrypoint.
