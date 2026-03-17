# IVF Companion — Product Architecture & Specification

**A Longitudinal AI Companion for Emotional Support & Education During the IVF/ART Journey**

*Dr Yuval Fouks — March 2026*

---

## 1. Vision & Problem Statement

Between 25–60% of patients undergoing fertility treatment experience clinically significant anxiety or depression. The IVF journey is uniquely distressing: it's cyclical (repeated hope–disappointment loops), physically invasive, socially isolating, and temporally uncertain. Most clinics offer a single pre-treatment counselling session, leaving patients unsupported during the hardest parts — the two-week wait, failed transfers, decision points about continuing.

**IVF Companion** is a longitudinal AI agent that walks alongside the patient throughout their ART journey. It provides three things:

1. **Emotional companionship** — a warm, non-judgmental conversational partner that remembers the patient's story across weeks and months
2. **Fertility education** — personalised, plain-language explanations of what's happening at each treatment stage (LLM + RAG, adapted from the Fertool knowledge base)
3. **Clinical safety net** — validated psychometric screening at smart intervals, with automated escalation to a human clinician when thresholds are breached

### What Makes This Different from Existing Apps

| Existing Solution | Gap IVF Companion Fills |
|---|---|
| FertileMind | Mindfulness/hypnosis content library — no conversational AI, no longitudinal tracking, no escalation |
| Tilly / Rhea | Pivoted to broader reproductive health platform — not a persistent companion |
| Wysa / Woebot | General mental health chatbots — no fertility-specific knowledge, no treatment stage awareness |
| Clinic counsellors | Typically 1–2 sessions pre-IVF — no ongoing support between appointments |

---

## 2. User Model — Hybrid

### Patient Side
- Direct-to-patient conversational interface (mobile-optimised web app, PWA)
- Daily micro check-ins + free conversation
- Education content triggered by treatment stage
- Visual tracking of mood/wellbeing over time (charts, illustrations)

### Clinician Side
- Dashboard showing patient cohort with risk-stratified alerts
- Longitudinal mood/screening trends per patient
- Escalation queue with context summaries
- Ability to send messages/resources through the platform

---

## 3. The Journey Map — Treatment Stage Awareness

The system is **stage-aware**. Each patient is mapped to a treatment timeline:

```
INITIAL WORKUP → STIMULATION → EGG RETRIEVAL → FERTILISATION REPORT →
EMBRYO TRANSFER → TWO-WEEK WAIT → PREGNANCY TEST → [POSITIVE PATH / NEGATIVE PATH] →
[NEXT CYCLE or ONGOING PREGNANCY or DECISION TO STOP]
```

Stage transitions are either:
- **Patient-reported** ("I had my egg retrieval today")
- **Clinician-updated** (via dashboard)
- **AI-inferred** from conversation context

Each stage has:
- **Education modules** (what to expect physically/emotionally)
- **Tailored check-in prompts** (e.g., during TWW: focus on uncertainty/waiting distress)
- **Adjusted screening sensitivity** (e.g., lower alert thresholds post-failed transfer)

---

## 4. Psychometric Screening Framework

### Design Principle: Engagement First, Clinical Validity Always

Yuval's requirement is right — if the screening feels like a hospital questionnaire, patients won't engage. Our approach uses a **two-tier system**:

### Tier 1: Daily Micro Check-Ins (Custom, Engaging)

Short, visually appealing daily prompts — 30 seconds to complete. These are NOT validated instruments, but they are **sentinel signals** that trigger deeper screening.

**Five Dimensions** (mapped to IVF-specific distress):

| Dimension | Daily Prompt Style | Mapping |
|---|---|---|
| **Mood** | Sliding scale with illustrated faces (not emojis — warm, hand-drawn style) | Maps to PHQ-2 depression domain |
| **Anxiety/Fear** | "How much is worry taking up space today?" — visual thermometer | Maps to GAD-2 anxiety domain |
| **Loneliness** | "Did you feel connected to someone who understands today?" — yes/sometimes/no | Social isolation screening |
| **Uncertainty** | "How much control do you feel over what's happening?" — wave metaphor slider | Fertility-specific distress |
| **Hope** | "Where is your hope sitting today?" — illustrated scale from ember to flame | Protective factor / treatment dropout predictor |

**Presentation**: Illustrated, warm, non-clinical. Think cozy watercolour style — NOT medical forms. Each dimension uses a custom visual metaphor. Results shown back to patient as a gentle "garden" or "weather" visualisation — never raw scores.

### Tier 2: Validated Instruments (Periodic, Triggered)

Administered at smart intervals — not on a rigid schedule:

| Instrument | Items | When Administered | Purpose |
|---|---|---|---|
| **PHQ-2** | 2 items | Weekly (embedded naturally in check-in) | Depression screening gate |
| **PHQ-9** | 9 items | When PHQ-2 ≥ 3, or monthly, or at stage transitions | Full depression assessment |
| **GAD-7** | 7 items | When daily anxiety dimension is elevated ≥ 3 days, or monthly | Full anxiety assessment |
| **FertiQoL Core** (adapted subset) | 12 items (selected from 24) | At treatment milestones (start, post-retrieval, post-transfer, post-result) | Fertility-specific QoL tracking |
| **Single-item loneliness** | 1 item | Fortnightly | Validated single-item measure |

**Why this combination:**
- PHQ-2/PHQ-9 and GAD-7 are the most validated, widely accepted, and brief instruments. ACOG 2023 guidelines specifically recommend them.
- FertiQoL is the only internationally validated fertility-specific QoL measure (Cronbach's α 0.72–0.92). We use a curated subset of 12 items (from the emotional + mind-body + relational subscales) to keep it practical. Full 36-item version available on demand.
- Research shows infertility-specific instruments capture distress more sensitively than general tools alone — hence the hybrid approach.

**Presentation of Tier 2**: These are woven into the conversation. Instead of "Please complete the PHQ-9", the agent says: *"It's been a couple of weeks — can I ask you a few more questions today? I want to make sure I'm really hearing how you're doing."* Items are presented one at a time, conversationally.

---

## 5. Escalation & Safety Framework

### Threshold Matrix

| Signal | Source | Threshold | Action |
|---|---|---|---|
| PHQ-9 ≥ 10 | Validated screen | Moderate depression | **AMBER**: Flag clinician dashboard, agent offers resources |
| PHQ-9 ≥ 15 | Validated screen | Moderately severe | **RED**: Immediate clinician notification, agent gently encourages professional contact |
| PHQ-9 Item 9 ≥ 1 | Single item | Any suicidal ideation | **RED IMMEDIATE**: Clinician SMS/call alert, agent provides crisis resources, maintains supportive conversation |
| GAD-7 ≥ 10 | Validated screen | Moderate anxiety | **AMBER**: Flag + resources |
| GAD-7 ≥ 15 | Validated screen | Severe anxiety | **RED**: Clinician notification |
| Daily mood ≤ 2/10 for ≥ 3 consecutive days | Micro check-in | Persistent low mood | **AMBER**: Trigger PHQ-9, flag dashboard |
| Hope dimension at 0 for ≥ 2 days | Micro check-in | Treatment dropout risk | **AMBER**: Flag clinician, agent explores gently |
| Loneliness "no" for ≥ 5 of 7 days | Micro check-in | Social isolation | **AMBER**: Suggest peer support, flag dashboard |
| Conversational safety signals | LLM analysis | Mentions self-harm, hopelessness, "can't go on" | **RED**: Immediate escalation protocol |
| Disengagement | Behavioural | No check-in for ≥ 4 days after prior daily use | **AMBER**: Outreach prompt, clinician flag |

### Escalation Flow

```
SIGNAL DETECTED
    │
    ├── GREEN (below threshold)
    │     └── Continue normal support, log for trends
    │
    ├── AMBER (moderate concern)
    │     ├── Dashboard flag with context summary
    │     ├── Agent offers psychoeducation + coping resources
    │     ├── Trigger validated screening if not recently done
    │     └── Clinician reviews within 48 hours
    │
    └── RED (acute concern)
          ├── Real-time clinician notification (push + SMS)
          ├── Context summary auto-generated (recent check-ins, conversation excerpts, scores)
          ├── Agent maintains warm, supportive presence
          ├── If suicidal ideation: provides crisis line + encourages immediate help
          └── Clinician responds within 4 hours (SLA)
```

### Safety Rails for the AI Agent

- The agent NEVER provides therapy, diagnoses, or prescribes
- The agent NEVER dismisses distress ("it'll be fine", "just relax")
- The agent ALWAYS validates emotions before offering education
- The agent is trained to recognise when conversation exceeds its scope and seamlessly bridges to human support
- All conversations are logged and auditable
- Patient can request human contact at any time (one-tap)

---

## 6. Technical Architecture

### Modelled After Fertool — Adapted for Patient-Facing Use

```
┌─────────────────────────────────────────────────────┐
│                    FRONTEND (PWA)                     │
│  Mobile-optimised web app · GitHub Pages / Vercel    │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Chat UI  │  │ Check-in │  │ My Journey        │  │
│  │ (warm,   │  │ (visual  │  │ (mood trends,     │  │
│  │  illust.)│  │  scales) │  │  stage timeline,  │  │
│  │          │  │          │  │  education cards)  │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
└───────────────────────┬─────────────────────────────┘
                        │ HTTPS / SSE
                        ▼
┌─────────────────────────────────────────────────────┐
│              BACKEND (Python FastAPI)                 │
│              Google Cloud Run (AU)                    │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │           QUERY PROCESSING ENGINE            │     │
│  │                                               │     │
│  │  ┌─────────┐  ┌──────────┐  ┌────────────┐  │     │
│  │  │ Triage  │  │ Education│  │ Emotional  │  │     │
│  │  │ (Haiku) │  │ RAG (L1) │  │ Support    │  │     │
│  │  │         │  │ Fertool  │  │ Layer (L2) │  │     │
│  │  │         │  │ KB adapt │  │ Sonnet     │  │     │
│  │  └─────────┘  └──────────┘  └────────────┘  │     │
│  │                      │                        │     │
│  │              ┌───────▼────────┐               │     │
│  │              │  Synthesis (L3) │               │     │
│  │              │  Patient-facing │               │     │
│  │              │  warm language  │               │     │
│  │              └────────────────┘               │     │
│  └─────────────────────────────────────────────┘     │
│                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ Patient State │  │ Screening    │  │ Escalation│  │
│  │ Manager      │  │ Engine       │  │ Engine    │  │
│  │ (stage,      │  │ (PHQ/GAD/    │  │ (threshold│  │
│  │  history,    │  │  FertiQoL    │  │  matrix,  │  │
│  │  memory)     │  │  scoring)    │  │  alerts)  │  │
│  └──────────────┘  └──────────────┘  └───────────┘  │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │              DATA LAYER                       │    │
│  │  PostgreSQL (patient records, scores, chat)   │    │
│  │  FAISS (education knowledge base)             │    │
│  │  Redis (session state, rate limiting)         │    │
│  └──────────────────────────────────────────────┘    │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│            CLINICIAN DASHBOARD                        │
│  Separate authenticated web interface                 │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Patient  │  │ Alert    │  │ Longitudinal     │  │
│  │ Cohort   │  │ Queue    │  │ Trends           │  │
│  │ Overview │  │ (AMBER/  │  │ (per-patient     │  │
│  │          │  │  RED)    │  │  score graphs)   │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### Key Differences from Fertool

| Aspect | Fertool (GP-facing) | IVF Companion (Patient-facing) |
|---|---|---|
| **Audience** | Medical professionals | Patients (non-medical) |
| **Language** | Clinical, evidence-cited | Warm, plain language, illustrated |
| **Session model** | Stateless (single query) | Stateful (longitudinal, remembers history) |
| **Output** | Structured JSON with guidelines | Conversational + visual (charts, illustrations) |
| **Data persistence** | None (no patient data stored) | Full persistence (scores, chat, stage, preferences) |
| **Safety** | L3 gating (specialist contact) | Active escalation (automated clinician alerts) |
| **Knowledge base** | Fertility medicine guidelines | Adapted: patient-education versions of same content |

### Three-Layer Architecture (Adapted)

**Triage (Haiku)**: Classifies incoming message as:
1. Emotional support needed (→ prioritise L2 Emotional Support layer)
2. Education/information question (→ prioritise L1 Education RAG)
3. Screening response (→ route to Screening Engine)
4. Crisis/safety signal (→ immediate Escalation Engine)

**L1 — Education RAG**: Same FAISS + sentence-transformer architecture as Fertool, but the knowledge base is **rewritten for patients** (plain language, no jargon, reassuring tone). Retrieves relevant education content based on treatment stage + query.

**L2 — Emotional Support Layer**: Pure Claude Sonnet with a carefully designed system prompt. This is the "companion personality" — warm, remembers context, validates emotions, gently educates when appropriate. Has access to the patient's conversation history and screening scores.

**L3 — Synthesis**: Combines L1 + L2 into a single response. Ensures education is woven into emotional support naturally. Checks for safety signals. Generates visualisations where helpful (treatment timeline progress, mood trends, "what's happening in your body" illustrations).

### Patient State Manager

Persistent per-patient state:
```python
{
  "patient_id": "uuid",
  "treatment_stage": "two_week_wait",
  "stage_start_date": "2026-03-10",
  "cycle_number": 2,
  "partner_involved": true,
  "conversation_summary": "...",  # Rolling LLM-generated summary
  "recent_messages": [...],        # Last 20 messages for context window
  "screening_scores": {
    "phq2_latest": {"score": 3, "date": "2026-03-15"},
    "phq9_latest": {"score": 12, "date": "2026-03-15"},
    "gad7_latest": {"score": 8, "date": "2026-03-12"},
    "fertiqol_latest": {"score": 62, "date": "2026-03-01"},
    "daily_mood": [7, 6, 5, 4, 3, 3, 4],
    "daily_anxiety": [3, 4, 5, 6, 5, 5, 4],
    "daily_hope": [6, 5, 4, 3, 2, 2, 3]
  },
  "escalation_history": [...],
  "preferences": {
    "check_in_time": "20:00",
    "tone_preference": "gentle",  # gentle | direct | humorous
    "partner_name": "Alex",
    "clinic_name": "Melbourne IVF"
  }
}
```

---

## 7. Agent Personality & Conversation Design

### Core Personality Traits

The agent (working name: **"Mira"** — means "wonder" in multiple languages) has these traits:

- **Warm but not saccharine** — acknowledges pain without toxic positivity
- **Remembers everything** — references previous conversations naturally ("Last week you mentioned feeling anxious about the retrieval — how did it go?")
- **Honest about uncertainty** — "I don't know what will happen, and I know that's the hardest part"
- **Gently educational** — weaves in relevant knowledge when the patient is receptive
- **Knows its limits** — "I think this is something worth talking to your counsellor about — would you like me to flag it for your clinic?"

### Conversation Patterns

**Daily Check-In Flow:**
```
Mira: Hey [name] 💛 How are you sitting today?
      [Visual check-in: 5 dimension sliders appear]

Patient: [Completes sliders — mood: 4, anxiety: 7, loneliness: 2, uncertainty: 8, hope: 5]

Mira: Thank you for sharing that. I can see uncertainty is really
      high today — that makes so much sense on day 8 of the wait.

      Would you like to:
      • Talk about what's on your mind
      • Learn about what's actually happening right now (embryo development at this stage)
      • Just sit with it — I'm here either way
```

**Education Triggered by Stage:**
```
[Patient has just entered STIMULATION stage]

Mira: You're starting stims tomorrow — that's a big moment.
      A lot of women tell me the first injection is the scariest,
      and then it gets easier.

      [Illustrated card: "What stimulation medication actually does"
       — simple diagram of follicle growth, warmly illustrated]

      Want me to walk you through what to expect this week?
```

**Escalation Conversation:**
```
[PHQ-9 score = 14, flagged as AMBER → RED boundary]

Mira: I really appreciate you being honest with me about how
      you're feeling. What you're going through is genuinely hard,
      and it sounds like things have been particularly heavy lately.

      I want to make sure you have the right support around you.
      Would it be okay if I let your clinic's support team know
      you could use some extra care right now? They're really
      good at this.

      [Button: "Yes, please reach out to them"]
      [Button: "Not right now — but let's keep talking"]
```

---

## 8. Visualisation & Engagement Layer

Since the output must be **tailored to non-medical users with engaging visuals**, the system includes:

### Patient-Facing Visualisations

1. **Journey Timeline** — horizontal illustrated timeline showing treatment stages, where the patient is now, and what's ahead. Warm watercolour style.

2. **Mood Garden** — daily check-in data visualised as a growing garden. Good days = flowers blooming. Tough days = rain (not wilting — rain is necessary for growth). Over time, the patient sees their garden grow regardless of individual days.

3. **Weekly Trend Charts** — simple, beautiful line charts showing mood/anxiety/hope over time. Annotated with treatment events ("Egg retrieval", "Transfer day"). Helps patients see patterns and feel a sense of narrative.

4. **Education Cards** — illustrated explainers triggered by treatment stage. Topics like "What happens after embryo transfer", "Understanding your fertilisation report", "Why the two-week wait feels so long". Illustrated, not clinical.

5. **Breathing/Grounding Exercises** — animated visual guides for acute anxiety moments. Triggered by high anxiety scores or patient request.

### Clinician Dashboard Visualisations

1. **Cohort Risk Heatmap** — all patients, colour-coded by risk level
2. **Individual Patient Timeline** — screening scores over time, overlaid with treatment events
3. **Alert Queue** — prioritised list of patients needing attention, with AI-generated context summaries

---

## 9. Data & Privacy

- All data encrypted at rest (AES-256) and in transit (TLS 1.3)
- Patient data stored in Australian data centres (GCP australia-southeast1)
- Conversation logs retained for clinical safety; patient can request deletion
- Clinician access is role-based and audited
- No data used for model training
- Compliant with Australian Privacy Act + health records regulations
- Consent flow at onboarding: explicit opt-in for AI companion, data sharing with clinic, and escalation protocols

---

## 10. Regulatory Positioning

Following the Fertool playbook — this is positioned as a **wellness and education companion**, not a medical device or digital therapeutic:

- Does NOT diagnose or treat
- Does NOT replace clinical care
- DOES provide education and emotional support
- DOES use validated screening instruments (same ones used in paper form at clinics)
- Escalation always routes to a human clinician
- Positioned as enhancing existing clinic support, not replacing counsellors

This positions it outside TGA regulatory scope (similar to meditation apps, health trackers) while maintaining clinical rigour through the escalation framework.

---

## 11. Development Roadmap

### Phase 1 — MVP Prototype (Weeks 1–4)
- Backend: FastAPI + Claude API + basic patient state management
- Frontend: Mobile-optimised PWA with chat + daily check-in
- Knowledge base: Adapt 20 key Fertool education topics for patient language
- Screening: PHQ-2 weekly + daily 5-dimension micro check-in
- Escalation: Basic threshold → email alert to clinician
- No clinician dashboard yet (email-based alerts)

### Phase 2 — Clinical Pilot (Weeks 5–10)
- Full PHQ-9/GAD-7/FertiQoL integration
- Clinician dashboard (basic: alert queue + patient scores)
- Journey timeline + mood visualisation
- Education cards for all treatment stages
- Conversation memory (rolling summaries)
- Pilot with 20–30 patients at Melbourne IVF

### Phase 3 — Production (Weeks 11–16)
- Full clinician dashboard with cohort analytics
- Mood Garden visualisation
- Partner support features (optional partner account)
- Push notifications for check-in reminders
- Outcome tracking (pregnancy outcomes correlated with wellbeing data)
- Multi-clinic support

### Phase 4 — Scale & Research
- Research publication: longitudinal wellbeing data across IVF cycles
- Integration with clinic EMR systems
- Multilingual support
- Virtus Health network rollout

---

## 12. Strategic Value for Virtus Health

This tool directly addresses the **patient experience gap** that every IVF clinic knows exists:

- **Retention**: Patients who feel supported are less likely to drop out of treatment (stress is the #1 non-financial reason for dropout)
- **Differentiation**: No Australian IVF clinic currently offers an AI companion — first-mover advantage
- **Data**: Longitudinal psychometric data across thousands of cycles is unprecedented research material
- **Outcomes**: Evidence suggests reduced psychological distress correlates with improved IVF outcomes
- **Brand**: Positions Virtus/Melbourne IVF as caring about the whole patient, not just the biology

Combined with Fertool (GP-facing) + IVF Companion (patient-facing), Yuval has built a **full-funnel digital support system**: GPs are educated and empowered to refer appropriately, and patients are supported throughout their journey.

---

*Next step: Build the Phase 1 MVP prototype.*
