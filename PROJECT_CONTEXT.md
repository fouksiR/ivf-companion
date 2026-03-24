# MELOD-AI — Complete Project Context
**Updated: March 24, 2026**

> Drop this file into Claude Project files for instant context in new chats.

---

## What This Is

Melod-AI is an AI-powered IVF/fertility patient companion app. It provides emotional support, clinical education, passive behavioral monitoring, and bidirectional clinician communication for patients going through IVF, IUI, or egg freezing cycles.

Built by **Dr. Yuval Fouks** (fertility specialist, Virtus Health / Melbourne IVF, Australia).

---

## Live URLs

| Resource | URL |
|----------|-----|
| Patient App | https://ivf-companion-532857641879.australia-southeast1.run.app/ |
| Clinician Dashboard | https://ivf-companion-532857641879.australia-southeast1.run.app/clinician-dashboard.html |
| Backend API | https://ivf-companion-532857641879.australia-southeast1.run.app |
| Fertool Knowledge Source | https://fertility-gp-backend-532857641879.australia-southeast2.run.app |
| Fertool Frontend Tools | https://fouksir.github.io/Fertool/ |
| Repository | https://github.com/fouksiR/ivf-companion |
| GitHub Pages | DISABLED — re-enable in repo Settings → Pages → Deploy from main branch |

---

## Technology Stack

- **Backend:** FastAPI (Python) on Google Cloud Run (australia-southeast1)
- **AI:** Claude API — Sonnet for responses, Haiku for triage + briefings + reflections
- **Database:** Firebase Realtime DB (project: fertility-gp-portal)
- **DB URL:** `https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app`
- **Auth:** Firebase Auth (email/password, optional — patients can skip)
- **Auth API Key:** `AIzaSyDfqkNkezFiO7qcXELKwzzwoK3kLvqdOFw`
- **Frontend:** Vanilla HTML/JS (single files: index.html ~3800 lines, clinician-dashboard.html ~1100 lines)
- **GCP Project:** fertility-gp-portal
- **Cloud Run env vars:** ANTHROPIC_API_KEY, CLINICIAN_API_KEY, DEBUG_MODE, FIREBASE_DB_URL

---

## Key Files

| File | Purpose | ~Lines |
|------|---------|--------|
| app.py | FastAPI backend — all endpoints, triage, chat, phenotyping, clinician system | 4500+ |
| firebase_db.py | Firebase CRUD, phenotype snapshots, patient records | 315 |
| signal_integration.py | 7 construct detectors, behavioral pattern analysis, escalation scoring | 535 |
| index.html | Patient app — entire frontend in one file | 3800+ |
| clinician-dashboard.html | Clinician portal — role-based views, charts, actions | 1100+ |

---

## Deploy Commands

```bash
# Deploy everything (frontend + backend):
cd ~/Documents/ivf-companion
git pull origin main
gcloud run deploy ivf-companion --source . --region australia-southeast1 \
  --allow-unauthenticated --memory 2Gi \
  --update-env-vars "DEBUG_MODE=true,FIREBASE_DB_URL=https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app"

# Push code (then deploy):
git checkout main && git add -A && git commit -m "MSG" && git push origin main

# Read backend logs:
gcloud run services logs read ivf-companion --region australia-southeast1 --limit 30
```

---

## CRITICAL WORKFLOW RULE

Claude Code worktrees have caused repeated data loss. **EVERY Claude Code prompt MUST start with:**

```
WORKFLOW: Work directly on the main branch. Do NOT use worktrees.
Run: git checkout main
Make INCREMENTAL changes — do NOT rewrite entire files.
Before committing, verify these strings exist in index.html:
  "renderAnzardChart", "sendMsg", "passive-signals", "anzard_charts"
If any are missing, you broke something. Fix before committing.
Commit: git checkout main && git add -A && git commit -m "MSG" && git push origin main
```

---

## Architecture

### Chat Flow

```
Patient message → POST /chat
→ Haiku triage (1=emotional, 2=education, 3=screening, 4=crisis, 5=social)
→ Education intent detection (REASSURANCE/EXPLAIN/PRACTICAL)
→ Dynamic adaptation (ANALYTICAL/EMOTIONAL/MIXED)
→ ANZARD chart detection
→ Clinical trigger evaluation
→ Sonnet generates response
→ Fertool cards matched
→ Care team messages/flags injected
→ Conversation summary stored
→ Response returned with: escalation_level, fertool_cards, anzard_charts, support_widgets
```

### Passive Phenotyping Flow

```
Frontend collects every 60s → POST /passive-signals
→ Session metrics (duration, screens, messages, idle)
→ Weekly behavior log
→ 7 construct detectors (psychomotor retardation, agitation, sleep disturbance, anxiety escalation, hopelessness)
→ Behavioral pattern analyzer (WITHDRAWAL, INSOMNIA_PATTERN, AVOIDANCE, HYPER_ENGAGEMENT)
→ Community behavior analyzer (SEEKING_CONNECTION, LATE_NIGHT_COMMUNITY, ANTICIPATORY_BROWSING)
→ Z-scores vs baseline → Escalation (GREEN→YELLOW→AMBER→RED)
→ Phenotype snapshot saved to Firebase
→ If RED → clinician alert
```

### Clinical Trigger Engine

6 rules that fire when mood + calendar + stage align:
1. **Pre-procedure anxiety** (event in 48h + anxiety ≥5)
2. **Post-result vulnerability** (result in 72h + mood <4)
3. **Stimulation fatigue** (day 8+ + mood declining)
4. **TWW spiral** (anxiety ≥6 + hyper-engagement + late nights)
5. **Disengagement warning** (3 days silent after low mood)
6. **Medication confusion** (asked about meds 2+ times in 3 days)

Triggers generate: proactive AI support content + clinician flag + support widget cards below chat.

### Clinician System

- Role-based briefings: `GET /clinician/patient/{id}/briefing?role=doctor|nurse|secretary`
- Bidirectional: send-message, flag-topic, schedule-nudge, resolve-concern
- AI fallback: flags questions for clinician, follows up if no response
- Daily digest via Haiku
- Unresolved question tracker (auto-detects repeated questions)
- Involvement levels: HIGH/MODERATE/LOW per clinician

---

## Onboarding Flow (5 steps)

1. **Name** — first name + partner name (optional)
2. **Details** — age, treatment type, cycle number, clinic
3. **Journey type** — 4 cards: IVF/ICSI, IUI, Egg Freezing, Frequent Flyer
4. **Timeline** — horizontal scrollable timeline with stage nodes (tap to select current position)
5. **Emotional wave** — animated anxiety + uncertainty curves with "You are here" marker

### Journey Types & Stage Counts
- **IVF:** 7 stages (start → stimulation → retrieval → embryo → transfer → TWW → result)
- **IUI:** 6 stages (start → monitoring → trigger → insemination → TWW → result)
- **Egg Freezing:** 5 stages (start → stimulation → retrieval → freeze → done)
- **Frequent Flyer:** picks cycle number (2-5+) + journey type, sets flag that changes AI tone to peer-level

---

## What's Built & Working

### Patient App (index.html)

- ✅ Firebase Auth (email/password, optional, auto-login, cross-device)
- ✅ Landing page with oocyte mascot + Get Started / Log In
- ✅ 5-step onboarding with journey type + timeline + emotional wave
- ✅ Animated emotional propensity waves (anxiety + uncertainty, stage-dependent turbulence)
- ✅ Oocyte mascot with zona pellucida, 5 moods, drag, squish
- ✅ Oocyte personality system (speech bubbles, time-of-day, journey context, arms, random tap interactions, celebrations)
- ✅ Swimming sperm canvas (follows oocyte position, hides during check-in)
- ✅ AI chat via Claude Sonnet with 3-tier triage
- ✅ Dynamic patient adaptation (ANALYTICAL/EMOTIONAL/MIXED)
- ✅ 7 ANZARD 2023 infographic charts (inline SVG in chat)
- ✅ Fertool education link cards (endometriosis, assessment, etc.)
- ✅ 5-dimension check-in (Mood, Anxiety, Loneliness, Uncertainty, Hope)
- ✅ IVF cycle calendar (monthly grid, mood dots, event markers, 8 event types)
- ✅ Circle/Community tab — Firebase-backed anonymous posts, reactions (Support/Same/Strength), replies, stage filters, seed posts
- ✅ Insights tab: Summary (emotional wave + memory jar + calendar), Weekly, Daily, Chats sub-tabs
- ✅ Clinical trigger engine with support widget cards (pre-procedure, TWW survival kit, etc.)
- ✅ Egg proactive support (floating toast card on any screen)
- ✅ Passive phenotyping (typing speed, deletions, circadian, sentiment)
- ✅ Stage-aware nudge system (29 stages)
- ✅ Human escalation ("Talk to someone" + crisis numbers)
- ✅ Quick-reply suggestion pills + education topic chips
- ✅ PWA manifest
- ✅ Logout function (Firebase signOut + localStorage clear)

### Clinician Dashboard (clinician-dashboard.html)

- ✅ V3 teal/white design (Playfair Display + DM Sans)
- ✅ Patient list with colored escalation borders (RED/AMBER/GREEN)
- ✅ Filter pills (All / Red / Amber / Green / Flagged)
- ✅ Patient email shown under name (from Firebase Auth)
- ✅ Notification bell with unread badge
- ✅ Alert feed with 8-second polling
- ✅ Role selector (Doctor / Nurse / Secretary) — each gets different briefing depth
- ✅ Patient detail modal with 4 tabs: Briefing, Trends, Conversations, Actions
- ✅ 5 Chart.js phenotype trend charts (Mood & Hope, Anxiety & Uncertainty, Typing, Sleep, Escalation)
- ✅ Morning digest panel (gradient teal header)
- ✅ Settings modal (involvement level, response window, notification prefs)
- ✅ Action buttons: send message, flag topic, schedule nudge, resolve concern
- ✅ Responsive (desktop, tablet, mobile)

### Backend Endpoints (app.py)

**Patient:**
- `POST /chat` — AI conversation with full context
- `POST /checkin` — 5-dimension mood check-in
- `POST /passive-signals` — behavioral phenotyping data
- `POST /onboard` — patient registration (accepts Firebase Auth UID)
- `GET /patient/{id}/profile` — basic profile (graceful, no 404)
- `GET /patient/{id}` — full patient data
- `GET /nudge/{patient_id}` — stage-aware daily nudges
- `POST /escalate/human` — crisis escalation
- `POST /patient/{id}/cycle-events` — save calendar event
- `GET /patient/{id}/cycle-events` — list events + calendar updates

**Clinician (all require X-API-Key header):**
- `GET /clinician/patients` — all patients with email, escalation, mood
- `GET /clinician/alerts` — recent alerts
- `GET /clinician/patient/{id}/briefing?role=` — role-based briefing
- `GET /clinician/patient/{id}/phenotype-history` — trend data for charts
- `GET /clinician/patient/{id}/unresolved` — unresolved questions
- `GET /clinician/patient/{id}/conversations` — chat session history
- `GET /clinician/digest` — morning digest
- `POST /clinician/patient/{id}/send-message` — care team message
- `POST /clinician/patient/{id}/flag-topic` — AI topic flag
- `POST /clinician/patient/{id}/schedule-nudge` — scheduled check-in
- `POST /clinician/patient/{id}/resolve-concern` — mark resolved
- `POST /clinician/settings` — save clinician settings

**Community:**
- `POST /community/posts` — create anonymous post
- `GET /community/posts` — list (stage filter, pagination, auto-seeds)
- `POST /community/posts/{id}/react` — reaction (support/same/strength)
- `POST /community/posts/{id}/reply` — reply (max 3, max 200 chars)
- `POST /community/posts/{id}/report` — report (2 reports auto-hides)
- `GET /community/stages` — active stage counts
- `GET /community/active-count?stage=` — patients at same stage
- `GET /community/insights/stage/{stage}` — "patients like you" data (clinician auth)

**ANZARD:**
- `match_anzard_charts()` runs on every /chat — returns chart IDs
- 7 chart types: age_outcomes, cumulative, fresh_vs_frozen, causes, baby_outcomes, trends, egg_freezing_stats
- Exclusion logic: AMH/egg-freezing-specific questions skip ANZARD charts

---

## Known Issues & Gaps

### Needs Verification
- Oocyte typing reactions may not be fully wired (code exists)
- Oocyte celebration on "I'm pregnant" may not trigger
- Oocyte arms may not render visibly at small sizes
- Community Firebase sync needs end-to-end verification

### Not Built Yet
- SSE streaming for chat (patient waits for full response)
- Data export (CSV/Excel)
- Hebrew language support
- Dark mode / circadian theme
- Partner/support person companion view
- Clinic-wide analytics dashboard
- "Patients Like You" frontend (backend exists, shows "Coming Soon" card)
- Inline AMH normogram + egg freezing table (were built, may have been reverted)

---

## Design System (V3)

**Colors:**
- Background gradient: #d4f1f1 → #e0f5f0 → #eaf9f4
- Primary teal: #2D8E8E
- Teal light: #4db8b8
- Teal soft: #b8e6e6
- Gold: #D4A843
- Coral: #ef7b6e
- Purple: #7c5da1
- Text dark: #1a3a3a
- Text muted: #6a9e9e
- Cards: rgba(255,255,255,0.75) with border rgba(45,142,142,0.1)

**Fonts:**
- Headings: Playfair Display (Google Fonts)
- Body: DM Sans (Google Fonts)

**Components:**
- Cards: border-radius 16-20px, subtle shadow
- Buttons: pill-shaped (border-radius 50px)
- Inputs: pill-shaped with teal border on focus
- Tab bar: white glass with backdrop-blur

---

## Firebase Data Structure

```
melod_ai/
  patients/{patient_id}          — profile, stage, email, auth UID
  conversations/{patient_id}/    — chat messages
  checkins/{patient_id}/         — 5-dimension scores
  screenings/{patient_id}/       — PHQ-9, GAD-7 results
  escalations/{patient_id}/      — escalation events
  passive_signals/{patient_id}/  — phenotyping snapshots
  reflections/{patient_id}/      — AI-generated reflections
  clinical_triggers/{patient_id}/ — trigger events
  cycle_events/{patient_id}/     — calendar events
  community_posts/{post_id}      — anonymous posts
  community_replies/{post_id}/   — post replies
  community_seeded               — flag: seed posts created
  phenotype_history/{patient_id}/ — historical phenotype data
```

---

## Yuval's Preferences

- Codes in Python, uses R for stats
- Prefers dictating ideas by voice (interpret loosely)
- Wants prompts ready to paste into Claude Code
- Deploying from Mac Terminal with gcloud CLI
- Git auth via HTTPS
- NEVER use worktrees
- NEVER rewrite entire files — incremental edits only
- Always verify existing features survive before committing
