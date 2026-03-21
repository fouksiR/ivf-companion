# CLAUDE.md — Melod·AI Project Context

## Project Overview

Melod·AI is a longitudinal AI companion for emotional support and education during IVF/ART treatment. Built by Dr Yuval Fouks (Fertility Specialist, Virtus Health / Melbourne IVF). It combines real-time passive digital phenotyping, clinical construct detection, and AI-powered adaptive conversation.

## Live URLs

- Patient App: https://fouksir.github.io/ivf-companion/
- Clinician Dashboard: https://fouksir.github.io/ivf-companion/clinician-dashboard.html
- Backend API: https://ivf-companion-532857641879.australia-southeast1.run.app
- Fertool Knowledge Source: https://fertility-gp-backend-532857641879.australia-southeast2.run.app
- Firebase DB: https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app

## Technology Stack

- Backend: FastAPI (Python) on Google Cloud Run (australia-southeast1)
- AI: Claude API — Sonnet for responses, Haiku for triage
- Database: Firebase Realtime DB (write-through cache pattern)
- Frontend: Vanilla HTML/JS on GitHub Pages (push to main = auto-deploy)
- GCP Project: fertility-gp-portal

## Key Files

| File | Purpose | ~Lines |
|------|---------|--------|
| app.py | FastAPI backend — triage, chat routing, dynamic patient adaptation, Fertool bridge, nudge system, clinician endpoints, Firebase sync | 1700 |
| firebase_db.py | Firebase Realtime DB persistence — all CRUD with graceful fallback | 220 |
| signal_integration.py | Passive signal analysis — 7 construct detectors, baseline calibration, escalation scoring | 535 |
| index.html | Patient app — onboarding, chat, check-in sliders, egg companion, nudge cards, human widget, passive collector | 850 |
| clinician-dashboard.html | Clinician portal — patient cards, alert feed, pre-consult briefing panel, detail modals | 1070 |
| manifest.json | PWA manifest for mobile app icon | 20 |
| requirements.txt | Python deps: fastapi, uvicorn, anthropic, pydantic, httpx, firebase-admin | 6 |

## Architecture

### Chat Flow
Patient message → POST /chat → Haiku triage (1=emotional, 2=education, 3=screening, 4=crisis, 5=social) → Dynamic adaptation (ANALYTICAL/EMOTIONAL/MIXED) → Route to response generator → Signal context injected → Response with escalation_level

### Egg Check-in Flow
Tap egg → 5 dimensions (Mood, Anxiety, Loneliness, Uncertainty, Hope) with touch-hold → POST /checkin → Firebase persist → Clinician dashboard polls

### Passive Phenotyping Flow
Every 60s: POST /passive-signals → 7 construct detectors vs baseline → Escalation updated → If RED → clinician alert + human widget shown

### Clinician Dashboard Flow
Polls /clinician/patients + /clinician/alerts every 8s → Pre-consult briefing on patient click → Communication style badge + stress bar + concerns + approach

## Key Features Already Built

- Dynamic patient adaptation: classify_patient_style() detects ANALYTICAL/EMOTIONAL/MIXED from conversation history
- Pre-consultation clinician briefing via Haiku: GET /clinician/patient/{id}/briefing
- Interactive egg companion with 5-dimension check-ins and character transformations
- Human escalation widget: "Talk to someone" → POST /escalate/human
- Firebase persistence with write-through cache
- Stage-aware daily nudge system: GET /nudge/{patient_id} (29 treatment stages)
- PWA manifest with app icons

## Deploy Commands

```bash
# Full deploy (rebuild container)
gcloud run deploy ivf-companion --source . --region australia-southeast1 --allow-unauthenticated --memory 2Gi

# Update env vars only (no rebuild)
gcloud run services update ivf-companion --region australia-southeast1 --update-env-vars KEY=VALUE

# Push frontend (auto-deploys via GitHub Pages)
git add -A && git commit -m "description" && git push origin main

# Read backend logs
gcloud run services logs read ivf-companion --region australia-southeast1 --limit 30

# Set GCP project (if needed)
gcloud config set project fertility-gp-portal
```

## Known Issues & Priorities

### Critical (do first)
1. API key was exposed — needs rotation. Set via Cloud Run env var, never in code
2. Zero authentication on all endpoints — need API keys on /clinician/* at minimum
3. Some files may be in Cloud Shell but not in this repo — ensure git is in sync

### High Priority (before Virtus demo)
4. Fertool bridge untested with live key — clinical questions should route to Fertool backend
5. No SSE streaming — patient waits for full response, needs token-by-token streaming
6. Cold start 30+ seconds — set min-instances=1 on Cloud Run
7. EDUCATION_TOPICS dict exists but never surfaced to patient as suggestion chips

### Medium Priority
8. Passive signal route only extracts typing/content/circadian, needs all signal types
9. Old files to remove: dashboard.html, patch_*.py, old spec docs
10. No rate limiting
11. No service worker for PWA offline support

## Code Conventions

- Python backend (app.py): FastAPI with async endpoints
- Frontend: vanilla JS, no framework, inline in single HTML files
- All Firebase writes go through firebase_db.py (write-through cache pattern)
- Escalation levels: GREEN → YELLOW → ORANGE → RED
- Patient styles: ANALYTICAL, EMOTIONAL, MIXED
- Triage categories: 1=emotional, 2=education, 3=screening, 4=crisis, 5=social

## Important Notes

- NEVER commit API keys, Firebase service account JSON, or .env files
- The frontend is a single-page app — all patient UI is in index.html
- Firebase data lives under melod_ai/patients/ and melod_ai/conversations/
- Cloud Run is set to australia-southeast1 (Melbourne) — keep it there
- The Fertool backend is a SEPARATE service on australia-southeast2 — don't mix them up
- When editing index.html or clinician-dashboard.html, these deploy via git push (GitHub Pages)
- When editing app.py, firebase_db.py, or signal_integration.py, these deploy via gcloud run deploy
