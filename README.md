# 🌼 Mira — IVF Companion

**A longitudinal AI companion for emotional support & education during the IVF/ART journey.**

Built by Dr Yuval Fouks — March 2026

---

## What Is This?

Mira is a hybrid patient-facing + clinician-dashboard tool that:

1. **Walks alongside patients** throughout their IVF journey with warm, personalised emotional support and education (powered by Claude LLM)
2. **Tracks wellbeing longitudinally** via daily micro check-ins (mood, anxiety, loneliness, uncertainty, hope) — all tagged to the patient's current treatment stage
3. **Screens for clinical concern** using validated instruments (PHQ-2/9, GAD-7, FertiQoL subset) administered conversationally
4. **Escalates to clinicians** when thresholds are breached — GREEN/AMBER/RED system with automated alerts
5. **Builds a prospective training dataset** — stage-coupled psychometric data for future predictive models and intervention design

---

## Project Structure

```
ivf-companion/
├── app.py                    # FastAPI backend (Python)
│                               - Chat endpoint (triage → education + emotional support → synthesis)
│                               - Daily check-in endpoint (5 dimensions, stage-tagged)
│                               - Screening engine (PHQ-2/9, GAD-7, FertiQoL scoring)
│                               - Escalation engine (threshold matrix, pattern detection)
│                               - Clinician dashboard API
│                               - Patient state management
│
├── patient-app.html          # Patient-facing frontend (mobile-first PWA)
│                               - 3-step onboarding (name → stage picker → cycle/treatment)
│                               - Chat with Mira (conversational AI companion)
│                               - Visual daily check-in (5 illustrated sliders)
│                               - Journey timeline + mood trend charts
│                               - Stage-specific education cards
│                               - 29 granular treatment stages
│
├── clinician-dashboard.html  # Clinician dashboard
│                               - Risk-stratified patient cohort table
│                               - Alert queue (RED/AMBER prioritised)
│                               - Per-patient drill-down (AI summary, scores, trends, escalation history)
│                               - Demo data showing realistic patient scenarios
│
├── requirements.txt          # Python dependencies
├── Dockerfile                # Cloud Run deployment
└── README.md                 # This file
```

> **Note:** `index.html` and `dashboard.html` are the earlier draft versions. Use `patient-app.html` and `clinician-dashboard.html` instead.

---

## 29 Treatment Stages

Every check-in is tagged with the patient's current stage — this is the core data structure for prospective training:

| # | Stage ID | Display Name | Emotional Focus |
|---|---|---|---|
| 1 | `consultation` | First Consultation | Overwhelm, hope, fear of the unknown |
| 2 | `investigation` | Investigations | Anxiety about results, identity questions |
| 3 | `waiting_to_start` | Waiting to Start | Limbo, impatience, anticipatory anxiety |
| 4 | `downregulation` | Down-Regulation | Side effects, emotional flatness |
| 5 | `stimulation` | Stimulation | Injection phobia, bloating, mood swings |
| 6 | `monitoring` | Monitoring Scans | Follicle count anxiety, comparison |
| 7 | `trigger` | Trigger Shot | Countdown anxiety, anticipation |
| 8 | `before_retrieval` | Day Before Retrieval | Procedural anxiety, fasting, preparation |
| 9 | `retrieval_day` | Retrieval Day | Fear, relief, physical discomfort |
| 10 | `post_retrieval` | Recovery | Physical pain, OHSS worry, waiting |
| 11 | `fertilisation_report` | Fertilisation Report | First drop-off grief, hope calibration |
| 12 | `embryo_development` | Embryo Updates | Attrition anxiety, daily hope/despair cycle |
| 13 | `freeze_all` | Freeze All | Disappointment at no fresh transfer |
| 14 | `before_transfer` | Before Transfer | Anticipation, lining anxiety, superstition |
| 15 | `transfer_day` | Transfer Day | Hope spike, vulnerability |
| 16 | `early_tww` | Early TWW (Days 1-5) | Symptom spotting begins, restlessness |
| 17 | `late_tww` | Late TWW (Days 6-12) | Peak anxiety, unbearable waiting |
| 18 | `result_day` | Result Day | Terror, dread, fragile hope |
| 19 | `positive_result` | Positive Result | Cautious joy, disbelief, fear of loss |
| 20 | `negative_result` | Negative Result | Grief, anger, emptiness |
| 21 | `chemical_pregnancy` | Chemical Pregnancy | Unique grief: hope given then taken |
| 22 | `miscarriage` | Miscarriage | Deep grief, physical + emotional recovery |
| 23 | `failed_cycle_acute` | Fresh After Failure | Raw, acute distress |
| 24 | `failed_cycle_processing` | Processing Failure | Making meaning, partner dynamics |
| 25 | `wtf_appointment` | Follow-up / WTF Appt | Information seeking, blame, planning |
| 26 | `between_cycles` | Between Cycles | Recovery, decision fatigue, financial stress |
| 27 | `considering_stopping` | Considering Stopping | Existential grief, identity, courage |
| 28 | `donor_journey` | Donor/Surrogacy Path | Complex emotions, letting go, new hope |
| 29 | `early_pregnancy` | Early Pregnancy | IVF-specific anxiety persists |

---

## Check-In Data Schema (for predictive modelling)

Each daily check-in produces a record structured for future ML training:

```json
{
  "timestamp": "2026-03-17T20:00:00Z",
  "patient_id": "abc123",
  "stage": "late_tww",
  "cycle_number": 2,
  "treatment_type": "icsi",
  "mood": 3,
  "anxiety": 8,
  "loneliness": 6,
  "uncertainty": 9,
  "hope": 2,
  "note": "I keep googling symptoms and I know I shouldn't...",
  "phq9_total": null,
  "gad7_total": null,
  "escalation_level": "AMBER",
  "escalation_triggers": ["mood_<=3_for_3_days", "hope_at_minimum"]
}
```

---

## Escalation Framework

| Level | Trigger | Action | SLA |
|---|---|---|---|
| 🟢 GREEN | Below all thresholds | Normal support, log for trends | — |
| 🟡 AMBER | PHQ-9 ≥ 10, GAD-7 ≥ 10, persistent low mood, high anxiety, disengagement | Dashboard flag, offer resources, trigger screening | Clinician review 48h |
| 🔴 RED | PHQ-9 ≥ 15, PHQ-9 Item 9 ≥ 1 (suicidal ideation), GAD-7 ≥ 15, crisis language | Immediate clinician alert (push + SMS), crisis resources shown | Clinician response 4h |

---

## Deployment (Cloud Run)

```bash
# Same pattern as Fertool
gcloud run deploy ivf-companion \
  --source . \
  --region australia-southeast1 \
  --allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=your_key \
  --memory 2Gi
```

Frontend can be deployed via GitHub Pages (separate repo or same repo `/docs` folder).

---

## Architecture

Same DNA as [Fertool](https://fouksiR.github.io/Fertool) — Python/FastAPI + Claude API + FAISS vectorstore on Google Cloud Run.

Key additions for IVF Companion:
- **Persistent patient state** (in-memory for MVP, PostgreSQL for production)
- **Screening engine** with validated instrument scoring
- **Escalation engine** with threshold matrix and pattern detection
- **Stage-aware conversation** — Mira adapts her support based on treatment phase
- **Clinician dashboard** with risk stratification and AI-generated patient summaries

---

## Regulatory Positioning

Positioned as a **wellness and education companion**, not a medical device:
- Does NOT diagnose or treat
- Does NOT replace clinical care
- Uses same validated screening instruments as paper forms at clinics
- Escalation always routes to a human clinician
- Positioned as enhancing existing clinic support

---

## License

Proprietary — Dr Yuval Fouks / Virtus Health

---

*Built with Claude · Anthropic*
