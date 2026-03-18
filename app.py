"""
IVF Companion — Phase 1 MVP Backend
A longitudinal AI companion for emotional support & education during IVF/ART.

Architecture mirrors Fertool:
  - Triage (Haiku) → classifies intent
  - L1: Education RAG (patient-language fertility knowledge)
  - L2: Emotional Support (warm companion personality)
  - L3: Synthesis (combines education + support, checks safety)
  - Screening Engine (PHQ-2/9, GAD-7, daily micro check-ins)
  - Escalation Engine (threshold matrix → clinician alerts)

Dr Yuval Fouks — March 2026
"""

from signal_integration import signal_router, get_signal_context_for_patient, patient_signal_store
import os
import json
import uuid
import hashlib
import logging
from datetime import datetime, timedelta, date
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
import anthropic
import asyncio


# Add the import line after the existing imports in app.py
sed -i '1s/^/from signal_integration import signal_router, get_signal_context_for_patient, patient_signal_store\n/' app.py

# Find where FastAPI app is created and add the router after it
sed -i '/^app = FastAPI/a app.include_router(signal_router)' app.py

# Verify it worked
head -5 app.py
grep "include_router" app.py

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ivf-companion")

# ── Constants ────────────────────────────────────────────────────────
SONNET_MODEL = "claude-sonnet-4-20250514"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ── Treatment Stages (29 granular stages for prospective training) ────
TREATMENT_STAGES = [
    "consultation", "investigation", "waiting_to_start", "downregulation",
    "stimulation", "monitoring", "trigger", "before_retrieval",
    "retrieval_day", "post_retrieval", "fertilisation_report", "embryo_development",
    "freeze_all", "before_transfer", "transfer_day", "early_tww", "late_tww",
    "result_day", "positive_result", "negative_result", "chemical_pregnancy",
    "miscarriage", "failed_cycle_acute", "failed_cycle_processing",
    "wtf_appointment", "between_cycles", "considering_stopping",
    "donor_journey", "early_pregnancy",
]

STAGE_DISPLAY = {
    "consultation": "First Consultation",
    "investigation": "Investigations",
    "waiting_to_start": "Waiting to Start",
    "downregulation": "Down-Regulation",
    "stimulation": "Stimulation",
    "monitoring": "Monitoring Scans",
    "trigger": "Trigger Shot",
    "before_retrieval": "Day Before Retrieval",
    "retrieval_day": "Retrieval Day",
    "post_retrieval": "Recovery",
    "fertilisation_report": "Fertilisation Report",
    "embryo_development": "Embryo Updates",
    "freeze_all": "Freeze All",
    "before_transfer": "Before Transfer",
    "transfer_day": "Transfer Day",
    "early_tww": "Early TWW (Days 1-5)",
    "late_tww": "Late TWW (Days 6-12)",
    "result_day": "Result Day",
    "positive_result": "Positive Result",
    "negative_result": "Processing Result",
    "chemical_pregnancy": "Chemical Pregnancy",
    "miscarriage": "Miscarriage",
    "failed_cycle_acute": "Fresh After Failure",
    "failed_cycle_processing": "Processing Failure",
    "wtf_appointment": "Follow-up / WTF Appt",
    "between_cycles": "Between Cycles",
    "considering_stopping": "Considering Stopping",
    "donor_journey": "Donor/Surrogacy Path",
    "early_pregnancy": "Early Pregnancy",
}

# ── Psychometric Instruments ─────────────────────────────────────────

PHQ2_QUESTIONS = [
    "Over the last 2 weeks, how often have you been bothered by having little interest or pleasure in doing things?",
    "Over the last 2 weeks, how often have you been bothered by feeling down, depressed, or hopeless?",
]

PHQ9_QUESTIONS = [
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself — or that you are a failure or have let yourself or your family down",
    "Trouble concentrating on things, such as reading or watching TV",
    "Moving or speaking so slowly that other people could have noticed? Or the opposite — being so fidgety or restless",
    "Thoughts that you would be better off dead, or of hurting yourself",
]

GAD7_QUESTIONS = [
    "Feeling nervous, anxious, or on edge",
    "Not being able to stop or control worrying",
    "Worrying too much about different things",
    "Trouble relaxing",
    "Being so restless that it's hard to sit still",
    "Becoming easily annoyed or irritable",
    "Feeling afraid, as if something awful might happen",
]

FERTIQOL_SUBSET = [
    "Do feelings of jealousy or resentment about others' fertility affect your daily life?",
    "Do you feel able to cope with your fertility problems?",
    "Do you feel physically drained because of fertility problems?",
    "Do you feel pain or discomfort because of your fertility treatment?",
    "Do you find it difficult to plan activities because of fertility problems?",
    "Is your fertility journey affecting your relationship with your partner?",
    "Do you find it hard to talk to your partner about your feelings related to fertility?",
    "Do you feel social pressure because of fertility problems?",
    "Do you feel isolated or left out because of fertility problems?",
    "Are you satisfied with the support you receive from friends and family?",
    "Are you satisfied with the information you receive about your treatment?",
    "Are you satisfied with the emotional support from your clinic?",
]

# ── Conversational Wrappers for Instruments ──────────────────────────

PHQ9_CONVERSATIONAL = [
    "Have you been finding it hard to enjoy things you usually like — even small things?",
    "How often have you been feeling really low or hopeless lately?",
    "How has your sleep been? Trouble falling asleep, waking up a lot, or sleeping way too much?",
    "Have you been feeling exhausted — like even small things take so much energy?",
    "Has your appetite changed much? Eating much less or more than usual?",
    "Have you been feeling down on yourself — like you're failing or letting people down?",
    "Has it been hard to concentrate — reading, watching something, even conversations?",
    "Have others noticed you seem slower than usual, or the opposite — really restless and fidgety?",
    "I want to ask something important, and please know there's no wrong answer — have you had any thoughts that life isn't worth living, or of hurting yourself?",
]

# ── Prompts ───────────────────────────────────────────────────────────

TRIAGE_PROMPT = """You are a triage classifier for an IVF patient support companion.
Classify the patient's message into ONE category. Reply with ONLY the number.

1 = EMOTIONAL — patient is expressing feelings, seeking comfort, venting, or needs emotional support
2 = EDUCATION — patient is asking a factual question about IVF, fertility treatment, medications, procedures, or their body
3 = SCREENING — patient is responding to a check-in or questionnaire prompt (e.g., rating mood, answering PHQ/GAD items)
4 = CRISIS — patient expresses suicidal ideation, self-harm, acute distress, or hopelessness suggesting danger
5 = SOCIAL — casual chat, greetings, logistics, or off-topic conversation

Reply ONLY with the number (1-5)."""

COMPANION_SYSTEM = """You are Melod-AI, a warm and knowledgeable AI companion supporting a patient through their IVF/ART journey.

CORE IDENTITY:
- You are a knowledgeable friend, NOT a therapist, NOT a doctor
- You ANSWER QUESTIONS directly with accurate fertility information in plain language
- You validate emotions when they come up, but you do not treat every message as emotional
- You remember their story and reference it naturally

RESPONSE RULES:
1. If the patient asks a QUESTION about treatment, medications, procedures or their body, ANSWER IT with clear accurate information. Use plain language and helpful analogies. Then offer emotional support if relevant.
2. If the patient is VENTING or expressing feelings, validate first, then gently offer support.
3. If the patient wants PRACTICAL HELP, give them concrete useful information.
4. NEVER give the same generic response to different questions.
5. Keep responses 2-4 paragraphs. Be warm but substantive.

WHAT YOU KNOW:
- IVF/ICSI procedures: stimulation protocols, egg retrieval, embryo culture, transfer, FET
- Medications: Gonal-F, Menopur, Cetrotide, Orgalutran, progesterone, trigger shots
- Conditions: endometriosis, PCOS, diminished ovarian reserve, male factor, unexplained
- Lab: AMH, FSH, AFC, embryo grading, blastocyst development, PGT-A
- Australian context: Medicare, PBS, clinic processes, referral pathways

WHAT YOU NEVER DO:
- Give specific medical advice (you educate, you do not prescribe)
- Promise outcomes
- Dismiss or minimise emotions
- Give the same response regardless of what was asked

EDUCATION APPROACH:
- Use plain language, not textbook terminology
- Use analogies: follicles as small fluid-filled pods, embryo transfer as a tiny passenger
- Always end educational answers with Your specialist can give you specifics for your situation

STAGE AWARENESS:
You know what treatment stage the patient is in and tailor accordingly.

{patient_context}
{education_context}
"""

ESCALATION_CHECK_PROMPT = """You are a safety classifier for an IVF patient support system.
Review the patient's message and the conversation context.

Classify the safety level. Reply with a JSON object ONLY:
{{
  "level": "GREEN" | "AMBER" | "RED",
  "reason": "brief explanation",
  "signals": ["list", "of", "specific", "signals", "detected"]
}}

GREEN = Normal conversation, no safety concerns
AMBER = Elevated distress, persistent low mood, significant anxiety, social isolation, treatment dropout language
RED = Suicidal ideation, self-harm references, acute crisis, expressions of hopelessness suggesting danger

Be sensitive but not over-reactive. IVF patients naturally express sadness, frustration, and fear — these are NORMAL and should be GREEN.
AMBER is for patterns that suggest the patient needs extra clinical support.
RED is ONLY for genuine safety concerns.

Patient message: {message}
Recent context: {context}"""

EDUCATION_TOPICS = {
    "initial_workup": [
        "What blood tests measure and why each one matters for your fertility picture",
        "Understanding AMH — what the numbers mean in plain language",
        "What an ultrasound scan is looking for and what follicle counts tell us",
        "How your GP and fertility specialist work together",
    ],
    "stimulation": [
        "What the stimulation medications actually do in your body",
        "Day-by-day: what's happening with your follicles during stims",
        "Managing injection anxiety — practical tips from other patients",
        "Why monitoring appointments matter and what they're checking",
        "Side effects: what's normal and when to call your clinic",
    ],
    "egg_retrieval": [
        "What happens during egg retrieval — a step-by-step walkthrough",
        "Recovery: what to expect in the hours and days after",
        "Why egg numbers vary and what a 'good' number really means",
        "The emotional rollercoaster of retrieval day",
    ],
    "fertilisation_report": [
        "Understanding your fertilisation report — the day-by-day updates",
        "What embryo grading means (and doesn't mean)",
        "Why some eggs don't fertilise — it's more common than you think",
        "The attrition curve: why numbers drop and what that means",
    ],
    "embryo_transfer": [
        "What happens during embryo transfer — simpler than you might think",
        "Fresh vs frozen transfer: why your doctor chose this path",
        "The science of implantation — what your body is doing right now",
        "What you can and can't control after transfer",
    ],
    "two_week_wait": [
        "What's actually happening in your body during the two-week wait",
        "Symptom spotting: why it's unreliable (but completely understandable)",
        "Managing the wait: evidence-based strategies that help",
        "The truth about bed rest, pineapple, and other myths",
    ],
    "pregnancy_test": [
        "Understanding your beta HCG result",
        "What happens next after a positive result",
        "Processing a negative result — there is no right way to feel",
    ],
    "negative_path": [
        "Grief after a failed cycle is real and valid",
        "When to think about next steps — and when to just be",
        "What your clinic might change for the next cycle",
        "The strength it takes to keep going (or to stop)",
    ],
    "positive_path": [
        "Early pregnancy after IVF — why the worry doesn't just stop",
        "What monitoring looks like in the first trimester",
        "Transitioning from fertility clinic to obstetric care",
    ],
    "between_cycles": [
        "Giving your body and mind time to recover",
        "Questions to ask your specialist before another cycle",
        "The emotional weight of deciding whether to continue",
    ],
    "decision_to_stop": [
        "There is no failure in choosing to stop",
        "Processing the end of treatment — grief, relief, and everything between",
        "Finding support for life after IVF",
    ],
}


# ── In-Memory Patient Store (Phase 1 — will move to PostgreSQL) ──────

patients_db: dict = {}
conversations_db: dict = {}  # patient_id -> list of messages
checkins_db: dict = {}       # patient_id -> list of daily check-ins
screenings_db: dict = {}     # patient_id -> list of screening results
escalations_db: dict = {}    # patient_id -> list of escalation events
passive_signals_db: dict = {}  # patient_id -> list of passive behavioural signals


def get_or_create_patient(patient_id: str) -> dict:
    if patient_id not in patients_db:
        patients_db[patient_id] = {
            "patient_id": patient_id,
            "name": None,
            "treatment_stage": "initial_workup",
            "cycle_number": 1,
            "stage_start_date": datetime.now().isoformat(),
            "partner_name": None,
            "clinic_name": None,
            "preferences": {
                "check_in_time": "20:00",
                "tone": "gentle",
            },
            "created_at": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
        }
        conversations_db[patient_id] = []
        checkins_db[patient_id] = []
        screenings_db[patient_id] = []
        escalations_db[patient_id] = []
        passive_signals_db[patient_id] = []
    patients_db[patient_id]["last_active"] = datetime.now().isoformat()
    return patients_db[patient_id]


def get_conversation_context(patient_id: str, last_n: int = 20) -> list:
    """Get recent conversation for context window."""
    return conversations_db.get(patient_id, [])[-last_n:]


def get_recent_checkins(patient_id: str, last_n: int = 7) -> list:
    """Get recent daily check-ins."""
    return checkins_db.get(patient_id, [])[-last_n:]


def build_patient_context(patient_id: str) -> str:
    """Build the patient context string for the system prompt."""
    patient = get_or_create_patient(patient_id)
    checkins = get_recent_checkins(patient_id)
    screenings = screenings_db.get(patient_id, [])[-3:]

    ctx = f"\nPATIENT CONTEXT:\n"
    if patient["name"]:
        ctx += f"- Name: {patient['name']}\n"
    ctx += f"- Treatment stage: {STAGE_DISPLAY.get(patient['treatment_stage'], patient['treatment_stage'])}\n"
    ctx += f"- Cycle number: {patient['cycle_number']}\n"
    if patient["partner_name"]:
        ctx += f"- Partner: {patient['partner_name']}\n"
    if patient["clinic_name"]:
        ctx += f"- Clinic: {patient['clinic_name']}\n"
    ctx += f"- Preferred tone: {patient['preferences'].get('tone', 'gentle')}\n"

    if checkins:
        ctx += f"\nRECENT DAILY CHECK-INS (last {len(checkins)} days):\n"
        for ci in checkins:
            ctx += f"  {ci['date']}: mood={ci['mood']}/10, anxiety={ci['anxiety']}/10, "
            ctx += f"loneliness={ci['loneliness']}/10, uncertainty={ci['uncertainty']}/10, "
            ctx += f"hope={ci['hope']}/10\n"

        # Trend analysis
        if len(checkins) >= 3:
            moods = [c["mood"] for c in checkins[-3:]]
            avg_mood = sum(moods) / len(moods)
            if avg_mood <= 3:
                ctx += "  ⚠ PATTERN: Persistently low mood over recent days\n"
            hopes = [c["hope"] for c in checkins[-3:]]
            if all(h <= 2 for h in hopes):
                ctx += "  ⚠ PATTERN: Hope has been very low — be especially gentle\n"
            anxieties = [c["anxiety"] for c in checkins[-3:]]
            if all(a >= 7 for a in anxieties):
                ctx += "  ⚠ PATTERN: Sustained high anxiety — offer grounding\n"

    if screenings:
        ctx += f"\nRECENT SCREENING SCORES:\n"
        for s in screenings:
            ctx += f"  {s['date']} — {s['instrument']}: {s['total_score']} ({s['severity']})\n"

    return ctx


def build_education_context(patient_id: str) -> str:
    """Get relevant education topics for current stage."""
    patient = get_or_create_patient(patient_id)
    stage = patient["treatment_stage"]
    topics = EDUCATION_TOPICS.get(stage, [])
    if not topics:
        return ""
    ctx = f"\nAVAILABLE EDUCATION TOPICS FOR THIS STAGE ({STAGE_DISPLAY.get(stage, stage)}):\n"
    for t in topics:
        ctx += f"  - {t}\n"
    ctx += "You can weave these into conversation naturally when relevant. Don't force them.\n"
    return ctx


# ── Scoring Functions ─────────────────────────────────────────────────

def score_phq(responses: list[int]) -> dict:
    """Score PHQ-2 or PHQ-9. Each item 0-3."""
    total = sum(responses)
    n = len(responses)

    if n == 2:  # PHQ-2
        return {
            "instrument": "PHQ-2",
            "total_score": total,
            "severity": "screen_positive" if total >= 3 else "screen_negative",
            "needs_phq9": total >= 3,
        }
    elif n == 9:  # PHQ-9
        item9 = responses[8]  # Suicidal ideation item
        if total <= 4:
            severity = "minimal"
        elif total <= 9:
            severity = "mild"
        elif total <= 14:
            severity = "moderate"
        elif total <= 19:
            severity = "moderately_severe"
        else:
            severity = "severe"

        return {
            "instrument": "PHQ-9",
            "total_score": total,
            "severity": severity,
            "item9_score": item9,
            "suicidal_ideation": item9 >= 1,
            "escalation_level": (
                "RED" if item9 >= 1 else
                "RED" if total >= 15 else
                "AMBER" if total >= 10 else
                "GREEN"
            ),
        }
    return {"error": "Invalid number of responses"}


def score_gad7(responses: list[int]) -> dict:
    """Score GAD-7. Each item 0-3."""
    total = sum(responses)
    if total <= 4:
        severity = "minimal"
    elif total <= 9:
        severity = "mild"
    elif total <= 14:
        severity = "moderate"
    else:
        severity = "severe"

    return {
        "instrument": "GAD-7",
        "total_score": total,
        "severity": severity,
        "escalation_level": (
            "RED" if total >= 15 else
            "AMBER" if total >= 10 else
            "GREEN"
        ),
    }


def check_daily_escalation(patient_id: str) -> dict:
    """Check daily check-in patterns for escalation triggers."""
    checkins = get_recent_checkins(patient_id)
    if len(checkins) < 3:
        return {"level": "GREEN", "triggers": []}

    triggers = []
    recent = checkins[-3:]

    # Persistent low mood
    if all(c["mood"] <= 2 for c in recent):
        triggers.append("Mood ≤ 2/10 for 3+ consecutive days")

    # Hope at zero
    if len(checkins) >= 2 and all(c["hope"] <= 1 for c in checkins[-2:]):
        triggers.append("Hope at minimum for 2+ days — treatment dropout risk")

    # Persistent high anxiety
    if all(c["anxiety"] >= 8 for c in recent):
        triggers.append("Anxiety ≥ 8/10 for 3+ consecutive days")

    # Persistent loneliness
    week = checkins[-7:] if len(checkins) >= 7 else checkins
    lonely_days = sum(1 for c in week if c["loneliness"] >= 7)
    if lonely_days >= 5:
        triggers.append(f"High loneliness {lonely_days}/7 recent days")

    # Disengagement check
    if len(checkins) >= 2:
        last_date = datetime.fromisoformat(checkins[-1]["date"]).date()
        prev_date = datetime.fromisoformat(checkins[-2]["date"]).date()
        gap = (last_date - prev_date).days
        if gap >= 4:
            triggers.append(f"Disengagement: {gap}-day gap in check-ins")

    level = "AMBER" if triggers else "GREEN"
    return {"level": level, "triggers": triggers}


# ── Request/Response Models ───────────────────────────────────────────

class ChatRequest(BaseModel):
    patient_id: str
    message: str

class ChatResponse(BaseModel):
    response: str
    patient_id: str
    treatment_stage: str
    escalation: Optional[dict] = None
    suggested_education: Optional[list] = None
    query_id: str = ""

class CheckInRequest(BaseModel):
    patient_id: str
    mood: int = Field(ge=0, le=10)
    anxiety: int = Field(ge=0, le=10)
    loneliness: int = Field(ge=0, le=10)
    uncertainty: int = Field(ge=0, le=10)
    hope: int = Field(ge=0, le=10)
    note: Optional[str] = None

class CheckInResponse(BaseModel):
    message: str
    patient_id: str
    checkin_summary: dict
    escalation: Optional[dict] = None
    trigger_screening: Optional[str] = None

class ScreeningRequest(BaseModel):
    patient_id: str
    instrument: str  # "PHQ-2", "PHQ-9", "GAD-7", "FertiQoL"
    responses: list[int]

class ScreeningResponse(BaseModel):
    result: dict
    message: str
    escalation: Optional[dict] = None

class PassiveSignalBatch(BaseModel):
    """Passive behavioural signals collected silently from the patient app."""
    patient_id: str
    signals: list[dict]  # Each: {signal_type, value, timestamp, metadata}

class PatientUpdateRequest(BaseModel):
    patient_id: str
    name: Optional[str] = None
    treatment_stage: Optional[str] = None
    cycle_number: Optional[int] = None
    partner_name: Optional[str] = None
    clinic_name: Optional[str] = None
    tone_preference: Optional[str] = None

class OnboardRequest(BaseModel):
    name: str
    treatment_stage: str = "consultation"
    cycle_number: int = 1
    treatment_type: str = "ivf"  # ivf, icsi, fet, iui, egg_freezing, other
    partner_name: Optional[str] = None
    clinic_name: Optional[str] = None


# ── App Setup ─────────────────────────────────────────────────────────

# ── Vectorstore (Education RAG) ───────────────────────────────────────

education_vectorstore = None

def load_vectorstore():
    """Load FAISS education vectorstore if available."""
    global education_vectorstore
    vs_path = os.environ.get("VECTORSTORE_PATH", "./education_vectorstore")
    if os.path.exists(vs_path):
        try:
            from langchain_community.vectorstores import FAISS
            from langchain_community.embeddings import HuggingFaceEmbeddings
            embeddings = HuggingFaceEmbeddings(
                model_name="sentence-transformers/all-MiniLM-L6-v2",
                model_kwargs={"device": "cpu"},
            )
            education_vectorstore = FAISS.load_local(
                vs_path, embeddings, allow_dangerous_deserialization=True
            )
            logger.info(f"Education vectorstore loaded from {vs_path}")
        except Exception as e:
            logger.warning(f"Could not load vectorstore: {e}. Education RAG disabled.")
    else:
        logger.info(f"No vectorstore at {vs_path}. Education RAG will use LLM knowledge only.")


def retrieve_education(query: str, stage: str, k: int = 4) -> str:
    """Retrieve relevant education content for the patient's query + stage."""
    if education_vectorstore is None:
        return ""

    try:
        # Search with query + stage context for better relevance
        search_query = f"{query} [stage: {stage}]"
        results = education_vectorstore.similarity_search_with_score(search_query, k=k)

        context_parts = []
        for doc, score in results:
            relevance = 1 / (1 + score)
            if relevance > 0.3:  # Only include reasonably relevant results
                context_parts.append(
                    f"[EDUCATION — {doc.metadata.get('title', 'Unknown')}]\n{doc.page_content}"
                )

        if context_parts:
            return "\nRELEVANT EDUCATION CONTENT (use naturally in conversation):\n" + \
                   "\n\n".join(context_parts) + \
                   "\n\nWeave this information naturally into your response. Don't dump it — " \
                   "share what's relevant to what the patient is actually asking or feeling.\n"
        return ""
    except Exception as e:
        logger.warning(f"Education retrieval error: {e}")
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("IVF Companion backend starting...")
    # Load vectorstore in background — don't block startup
    try:
        load_vectorstore()
    except Exception as e:
        logger.warning(f"Vectorstore load failed (non-fatal): {e}")
    yield
    logger.info("IVF Companion backend shutting down.")

app = FastAPI(
    title="IVF Companion API",
    version="0.1.0",
    description="Longitudinal AI companion for emotional support & education during IVF/ART",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(signal_router)
client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/")
async def health():
    return {
        "service": "Melod-AI",
        "version": "0.1.0",
        "status": "running",
        "patients_active": len(patients_db),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/onboard")
async def onboard_patient(req: OnboardRequest):
    """Onboard a new patient and get a welcome message."""
    patient_id = str(uuid.uuid4())[:8]
    patient = get_or_create_patient(patient_id)
    patient["name"] = req.name
    patient["treatment_stage"] = req.treatment_stage
    patient["cycle_number"] = req.cycle_number
    patient["partner_name"] = req.partner_name
    patient["clinic_name"] = req.clinic_name

    # Generate welcome message
    stage_name = STAGE_DISPLAY.get(req.treatment_stage, req.treatment_stage)
    welcome_prompt = f"""Generate a warm welcome message for {req.name} who is just starting to use IVF Companion.
They are currently at the '{stage_name}' stage of their IVF journey (cycle {req.cycle_number}).
{"Their partner's name is " + req.partner_name + ". " if req.partner_name else ""}
{"They're being treated at " + req.clinic_name + ". " if req.clinic_name else ""}

Introduce yourself as Melod-AI. Be warm, brief (3-4 sentences), and let them know you're here for them throughout this journey.
Mention what you can help with (emotional support, education about what's happening, daily check-ins) without overwhelming them.
End with one gentle question to start the conversation."""

    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=500,
        system=COMPANION_SYSTEM.format(
            patient_context=build_patient_context(patient_id),
            education_context=build_education_context(patient_id),
        ),
        messages=[{"role": "user", "content": welcome_prompt}],
    )

    welcome_msg = response.content[0].text

    # Store in conversation history
    conversations_db[patient_id].append({
        "role": "assistant",
        "content": welcome_msg,
        "timestamp": datetime.now().isoformat(),
        "type": "welcome",
    })

    return {
        "patient_id": patient_id,
        "message": welcome_msg,
        "treatment_stage": req.treatment_stage,
        "stage_display": stage_name,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint — triage → layers → synthesis → safety check."""
    patient = get_or_create_patient(req.patient_id)
    query_id = str(uuid.uuid4())[:12]

    # Store user message
    conversations_db[req.patient_id].append({
        "role": "user",
        "content": req.message,
        "timestamp": datetime.now().isoformat(),
    })

    # ── Step 1: Triage ──
    triage_resp = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=10,
        system=TRIAGE_PROMPT,
        messages=[{"role": "user", "content": req.message}],
    )
    try:
        triage_category = int(triage_resp.content[0].text.strip())
    except (ValueError, IndexError):
        triage_category = 1  # Default to emotional

    logger.info(f"[{query_id}] Triage: category={triage_category} for patient={req.patient_id}")

    # ── Step 2: Safety check (parallel with response generation) ──
    context_msgs = get_conversation_context(req.patient_id, last_n=10)
    context_str = "\n".join([f"{m['role']}: {m['content']}" for m in context_msgs[-6:]])

    escalation = None

    # Quick crisis check for category 4
    if triage_category == 4:
        escalation = {
            "level": "RED",
            "reason": "Triage detected crisis-level content",
            "signals": ["triage_crisis_classification"],
            "timestamp": datetime.now().isoformat(),
        }
    else:
        # LLM-based safety check
        safety_resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": ESCALATION_CHECK_PROMPT.format(
                    message=req.message,
                    context=context_str,
                ),
            }],
        )
        try:
            safety_text = safety_resp.content[0].text.strip()
            # Try to parse JSON from the response
            if "{" in safety_text:
                json_str = safety_text[safety_text.index("{"):safety_text.rindex("}") + 1]
                safety_result = json.loads(json_str)
                if safety_result.get("level") in ("AMBER", "RED"):
                    escalation = {
                        **safety_result,
                        "timestamp": datetime.now().isoformat(),
                    }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[{query_id}] Safety check parse error: {e}")

    # Also check daily check-in patterns
    daily_esc = check_daily_escalation(req.patient_id)
    if daily_esc["level"] == "AMBER" and (escalation is None or escalation.get("level") == "GREEN"):
        escalation = {
            "level": "AMBER",
            "reason": "Daily check-in pattern concern",
            "signals": daily_esc["triggers"],
            "timestamp": datetime.now().isoformat(),
        }

    # Store escalation if triggered
    if escalation and escalation.get("level") != "GREEN":
        escalations_db.setdefault(req.patient_id, []).append(escalation)
        logger.warning(f"[{query_id}] ESCALATION: {escalation['level']} for patient={req.patient_id}")

    # ── Step 3: Generate companion response ──
    # Retrieve education RAG content if this is an education query
    rag_context = ""
    if triage_category == 2:  # Education question
        rag_context = retrieve_education(req.message, patient["treatment_stage"])

    system_prompt = COMPANION_SYSTEM.format(
        patient_context=build_patient_context(req.patient_id),
        education_context=build_education_context(req.patient_id) + rag_context,
    )

    # Add safety-aware instructions if escalation detected
    if escalation:
        if escalation["level"] == "RED":
            system_prompt += """

SAFETY ALERT — RED:
The patient may be in acute distress. Your response MUST:
1. Validate their feelings with deep empathy
2. Gently ask if they're safe
3. Provide the crisis support line: Lifeline 13 11 14 (Australia) or 988 (US)
4. Let them know their clinic support team is being notified
5. Stay present — don't end the conversation abruptly
Do NOT diagnose. Do NOT minimise. Just be there."""
        elif escalation["level"] == "AMBER":
            system_prompt += """

SAFETY NOTE — AMBER:
The patient is showing elevated distress. Be especially:
- Validating and warm
- Gently explore what's driving the distress
- Offer to connect them with their clinic's support team
- Consider suggesting a full check-in if not done recently
Do NOT be alarmist. Just be attentive and caring."""

    # Build conversation messages for Claude
    conv_messages = []
    for msg in get_conversation_context(req.patient_id, last_n=16):
        conv_messages.append({
            "role": msg["role"],
            "content": msg["content"],
        })

    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=800,
        system=system_prompt,
        messages=conv_messages,
    )

    assistant_msg = response.content[0].text

    # Store response
    conversations_db[req.patient_id].append({
        "role": "assistant",
        "content": assistant_msg,
        "timestamp": datetime.now().isoformat(),
        "triage": triage_category,
        "query_id": query_id,
    })

    # Suggested education topics
    stage = patient["treatment_stage"]
    suggested = EDUCATION_TOPICS.get(stage, [])[:3] if triage_category == 2 else None

    return ChatResponse(
        response=assistant_msg,
        patient_id=req.patient_id,
        treatment_stage=stage,
        escalation=escalation,
        suggested_education=suggested,
        query_id=query_id,
    )


@app.post("/checkin", response_model=CheckInResponse)
async def daily_checkin(req: CheckInRequest):
    """Record a daily micro check-in and generate a response."""
    patient = get_or_create_patient(req.patient_id)

    checkin = {
        "date": datetime.now().isoformat(),
        "mood": req.mood,
        "anxiety": req.anxiety,
        "loneliness": req.loneliness,
        "uncertainty": req.uncertainty,
        "hope": req.hope,
        "note": req.note,
    }
    checkins_db[req.patient_id].append(checkin)

    # Check escalation triggers
    esc = check_daily_escalation(req.patient_id)
    escalation = None
    if esc["level"] != "GREEN":
        escalation = {
            "level": esc["level"],
            "triggers": esc["triggers"],
            "timestamp": datetime.now().isoformat(),
        }
        escalations_db.setdefault(req.patient_id, []).append(escalation)

    # Check if we should trigger a validated screening
    trigger_screening = None
    recent = get_recent_checkins(req.patient_id)

    # Trigger PHQ-9 if mood persistently low
    if len(recent) >= 3 and all(c["mood"] <= 3 for c in recent[-3:]):
        last_phq = [s for s in screenings_db.get(req.patient_id, []) if s["instrument"] in ("PHQ-9", "PHQ-2")]
        if not last_phq or (datetime.now() - datetime.fromisoformat(last_phq[-1]["date"])).days >= 7:
            trigger_screening = "PHQ-9"

    # Trigger GAD-7 if anxiety persistently high
    if len(recent) >= 3 and all(c["anxiety"] >= 7 for c in recent[-3:]):
        last_gad = [s for s in screenings_db.get(req.patient_id, []) if s["instrument"] == "GAD-7"]
        if not last_gad or (datetime.now() - datetime.fromisoformat(last_gad[-1]["date"])).days >= 7:
            trigger_screening = "GAD-7"

    # Generate companion response to check-in
    summary = (
        f"Today's check-in: mood {req.mood}/10, anxiety {req.anxiety}/10, "
        f"loneliness {req.loneliness}/10, uncertainty {req.uncertainty}/10, hope {req.hope}/10."
    )
    if req.note:
        summary += f" Note: {req.note}"

    prompt = f"""The patient just completed their daily check-in. Here are the scores:
- Mood: {req.mood}/10
- Anxiety: {req.anxiety}/10
- Loneliness: {req.loneliness}/10
- Uncertainty: {req.uncertainty}/10
- Hope: {req.hope}/10
{"They also shared: " + req.note if req.note else ""}

Respond warmly and briefly (2-3 sentences). Acknowledge the dimension that seems most salient.
If everything looks good, be affirming. If something is low, gently acknowledge it and offer to talk.
Don't list back all the numbers — respond to the feeling, not the data."""

    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=300,
        system=COMPANION_SYSTEM.format(
            patient_context=build_patient_context(req.patient_id),
            education_context="",
        ),
        messages=[{"role": "user", "content": prompt}],
    )

    melod_msg = response.content[0].text

    # Store as conversation
    conversations_db[req.patient_id].append({
        "role": "assistant",
        "content": melod_msg,
        "timestamp": datetime.now().isoformat(),
        "type": "checkin_response",
    })

    return CheckInResponse(
        message=melod_msg,
        patient_id=req.patient_id,
        checkin_summary=checkin,
        escalation=escalation,
        trigger_screening=trigger_screening,
    )


@app.post("/screening", response_model=ScreeningResponse)
async def submit_screening(req: ScreeningRequest):
    """Submit a completed screening instrument and get results."""
    patient = get_or_create_patient(req.patient_id)

    if req.instrument in ("PHQ-2", "PHQ-9"):
        result = score_phq(req.responses)
    elif req.instrument == "GAD-7":
        result = score_gad7(req.responses)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {req.instrument}")

    # Store screening result
    screening_record = {
        "date": datetime.now().isoformat(),
        "instrument": req.instrument,
        "responses": req.responses,
        "total_score": result["total_score"],
        "severity": result["severity"],
    }
    screenings_db.setdefault(req.patient_id, []).append(screening_record)

    # Check escalation
    escalation = None
    esc_level = result.get("escalation_level", "GREEN")
    if esc_level in ("AMBER", "RED"):
        escalation = {
            "level": esc_level,
            "reason": f"{req.instrument} score: {result['total_score']} ({result['severity']})",
            "timestamp": datetime.now().isoformat(),
        }
        if result.get("suicidal_ideation"):
            escalation["critical"] = "Suicidal ideation detected (Item 9)"
            escalation["level"] = "RED"
        escalations_db.setdefault(req.patient_id, []).append(escalation)

    # Generate companion message about the screening
    severity_msg = result["severity"].replace("_", " ")
    prompt = f"""The patient just completed the {req.instrument} screening.
Score: {result['total_score']} — classified as {severity_msg}.
{"⚠ Item 9 (suicidal ideation) was endorsed." if result.get("suicidal_ideation") else ""}

Respond as Melod-AI. Do NOT share the raw score or clinical classification.
Instead, acknowledge their honesty and respond to the emotional content.
If the score suggests they're struggling, gently offer to connect them with support.
If the score is low (they're doing okay), affirm them.
Keep it to 2-3 warm sentences."""

    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=300,
        system=COMPANION_SYSTEM.format(
            patient_context=build_patient_context(req.patient_id),
            education_context="",
        ),
        messages=[{"role": "user", "content": prompt}],
    )

    return ScreeningResponse(
        result=result,
        message=response.content[0].text,
        escalation=escalation,
    )


@app.post("/patient/update")
async def update_patient(req: PatientUpdateRequest):
    """Update patient profile (stage transitions, preferences, etc.)."""
    patient = get_or_create_patient(req.patient_id)

    if req.name is not None:
        patient["name"] = req.name
    if req.treatment_stage is not None:
        if req.treatment_stage in TREATMENT_STAGES:
            old_stage = patient["treatment_stage"]
            patient["treatment_stage"] = req.treatment_stage
            patient["stage_start_date"] = datetime.now().isoformat()
            logger.info(f"Patient {req.patient_id} stage transition: {old_stage} → {req.treatment_stage}")
        else:
            raise HTTPException(status_code=400, detail=f"Invalid stage: {req.treatment_stage}")
    if req.cycle_number is not None:
        patient["cycle_number"] = req.cycle_number
    if req.partner_name is not None:
        patient["partner_name"] = req.partner_name
    if req.clinic_name is not None:
        patient["clinic_name"] = req.clinic_name
    if req.tone_preference is not None:
        patient["preferences"]["tone"] = req.tone_preference

    return {"status": "updated", "patient": patient}


@app.get("/patient/{patient_id}")
async def get_patient(patient_id: str):
    """Get patient profile and recent data."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = patients_db[patient_id]
    return {
        "patient": patient,
        "recent_checkins": get_recent_checkins(patient_id, last_n=14),
        "recent_screenings": screenings_db.get(patient_id, [])[-5:],
        "conversation_count": len(conversations_db.get(patient_id, [])),
        "escalation_history": escalations_db.get(patient_id, [])[-10:],
        "stage_display": STAGE_DISPLAY.get(patient["treatment_stage"], patient["treatment_stage"]),
    }


@app.get("/patient/{patient_id}/trends")
async def get_trends(patient_id: str):
    """Get longitudinal trend data for visualisation."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    checkins = checkins_db.get(patient_id, [])
    screenings = screenings_db.get(patient_id, [])

    return {
        "patient_id": patient_id,
        "checkins": checkins,
        "screenings": screenings,
        "escalations": escalations_db.get(patient_id, []),
    }


# ── Clinician Dashboard Endpoints ────────────────────────────────────

@app.get("/clinician/dashboard")
async def clinician_dashboard():
    """Get overview of all patients for clinician dashboard."""
    overview = []
    for pid, patient in patients_db.items():
        recent_checkins = get_recent_checkins(pid, last_n=3)
        recent_esc = escalations_db.get(pid, [])[-1:] if escalations_db.get(pid) else []

        avg_mood = None
        if recent_checkins:
            avg_mood = round(sum(c["mood"] for c in recent_checkins) / len(recent_checkins), 1)

        # Determine risk level
        risk = "GREEN"
        if recent_esc and recent_esc[0].get("level") == "RED":
            risk = "RED"
        elif recent_esc and recent_esc[0].get("level") == "AMBER":
            risk = "AMBER"

        overview.append({
            "patient_id": pid,
            "name": patient.get("name", "Unknown"),
            "treatment_stage": STAGE_DISPLAY.get(patient["treatment_stage"], patient["treatment_stage"]),
            "cycle_number": patient["cycle_number"],
            "avg_mood_3d": avg_mood,
            "risk_level": risk,
            "last_active": patient["last_active"],
            "last_escalation": recent_esc[0] if recent_esc else None,
        })

    # Sort by risk (RED first, then AMBER, then GREEN)
    risk_order = {"RED": 0, "AMBER": 1, "GREEN": 2}
    overview.sort(key=lambda x: risk_order.get(x["risk_level"], 3))

    return {
        "patients": overview,
        "total": len(overview),
        "alerts": sum(1 for p in overview if p["risk_level"] in ("RED", "AMBER")),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/clinician/patient/{patient_id}/summary")
async def clinician_patient_summary(patient_id: str):
    """Detailed clinician view of a specific patient."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = patients_db[patient_id]
    checkins = checkins_db.get(patient_id, [])
    screenings = screenings_db.get(patient_id, [])
    escalations = escalations_db.get(patient_id, [])

    # Generate AI summary of recent conversations
    recent_conv = get_conversation_context(patient_id, last_n=20)
    summary_text = ""
    if recent_conv:
        conv_str = "\n".join([f"{m['role']}: {m['content']}" for m in recent_conv])
        summary_resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": f"""Summarise this patient's recent conversations for their clinician.
Focus on: emotional state, key concerns, treatment experiences, any red flags.
Be concise (3-5 bullet points). Use clinical language (this is for the clinician, not the patient).

Conversations:
{conv_str}""",
            }],
        )
        summary_text = summary_resp.content[0].text

    return {
        "patient": patient,
        "stage_display": STAGE_DISPLAY.get(patient["treatment_stage"], patient["treatment_stage"]),
        "checkins": checkins,
        "screenings": screenings,
        "escalations": escalations,
        "ai_summary": summary_text,
        "conversation_count": len(conversations_db.get(patient_id, [])),
    }


# ── Screening Question Endpoints ─────────────────────────────────────

@app.get("/screening/questions/{instrument}")
async def get_screening_questions(instrument: str):
    """Get the questions for a screening instrument (both clinical and conversational versions)."""
    if instrument == "PHQ-2":
        return {
            "instrument": "PHQ-2",
            "questions": PHQ2_QUESTIONS,
            "response_options": ["Not at all (0)", "Several days (1)", "More than half the days (2)", "Nearly every day (3)"],
            "conversational_intro": "Can I check in with you about something? Just two quick questions — there are no wrong answers.",
        }
    elif instrument == "PHQ-9":
        return {
            "instrument": "PHQ-9",
            "questions_clinical": PHQ9_QUESTIONS,
            "questions_conversational": PHQ9_CONVERSATIONAL,
            "response_options": ["Not at all (0)", "Several days (1)", "More than half the days (2)", "Nearly every day (3)"],
            "conversational_intro": "I'd like to ask you a few more questions today — just to make sure I'm really hearing how you're doing. Take your time with each one.",
        }
    elif instrument == "GAD-7":
        return {
            "instrument": "GAD-7",
            "questions": GAD7_QUESTIONS,
            "response_options": ["Not at all (0)", "Several days (1)", "More than half the days (2)", "Nearly every day (3)"],
            "conversational_intro": "Let me check in on something — these questions are about how worry and anxiety have been showing up for you lately.",
        }
    elif instrument == "FertiQoL":
        return {
            "instrument": "FertiQoL (subset)",
            "questions": FERTIQOL_SUBSET,
            "response_options": ["Not at all (0)", "A little (1)", "Moderately (2)", "A lot (3)", "Extremely (4)"],
            "conversational_intro": "Since you've reached a milestone in your journey, I'd love to hear how things are going more broadly — not just today, but how the whole experience is sitting with you.",
        }
    else:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {instrument}")


# ── Passive Behavioural Signals ───────────────────────────────────────

PASSIVE_SIGNAL_TYPES = {
    # In-app behavioural signals (collected from browser)
    "typing_speed": "Characters per second during chat input",
    "typing_hesitation": "Pauses > 3s during typing (count)",
    "message_length": "Character count of user message",
    "session_duration": "Total seconds app was active this session",
    "session_start_hour": "Hour of day (0-23) when session started",
    "time_to_first_interaction": "Seconds from app open to first tap/type",
    "checkin_completion_time": "Seconds to complete daily check-in",
    "checkin_slider_changes": "Number of times sliders were adjusted before submit",
    "chat_response_latency": "Seconds between Mira responding and user typing",
    "education_cards_clicked": "Which education topics the patient engaged with",
    "education_cards_avoided": "Topics shown but never clicked",
    "tab_switches": "Number of tab switches in a session",
    "app_visibility_time": "Total seconds app tab was visible (not backgrounded)",
    "app_background_events": "Number of times app went to background",
    "scroll_velocity": "Average scroll speed (pixels/sec) — agitation indicator",
    "touch_pressure_proxy": "Touch duration as pressure proxy (ms per tap)",
    "backspace_ratio": "Ratio of backspaces to characters (indecision/rumination)",
    "emoji_usage": "Emojis used in messages (type and count)",
    "sentiment_shift": "NLP-estimated sentiment delta from previous message",
    "night_usage": "Boolean — app used between 11pm-5am",
    "days_since_last_session": "Gap between sessions (disengagement signal)",
    "geolocation_clinic": "Boolean — user near clinic coordinates (if permission granted)",
    "network_type": "wifi/cellular/offline — proxy for home vs away",
    "screen_orientation_changes": "Restlessness indicator",
    "device_battery_level": "Low battery + late night = concerning pattern",
}


@app.post("/passive/signals")
async def receive_passive_signals(batch: PassiveSignalBatch):
    """Receive a batch of passive behavioural signals from the patient app.

    These are collected silently during normal app usage — no extra input from patient.
    Each signal is tagged with patient_id, treatment stage, and timestamp for
    prospective training dataset construction.
    """
    if batch.patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = patients_db[batch.patient_id]
    patient["last_active"] = datetime.now().isoformat()

    stored = []
    for signal in batch.signals:
        record = {
            "signal_type": signal.get("signal_type", "unknown"),
            "value": signal.get("value"),
            "timestamp": signal.get("timestamp", datetime.now().isoformat()),
            "treatment_stage": patient["treatment_stage"],
            "cycle_number": patient["cycle_number"],
            "metadata": signal.get("metadata", {}),
        }
        passive_signals_db.setdefault(batch.patient_id, []).append(record)
        stored.append(record)

    logger.info(f"Stored {len(stored)} passive signals for patient {batch.patient_id}")

    return {
        "stored": len(stored),
        "patient_id": batch.patient_id,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/passive/signals/{patient_id}")
async def get_passive_signals(patient_id: str, signal_type: Optional[str] = None, last_n: int = 100):
    """Retrieve passive signals for a patient (clinician/research view)."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    signals = passive_signals_db.get(patient_id, [])
    if signal_type:
        signals = [s for s in signals if s["signal_type"] == signal_type]

    return {
        "patient_id": patient_id,
        "signals": signals[-last_n:],
        "total": len(signals),
        "signal_types": list(set(s["signal_type"] for s in passive_signals_db.get(patient_id, []))),
    }


@app.get("/passive/summary/{patient_id}")
async def get_passive_summary(patient_id: str):
    """Get a summary of passive signals for the clinician dashboard.

    Computes derived features for predictive modelling:
    - Engagement score (session frequency, completion rates)
    - Circadian disruption (night usage, irregular timing)
    - Communication changes (message length trends, typing speed)
    - Behavioural activation (education engagement, response latency)
    """
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    signals = passive_signals_db.get(patient_id, [])
    if not signals:
        return {"patient_id": patient_id, "summary": None, "message": "No passive data yet"}

    # Compute derived features
    by_type = {}
    for s in signals:
        by_type.setdefault(s["signal_type"], []).append(s["value"])

    summary = {
        "total_signals": len(signals),
        "signal_types_collected": list(by_type.keys()),
        "date_range": {
            "first": signals[0].get("timestamp"),
            "last": signals[-1].get("timestamp"),
        },
        "derived_features": {},
    }

    # Engagement score
    session_durations = by_type.get("session_duration", [])
    if session_durations:
        vals = [v for v in session_durations if isinstance(v, (int, float))]
        if vals:
            summary["derived_features"]["avg_session_duration_sec"] = round(sum(vals) / len(vals), 1)
            summary["derived_features"]["session_count"] = len(vals)

    # Night usage (circadian disruption)
    night_flags = by_type.get("night_usage", [])
    if night_flags:
        summary["derived_features"]["night_usage_pct"] = round(sum(1 for n in night_flags if n) / len(night_flags) * 100, 1)

    # Message length trend
    msg_lengths = by_type.get("message_length", [])
    if len(msg_lengths) >= 4:
        first_half = msg_lengths[:len(msg_lengths)//2]
        second_half = msg_lengths[len(msg_lengths)//2:]
        f_avg = sum(v for v in first_half if isinstance(v, (int, float))) / max(len(first_half), 1)
        s_avg = sum(v for v in second_half if isinstance(v, (int, float))) / max(len(second_half), 1)
        summary["derived_features"]["message_length_trend"] = "shortening" if s_avg < f_avg * 0.7 else "stable" if s_avg > f_avg * 0.5 else "declining"

    # Typing speed trend
    typing_speeds = by_type.get("typing_speed", [])
    if typing_speeds:
        vals = [v for v in typing_speeds if isinstance(v, (int, float))]
        if vals:
            summary["derived_features"]["avg_typing_speed_cps"] = round(sum(vals) / len(vals), 2)

    # Checkin completion time
    checkin_times = by_type.get("checkin_completion_time", [])
    if checkin_times:
        vals = [v for v in checkin_times if isinstance(v, (int, float))]
        if vals:
            summary["derived_features"]["avg_checkin_time_sec"] = round(sum(vals) / len(vals), 1)

    return {
        "patient_id": patient_id,
        "summary": summary,
    }


# ── Run ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
