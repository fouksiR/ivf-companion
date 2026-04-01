# CLAUDE.md — Melod·AI IVF Companion

## Project Overview
Melod·AI is a longitudinal AI companion for emotional support and education during IVF/ART treatment. Built by Dr Yuval Fouks (Fertility Specialist, Virtus Health / Melbourne IVF). Combines passive digital phenotyping, clinical construct detection, and AI-powered adaptive conversation.

## Live URLs
- **Patient App (primary):** https://ivf-companion-532857641879.australia-southeast1.run.app/
- **Clinician Dashboard:** https://ivf-companion-532857641879.australia-southeast1.run.app/clinician-dashboard.html
- **Backend API:** Same Cloud Run URL (all endpoints)
- **GitHub Pages (secondary):** https://fouksir.github.io/ivf-companion/ (may lag behind Cloud Run)

## Technology Stack
- **Backend:** FastAPI (Python) on Google Cloud Run (australia-southeast1)
- **AI:** Claude API — Sonnet for responses, Haiku for triage/briefings
- **Database:** Firebase Realtime DB (write-through cache, graceful fallback to in-memory)
- **Frontend:** Vanilla HTML/JS — single-page app (index.html ~2600 lines)
- **GCP Project:** fertility-gp-portal

## Key Files
| File | Purpose | ~Lines |
|------|---------|--------|
| app.py | FastAPI backend — triage, chat, check-in, education, ANZARD charts, Fertool, clinician system, phenotyping, Firebase sync | 4500 |
| index.html | Patient app — landing, onboarding, chat, check-in, journey landscape, ANZARD/Fertool widgets, community, oocyte mascot | 2600 |
| firebase_db.py | Firebase persistence — patients, conversations, check-ins, reflections, phenotype snapshots | 315 |
| signal_integration.py | Passive signal analysis — 7 construct detectors, baseline calibration, escalation | 535 |
| clinician-dashboard.html | Clinician portal — role-based briefings, patient cards, alerts, actions tab | 1030 |

## Architecture

### Chat Flow
Patient message → POST /chat → Haiku triage (1=emotional, 2=education, 3=screening, 4=crisis, 5=social) → Dynamic style adaptation (ANALYTICAL/EMOTIONAL/MIXED) → Sonnet response → ANZARD chart matching → Fertool matching (only if no ANZARD) → ChatResponse JSON

### ChatResponse Fields
```
response: str              # AI text
patient_id, treatment_stage, query_id
escalation: {level, reason, signals}
anzard_charts: [{key, title, subtitle}]     # PRIORITY — 7 chart types
fertool_cards: [{key, title, description, url, icon, embed}]  # Only if no ANZARD
one_word_checkin: {mood, anxiety, loneliness, uncertainty, hope}
education_fork: str        # Clarifying question
capability_hint: str       # Feature discovery
```

### ANZARD 2023 Charts (rendered as native SVG/HTML in chat)
Triggered by keyword matching on ALL messages (not just education):
- `age_outcomes` — Bar chart: live birth rate by age group (fresh vs frozen)
- `cumulative` — Area chart: cumulative success over 6 cycles (39%→60%)
- `fresh_vs_frozen` — Side-by-side comparison cards
- `causes` — Horizontal bars: infertility causes breakdown
- `baby_outcomes` — 6-stat bubble grid (83% full-term, 20K babies, etc.)
- `trends` — Dual line chart: 2019-2023 improvement
- `egg_freezing_stats` — Hero number + breakdown by reason

**Priority rule:** ANZARD charts always take precedence over Fertool cards. Never show both.

### Fertool Widgets (native HTML in chat, triage=2 only)
- `amh` — Interactive AMH normogram SVG with age/value inputs + plot
- `egg_freezing` — Success rate table with age/eggs selectors + cell highlight
- `endometriosis` — Summary card with stage diagram + key facts
- `fertility_assessment` — Expandable checklist with details on tap
- `fertool_search` — Link card to Fertool KB (no embed)

### Check-in Flow
Oocyte tap → 5 dimensions (Mood, Anxiety, Loneliness, Uncertainty, Hope 0-10) → POST /checkin → Escalation check → Screening trigger (PHQ-9/GAD-7) → AI response → Firebase persist

### Passive Phenotyping Flow
Every 60s: JS collector captures typing speed, deletion ratio, scroll agitation, session timing → POST /passive-signals → 7 construct detectors → Phenotype snapshot to Firebase → Clinician dashboard

### Treatment Stages (29 total)
consultation, investigation, waiting_to_start, downregulation, stimulation, monitoring, trigger, before_retrieval, retrieval_day, post_retrieval, fertilisation_report, embryo_development, freeze_all, before_transfer, transfer_day, early_tww, late_tww, result_day, positive_result, negative_result, chemical_pregnancy, miscarriage, failed_cycle_acute, failed_cycle_processing, wtf_appointment, between_cycles, considering_stopping, donor_journey, early_pregnancy

## Frontend Structure (index.html)
- **#screen-welcome** — Landing page: oocyte mascot, clouds, "Create Account"/"Log in" buttons
- **#screen-onboard** — 3-step: name → age+details → stage selection (returning users skip via localStorage)
- **#screen-app** — 4-tab navigation:
  - **Chat** (panel-chat) — AI conversation + ANZARD/Fertool widgets
  - **Check-in** (panel-checkin) — 5-dimension sliders + oocyte mascot
  - **Circle** (panel-circle) — Community buddies/world tabs (localStorage-backed)
  - **Insights** (panel-journey) — Summary/Weekly/Daily/Chats sub-tabs, IVF calendar, journey landscape
- **Oocyte mascot** — Biological SVG (zona pellucida, ooplasm, face) with moods, stress-ball squish
- **Returning users** detected via localStorage `melodai_patient_id` → skip to chat with "Welcome back"

## Deploy Commands
```bash
# Deploy backend + frontend (Cloud Run serves both)
gcloud run deploy ivf-companion --source . --region australia-southeast1 --allow-unauthenticated --memory 2Gi

# Frontend-only change (also push to GitHub Pages)
git add -A && git commit -m "description" && git push origin main

# Read backend logs
gcloud run services logs read ivf-companion --region australia-southeast1 --limit 30
```

## Critical Rules
1. **Read the full file before editing** — index.html ~2600 lines, app.py ~4500 lines
2. **Never break existing API endpoints** — live patients using the backend
3. **ANZARD/Fertool charts are native SVG/HTML** — no iframes, no external libraries
4. **All element IDs referenced by JS must be preserved** when reskinning
5. **Firebase calls are fire-and-forget** — app works without Firebase (in-memory fallback)
6. **After app.py changes**, must redeploy Cloud Run
7. **Cloud Run serves index.html at `/`** — the frontend deploys WITH the backend
8. **Never commit API keys or service account JSON**
9. **Region is australia-southeast1** (Melbourne) — don't change

## Firebase Data Structure
```
melod_ai/
  patients/{patient_id}
  conversations/{patient_id}/{push_key}
  checkins/{patient_id}/{push_key}
  screenings/{patient_id}/{push_key}
  escalations/{patient_id}/{push_key}
  passive_signals/{patient_id}/{push_key}
  reflections/{patient_id}/{timestamp_key}
  conversation_summaries/{patient_id}/{timestamp_key}
  daily_insights/{patient_id}/{date_str}
  phenotype_history/{patient_id}/{timestamp_key}
```

## Clinician System
- **Auth:** X-API-Key header on all /clinician/* endpoints
- **Roles:** doctor, nurse, secretary — different briefing depth per role
- **Briefing:** GET /clinician/patient/{id}/briefing?role=doctor
- **Actions:** POST send-message, flag-topic, schedule-nudge, resolve-concern
- **Digest:** GET /clinician/digest — morning summary via Haiku
- **Dashboard polling:** Every 8 seconds for patients + alerts

---

## CRITICAL BUG FIX LOG (March 31, 2026)

### `save_patient` was wiping cycle data
`firebase_db.py` line 83 used `.set(data)` which REPLACES the entire patient node. Every time the patient app loaded (`/onboard`), it saved `{name, email, stage}` WITHOUT the `cycle` key — **deleting all cycle medications**.

**Fix:** Changed to `.update(data)` which MERGES keys, preserving siblings like `cycle/`.

**Rule:** NEVER use `.set()` on a parent node if child nodes (like `cycle/`, `conversations/`, `checkins/`) contain data you want to keep. Always use `.update()` for partial writes.

### Firebase multi-instance behavior
Cloud Run can have multiple instances. POST may hit instance A, GET may hit instance B. If Firebase init fails on one instance, that instance returns `{}` for everything while still returning `{"status":"updated"}` for writes (because writes go to the first-matched endpoint which may use a different code path).

**Rule:** Always verify writes by reading back from the SAME request context. The debug endpoint at `/debug/firebase-check/{patient_id}` is useful for this.

### Dashboard Save → Patient Calendar pipeline
The full working flow:
1. Dashboard: dropdown select med → type doses → click Save
2. `doSaveMeds` reads select/input values → POST to `/clinician/patient/{id}/cycle` → also saves to localStorage
3. Firebase stores under `melod_ai/patients/{id}/cycle/medications_simple/`
4. Patient app: `loadCycleToCalendar()` → GET `/patient/{id}/cycle-meds` → maps d1/d2/d3 to dates using `start_date` → stores in `window.cycleMedsByDate` → `buildCalendar()` renders 💊 icons
5. Tapping a calendar day shows medication names + doses

### Variables that MUST match
| Dashboard JS | Correct name | WRONG name (causes silent failure) |
|---|---|---|
| API base URL | `API_BASE` | `API_URL` |
| API key | `API_KEY` | `apiKey` |
| Calendar rebuild | `buildCalendar()` | `renderCalendar()` |
| Firebase ref | `_fb_ref.child()` | `firebase_db.reference()` |

### Med Grid — Procedures in Same Dropdown (March 31, 2026)

Procedures (Ultrasound scan, Blood test, OPU, ET, Hysteroscopy, D&C, etc.) are in the SAME dropdown as medications, under a "Procedures" optgroup. They use the same persistence pipeline — no separate code path.

**How procedures work in the grid:** Select "OPU (egg retrieval)" from dropdown, type "OPU" in the day cell where it happens. For medications, type the dose number. The patient calendar detects text values (non-numeric) and maps them to procedure types.

**Patient calendar rendering (mobile-friendly):**
- Medication days: light teal cell background + count number
- OPU: amber background + "OPU" label
- ET: green background + "ET" label  
- Scan: blue background + "Scan" label
- Pregnancy test: purple background + "Test" label
- Trigger: coral background + "Trig" label
- Uses colored cell backgrounds, NOT dots or SVG icons (invisible on mobile)

**Pattern:** The `cycleMedsByDate` object drives BOTH the cell background color AND the day detail panel. The `_calEvents` array is also populated for the event system. Text values (non-numeric dose cells) are treated as procedure names, numeric values as medication doses.

### MED_COLORS Object Structure
Procedure colors were added inside the same `MED_COLORS` object as medications. **Do NOT put a `};` between medication colors and procedure colors** — they're all entries in one object. The closing `};` comes after the last procedure entry, followed by `var MED_DEFAULT = ...`.

---

## Session Log — April 1, 2026

### Bugs Fixed

**1. `loadExistingPatient is not defined`**
Auth functions (`loadExistingPatient`, `submitAuth`) were trapped inside the Egg Companion IIFE (lines 2511-3325), invisible to the auth IIFE at line 4002. Fix: added `window.loadExistingPatient = loadExistingPatient` at line 3198 to expose it globally.

**2. `appendChild null` (typewriterWelcome)**
Welcome screen element doesn't exist when returning user auto-logs in. Fix: added `if(!el)return;` guard at top of `typewriterWelcome()`.

**3. Calendar date offset (off-by-one)**
`new Date('2026-04-01')` parses as UTC midnight. In Melbourne (UTC+10), `toISOString()` can shift dates backward. Fix: replaced UTC date math with local date construction:
```javascript
// OLD (broken in non-UTC timezones):
var d = new Date(start);
d.setDate(d.getDate() + dayNum);
var ds = d.toISOString().substring(0,10);

// NEW (works everywhere):
var d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + dayNum);
var ds = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
```
**Rule:** NEVER use `toISOString()` for display dates — always use local `getFullYear()/getMonth()/getDate()`.

**4. Chart.js canvas reuse error**
`trendChart` was re-initialized without destroying the old instance. Fix: added `if(trendChart){try{trendChart.destroy();}catch(e){}}` before `new Chart()`.

**5. Calendar data bleeding between patients**
`_calEvents` stored under shared localStorage key `melodai_cycle_events`. Fix: changed to per-patient key `cal_{patient_id}`. Old shared key is auto-deleted.

**6. Patient delete button on dashboard**
Added `×` button per patient card with double-confirm dialog. Endpoint `DELETE /clinician/patient/{id}` removes all data from 10 Firebase paths + Firebase Auth account.

### Known Issues (not yet fixed)
- `/daily-insight/{id}` returns 404 — endpoint not implemented, cosmetic console noise
- `/manifest.json` returns 404 — PWA manifest missing, cosmetic
- `loadExistingPatient` error still appears once on first load (race condition with Firebase Auth init) — harmless, app recovers

### Pattern: Timezone-Safe Date Handling
When converting between day numbers (D1, D2...) and calendar dates:
- Parse start_date with `split('-')` + `new Date(year, month-1, day)` — NOT `new Date(dateString)`
- Format with `getFullYear()/getMonth()/getDate()` — NOT `toISOString()`
- This avoids UTC offset issues in any timezone
