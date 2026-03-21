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

from signal_integration import (
    signal_router, get_signal_context_for_patient, patient_signal_store,
    analyze_passive_signals,
)
from firebase_db import db as firebase_db
import os
import json
import uuid
import hashlib
import logging
from datetime import datetime, timedelta, date, timezone
from typing import Optional


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    """Return ISO 8601 timestamp with Z suffix for consistent frontend parsing."""
    return utc_now().isoformat()
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
import anthropic
import asyncio


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

# ── Soft Spots — Known emotional difficulty points per stage ─────────
import random

SOFT_SPOTS = {
    "stimulation": {
        "trigger_days": [5, 7, 10],
        "messages": [
            "Around this point in stimulation, many people tell us the hormones start to feel heavy. That's completely normal.",
            "By now the injections can start to wear you down — physically and emotionally. That's a really common experience.",
            "A lot of people hit a wall around this point in stim. The hormones are real, and so is the exhaustion.",
        ],
        "what_helps": "Some find it helps to talk about the physical side. Others just want to know it's temporary. What would help you?",
    },
    "retrieval_day": {
        "trigger": "stage_entry",
        "messages": [
            "Retrieval day can bring up a lot — nervousness about the procedure, hope about the outcome, or just wanting it to be over.",
            "Today's a big day. However you're feeling right now — nervous, hopeful, numb — it's all valid.",
            "A lot of people describe retrieval day as a mix of relief and anxiety. Whatever you're feeling is okay.",
        ],
        "what_helps": "Want to talk through what to expect, or just want some calm company?",
    },
    "before_retrieval": {
        "trigger": "stage_entry",
        "messages": [
            "The day before retrieval can feel like the longest day. Waiting when you've done everything you can is genuinely hard.",
            "Tomorrow's the big day. It's normal to feel a swirl of emotions right now.",
        ],
        "what_helps": "Would it help to go through what tomorrow looks like, or would you rather talk about something else?",
    },
    "early_tww": {
        "trigger_days": [1, 3],
        "messages": [
            "The first days of waiting are often the hardest because there's nothing to do but wait.",
            "These early days after transfer can feel surreal — like time slows down. You're not imagining it.",
            "A lot of people describe these first few days as a strange limbo. That's a really normal response.",
        ],
        "what_helps": "I can share what's actually happening in your body right now if that helps, or we can talk about something else entirely.",
    },
    "late_tww": {
        "trigger_days": [8, 10, 12],
        "messages": [
            "These final days before the result can feel unbearable. You're not imagining it — this is genuinely one of the hardest parts.",
            "The end of the wait is often harder than the beginning. Every sensation gets analyzed. That's completely understandable.",
            "You're so close to knowing. The intensity of these last days is something everyone describes — you're not overreacting.",
        ],
        "what_helps": "Many people start symptom-spotting around now. Want to talk about what's real vs what's anxiety?",
    },
    "fertilisation_report": {
        "trigger": "stage_entry",
        "messages": [
            "Waiting for the fertilisation report is a unique kind of anxiety. The phone becomes the most important object in the world.",
            "This wait feels different from other waits — it's about your embryos, and that makes it deeply personal.",
        ],
        "what_helps": "Would it help to understand what the embryologist is looking for, or would you rather just talk?",
    },
    "embryo_development": {
        "trigger_days": [3, 5],
        "messages": [
            "Waiting for embryo updates is nerve-wracking. Each call or message from the clinic carries so much weight.",
            "The attrition — losing embryos between day 3 and day 5 — is one of the most emotionally difficult parts. No one prepares you for it.",
        ],
        "what_helps": "Want me to explain what's happening at this stage of development, or would you rather just talk about how you're feeling?",
    },
    "negative_result": {
        "trigger": "stage_entry",
        "messages": [
            "There are no words that make this easier. I'm here.",
            "I'm so sorry. You don't have to say anything right now. I'm here whenever you're ready.",
        ],
        "what_helps": None,
    },
    "chemical_pregnancy": {
        "trigger": "stage_entry",
        "messages": [
            "A chemical pregnancy is a real loss. The fact that it was brief doesn't make it less painful.",
            "This is grief. However you're processing it is valid.",
        ],
        "what_helps": None,
    },
    "miscarriage": {
        "trigger": "stage_entry",
        "messages": [
            "I'm deeply sorry. This loss is profound and you deserve space to feel whatever comes.",
        ],
        "what_helps": None,
    },
    "failed_cycle_acute": {
        "trigger": "stage_entry",
        "messages": [
            "This news is devastating. You did everything right. This is not your fault.",
            "Right now, there's nothing I can say that fixes this. But I'm here.",
        ],
        "what_helps": None,
    },
    "considering_stopping": {
        "trigger": "stage_entry",
        "messages": [
            "Thinking about stopping takes as much courage as deciding to start. There's no wrong answer here.",
            "This is one of the hardest decisions in the journey. Whatever you're feeling about it is completely valid.",
        ],
        "what_helps": "Would it help to talk through what you're weighing, or do you just need space to sit with it?",
    },
    "trigger": {
        "trigger": "stage_entry",
        "messages": [
            "Trigger shot day means things are moving. It's normal to feel a rush of emotions — excitement, fear, hope all at once.",
        ],
        "what_helps": "Want to know what happens next, or just want to talk about how you're feeling?",
    },
}

# ── One-word mood mapping ────────────────────────────────────────────
ONE_WORD_MOOD_MAP = {
    # Low mood words
    "tired": {"mood": 3, "anxiety": 4, "loneliness": 4, "uncertainty": 5, "hope": 4},
    "exhausted": {"mood": 2, "anxiety": 4, "loneliness": 5, "uncertainty": 5, "hope": 3},
    "drained": {"mood": 2, "anxiety": 5, "loneliness": 5, "uncertainty": 5, "hope": 3},
    "sad": {"mood": 2, "anxiety": 4, "loneliness": 6, "uncertainty": 5, "hope": 3},
    "low": {"mood": 2, "anxiety": 4, "loneliness": 5, "uncertainty": 5, "hope": 3},
    "flat": {"mood": 3, "anxiety": 3, "loneliness": 5, "uncertainty": 5, "hope": 3},
    "numb": {"mood": 2, "anxiety": 3, "loneliness": 6, "uncertainty": 6, "hope": 2},
    "empty": {"mood": 2, "anxiety": 3, "loneliness": 7, "uncertainty": 6, "hope": 2},
    "heavy": {"mood": 3, "anxiety": 5, "loneliness": 5, "uncertainty": 5, "hope": 3},
    "defeated": {"mood": 1, "anxiety": 4, "loneliness": 6, "uncertainty": 7, "hope": 1},
    "broken": {"mood": 1, "anxiety": 5, "loneliness": 7, "uncertainty": 7, "hope": 1},
    "crying": {"mood": 2, "anxiety": 5, "loneliness": 6, "uncertainty": 5, "hope": 3},
    "down": {"mood": 3, "anxiety": 4, "loneliness": 5, "uncertainty": 5, "hope": 3},
    "miserable": {"mood": 1, "anxiety": 5, "loneliness": 6, "uncertainty": 6, "hope": 2},
    "devastated": {"mood": 1, "anxiety": 6, "loneliness": 7, "uncertainty": 7, "hope": 1},
    "gutted": {"mood": 1, "anxiety": 5, "loneliness": 6, "uncertainty": 6, "hope": 2},
    # Anxiety words
    "scared": {"mood": 4, "anxiety": 8, "loneliness": 4, "uncertainty": 7, "hope": 4},
    "nervous": {"mood": 5, "anxiety": 7, "loneliness": 3, "uncertainty": 6, "hope": 5},
    "anxious": {"mood": 4, "anxiety": 8, "loneliness": 4, "uncertainty": 6, "hope": 4},
    "worried": {"mood": 4, "anxiety": 7, "loneliness": 4, "uncertainty": 7, "hope": 4},
    "panicked": {"mood": 3, "anxiety": 9, "loneliness": 5, "uncertainty": 7, "hope": 3},
    "terrified": {"mood": 3, "anxiety": 9, "loneliness": 5, "uncertainty": 8, "hope": 3},
    "stressed": {"mood": 4, "anxiety": 7, "loneliness": 4, "uncertainty": 6, "hope": 4},
    "overwhelmed": {"mood": 3, "anxiety": 8, "loneliness": 5, "uncertainty": 7, "hope": 3},
    "restless": {"mood": 4, "anxiety": 7, "loneliness": 3, "uncertainty": 6, "hope": 5},
    "tense": {"mood": 4, "anxiety": 7, "loneliness": 3, "uncertainty": 5, "hope": 5},
    # Hope words
    "hopeful": {"mood": 7, "anxiety": 3, "loneliness": 2, "uncertainty": 4, "hope": 8},
    "excited": {"mood": 8, "anxiety": 3, "loneliness": 2, "uncertainty": 3, "hope": 8},
    "ready": {"mood": 7, "anxiety": 3, "loneliness": 2, "uncertainty": 3, "hope": 7},
    "optimistic": {"mood": 7, "anxiety": 3, "loneliness": 2, "uncertainty": 3, "hope": 8},
    "positive": {"mood": 7, "anxiety": 3, "loneliness": 2, "uncertainty": 3, "hope": 7},
    "grateful": {"mood": 7, "anxiety": 3, "loneliness": 2, "uncertainty": 4, "hope": 7},
    "calm": {"mood": 7, "anxiety": 2, "loneliness": 3, "uncertainty": 4, "hope": 6},
    "peaceful": {"mood": 7, "anxiety": 1, "loneliness": 2, "uncertainty": 3, "hope": 7},
    "strong": {"mood": 7, "anxiety": 3, "loneliness": 2, "uncertainty": 3, "hope": 7},
    "brave": {"mood": 6, "anxiety": 4, "loneliness": 3, "uncertainty": 4, "hope": 7},
    "determined": {"mood": 6, "anxiety": 4, "loneliness": 3, "uncertainty": 4, "hope": 7},
    # Loneliness words
    "alone": {"mood": 3, "anxiety": 4, "loneliness": 8, "uncertainty": 5, "hope": 3},
    "isolated": {"mood": 3, "anxiety": 4, "loneliness": 9, "uncertainty": 5, "hope": 3},
    "lonely": {"mood": 3, "anxiety": 3, "loneliness": 9, "uncertainty": 5, "hope": 3},
    "invisible": {"mood": 2, "anxiety": 4, "loneliness": 9, "uncertainty": 6, "hope": 2},
    "misunderstood": {"mood": 3, "anxiety": 4, "loneliness": 8, "uncertainty": 5, "hope": 3},
    # Neutral / mixed
    "okay": {"mood": 5, "anxiety": 4, "loneliness": 4, "uncertainty": 5, "hope": 5},
    "fine": {"mood": 5, "anxiety": 4, "loneliness": 4, "uncertainty": 5, "hope": 5},
    "meh": {"mood": 4, "anxiety": 4, "loneliness": 5, "uncertainty": 5, "hope": 4},
    "whatever": {"mood": 3, "anxiety": 3, "loneliness": 5, "uncertainty": 6, "hope": 3},
    "uncertain": {"mood": 4, "anxiety": 5, "loneliness": 4, "uncertainty": 8, "hope": 4},
    "confused": {"mood": 4, "anxiety": 5, "loneliness": 4, "uncertainty": 8, "hope": 4},
    "frustrated": {"mood": 3, "anxiety": 6, "loneliness": 4, "uncertainty": 6, "hope": 4},
    "angry": {"mood": 3, "anxiety": 6, "loneliness": 4, "uncertainty": 5, "hope": 4},
    "jealous": {"mood": 3, "anxiety": 4, "loneliness": 7, "uncertainty": 5, "hope": 3},
    "resentful": {"mood": 3, "anxiety": 4, "loneliness": 6, "uncertainty": 5, "hope": 3},
    "good": {"mood": 7, "anxiety": 3, "loneliness": 3, "uncertainty": 4, "hope": 6},
    "great": {"mood": 8, "anxiety": 2, "loneliness": 2, "uncertainty": 3, "hope": 7},
    "amazing": {"mood": 9, "anxiety": 2, "loneliness": 1, "uncertainty": 2, "hope": 8},
    "better": {"mood": 6, "anxiety": 4, "loneliness": 3, "uncertainty": 4, "hope": 6},
    "surviving": {"mood": 4, "anxiety": 5, "loneliness": 5, "uncertainty": 6, "hope": 4},
    "coping": {"mood": 5, "anxiety": 5, "loneliness": 4, "uncertainty": 5, "hope": 5},
}


def get_soft_spot_context(patient_id: str) -> Optional[dict]:
    """Check if patient is at a known emotional difficulty point."""
    patient = get_or_create_patient(patient_id)
    stage = patient.get("treatment_stage", "")
    spot = SOFT_SPOTS.get(stage)
    if not spot:
        return None

    # Check trigger type
    if spot.get("trigger") == "stage_entry":
        # Always relevant when at this stage
        return {
            "message": random.choice(spot["messages"]),
            "what_helps": spot.get("what_helps"),
            "stage": stage,
        }

    # Day-based triggers
    trigger_days = spot.get("trigger_days", [])
    if trigger_days:
        stage_start = patient.get("stage_start_date")
        if stage_start:
            try:
                start_dt = datetime.fromisoformat(stage_start.replace("Z", "+00:00"))
                days_in_stage = (utc_now() - start_dt).days
                # Check if within 1 day of a trigger point
                for td in trigger_days:
                    if abs(days_in_stage - td) <= 1:
                        return {
                            "message": random.choice(spot["messages"]),
                            "what_helps": spot.get("what_helps"),
                            "stage": stage,
                            "days_in_stage": days_in_stage,
                        }
            except (ValueError, TypeError):
                pass

    return None


def map_one_word_to_checkin(word: str) -> Optional[dict]:
    """Map a single word/short phrase to check-in dimensions."""
    word_clean = word.strip().lower().rstrip(".,!?…")
    # Direct match
    if word_clean in ONE_WORD_MOOD_MAP:
        return ONE_WORD_MOOD_MAP[word_clean]
    # Fuzzy: check if any key is contained in the word
    for key, vals in ONE_WORD_MOOD_MAP.items():
        if key in word_clean:
            return vals
    return None


def build_smart_greeting(patient_id: str) -> str:
    """Build a contextual opening message instead of generic greeting."""
    patient = get_or_create_patient(patient_id)
    name = patient.get("name", "there")
    stage = patient.get("treatment_stage", "consultation")
    stage_name = STAGE_DISPLAY.get(stage, stage)

    parts = []

    # ── Time of day awareness ──
    now = utc_now()
    # Approximate — user timezone not stored, but we can be gentle
    hour = now.hour  # UTC — imperfect but a start
    if hour >= 22 or hour < 5:
        time_greetings = [
            f"It's late, {name} — can't sleep?",
            f"Hey {name}, burning the midnight oil?",
            f"Late-night thoughts? I'm here, {name}.",
        ]
        parts.append(random.choice(time_greetings))
    elif hour < 12:
        parts.append(random.choice([
            f"Good morning, {name}.",
            f"Morning, {name}.",
            f"Hi {name} — how's today starting?",
        ]))
    elif hour < 18:
        parts.append(random.choice([
            f"Hey {name}.",
            f"Hi {name}.",
            f"Afternoon, {name}.",
        ]))
    else:
        parts.append(random.choice([
            f"Evening, {name}.",
            f"Hey {name} — how's today been?",
            f"Hi {name}.",
        ]))

    # ── Streak & rhythm awareness ──
    engagement = patient.get("engagement", {})
    consec = engagement.get("consecutive_days", 0)
    gap_ack = engagement.get("gap_acknowledged", False)

    if consec == 3:
        parts.append("Three days straight you've shown up for yourself. That matters.")
    elif consec == 7:
        parts.append("A whole week of checking in. You're building a habit of self-care through this.")
    elif consec >= 14:
        parts.append(f"{'Two' if consec < 21 else str(consec // 7) + ' '}weeks of showing up. In the middle of everything you're going through, that's remarkable.")
    elif consec == 0 and not gap_ack:
        # Check for gap — the days_since logic below will handle messaging
        pass

    # ── Days since last check-in / conversation ──
    checkins = checkins_db.get(patient_id, [])
    last_checkin = checkins[-1] if checkins else None
    conv = conversations_db.get(patient_id, [])
    last_user_msg = None
    for m in reversed(conv):
        if m.get("role") == "user":
            last_user_msg = m
            break

    days_since = None
    if last_user_msg and last_user_msg.get("timestamp"):
        try:
            last_ts = datetime.fromisoformat(last_user_msg["timestamp"].replace("Z", "+00:00"))
            days_since = (utc_now() - last_ts).days
        except (ValueError, TypeError):
            pass

    if days_since and days_since >= 3:
        parts.append(random.choice([
            f"It's been a few days. How are things?",
            f"I've been thinking about you. How have the last few days been?",
            f"It's been {days_since} days since we last talked. No pressure — just wanted to check in.",
        ]))
    elif days_since and days_since >= 1:
        parts.append(random.choice([
            "How are things today?",
            "What's on your mind today?",
        ]))

    # ── Last mood score reference ──
    if last_checkin:
        mood = last_checkin.get("mood", 5)
        if mood <= 3:
            parts.append(random.choice([
                "Last time we talked, you were having a tough time. How are things now?",
                "You were feeling pretty low last time. Has anything shifted?",
                "I remember things were hard last time. How are you doing?",
            ]))
        elif mood >= 7:
            parts.append(random.choice([
                "You were in a good place last time. Hope that's holding up.",
                "Things were feeling better last time — how's today?",
            ]))

    # ── Soft spot awareness ──
    soft_spot = get_soft_spot_context(patient_id)
    if soft_spot:
        parts.append(soft_spot["message"])
        if soft_spot.get("what_helps"):
            parts.append(soft_spot["what_helps"])
    elif not days_since or days_since == 0:
        # Stage-specific openers when no soft spot and first conversation or same day
        stage_openers = {
            "stimulation": [
                f"You're in the thick of stimulation. How's your body feeling?",
                f"Stim days can be a lot. How are you going?",
            ],
            "monitoring": [
                f"Monitoring can feel like a lot of waiting between scans. How are you holding up?",
            ],
            "early_tww": [
                f"The two-week wait is its own kind of challenge. How are you managing?",
            ],
            "late_tww": [
                f"These final days of waiting are intense. I'm here if you need to talk.",
            ],
            "between_cycles": [
                f"Time between cycles can feel like limbo. How are you using this space?",
                f"Are you giving yourself permission to rest, or does your mind keep going?",
            ],
            "early_pregnancy": [
                f"Early pregnancy after IVF comes with its own set of worries. How are you feeling?",
            ],
        }
        if stage in stage_openers and not last_checkin:
            parts.append(random.choice(stage_openers[stage]))

    # If we only have the time greeting, add something gentle
    if len(parts) <= 1:
        parts.append(random.choice([
            "I'm here whenever you're ready to talk.",
            "What's on your mind?",
            "How are you feeling?",
        ]))

    return "\n\n".join(parts)


# ── Conversation Continuity ─────────────────────────────────────────

def summarize_last_conversations(patient_id: str, last_n: int = 3) -> list[str]:
    """Return brief summaries of the patient's last N conversations for continuity."""
    conv = conversations_db.get(patient_id, [])
    if not conv:
        return []

    # Group messages into sessions (gap of 2+ hours = new session)
    sessions = []
    current_session = []
    last_ts = None
    for msg in conv:
        ts_str = msg.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = None

        if last_ts and ts and (ts - last_ts).total_seconds() > 7200:
            if current_session:
                sessions.append(current_session)
            current_session = [msg]
        else:
            current_session.append(msg)
        if ts:
            last_ts = ts

    if current_session:
        sessions.append(current_session)

    # Take last N sessions (skip the current one if it just started)
    recent = sessions[-last_n - 1:-1] if len(sessions) > 1 else []
    if not recent and len(sessions) == 1 and len(sessions[0]) > 2:
        recent = sessions[-1:]

    summaries = []
    for session in recent[-last_n:]:
        user_msgs = [m["content"] for m in session if m.get("role") == "user"]
        ai_msgs = [m for m in session if m.get("role") == "assistant"]

        # Get timestamp for relative date
        ts_str = session[0].get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            days_ago = (utc_now() - ts).days
            if days_ago == 0:
                when = "Earlier today"
            elif days_ago == 1:
                when = "Yesterday"
            else:
                when = f"{days_ago} days ago"
        except (ValueError, TypeError):
            when = "Recently"

        # Extract topics from user messages
        all_text = " ".join(user_msgs).lower()
        topics = []
        topic_keywords = {
            "progesterone": "progesterone", "estrogen": "estrogen", "clexane": "clexane",
            "embryo": "embryo", "transfer": "transfer", "retrieval": "retrieval",
            "injection": "injections", "side effect": "side effects", "amh": "AMH",
            "ivf": "IVF process", "scan": "scans", "blood": "blood tests",
            "medication": "medications", "pregnant": "pregnancy", "symptom": "symptoms",
            "alone": "loneliness", "scared": "fears", "anxious": "anxiety",
            "partner": "partner", "work": "work", "sleep": "sleep",
            "waiting": "the wait", "result": "results", "egg": "eggs",
            "sperm": "sperm", "clinic": "clinic",
        }
        for keyword, topic in topic_keywords.items():
            if keyword in all_text and topic not in topics:
                topics.append(topic)
                if len(topics) >= 3:
                    break

        # Detect emotional tone
        tone = "neutral"
        neg_words = sum(1 for w in ["sad", "scared", "anxious", "worried", "alone", "afraid", "crying", "angry", "frustrated", "hopeless", "overwhelmed"] if w in all_text)
        pos_words = sum(1 for w in ["happy", "hopeful", "grateful", "excited", "better", "good", "relieved", "calm", "positive"] if w in all_text)
        if neg_words > pos_words:
            tone = "anxious" if "anxious" in all_text or "worried" in all_text else "struggling"
        elif pos_words > neg_words:
            tone = "hopeful" if "hope" in all_text else "positive"
        elif "?" in " ".join(user_msgs):
            tone = "curious"

        # Build summary line
        topic_str = ", ".join(topics[:2]) if topics else "general chat"
        summary = f"{when}: talked about {topic_str}, seemed {tone}"
        summaries.append(summary)

    return summaries[-last_n:]


# ── Daily Insight ───────────────────────────────────────────────────

EVENING_PROMPTS = {
    "stimulation": "Your body did a lot of work today. Thank it.",
    "monitoring": "Another scan day done. Rest now.",
    "early_tww": "Another day closer. That's enough for today.",
    "late_tww": "Another day closer. That's enough for today.",
    "retrieval_day": "Your body did something incredible today. Rest well.",
    "transfer_day": "A tiny passenger is on board. Breathe easy tonight.",
    "negative_result": "You made it through today. That's not nothing.",
    "miscarriage": "You made it through today. That's not nothing.",
    "failed_cycle_acute": "You made it through today. That's not nothing.",
    "chemical_pregnancy": "You made it through today. That's not nothing.",
}

EVENING_GENERIC = [
    "Before you close your eyes tonight — one thing from today that wasn't terrible?",
    "Today is done. You showed up. That counts.",
    "Whatever today was — you made it through. Rest well.",
]


# ── Common IVF Topics Knowledge Base ─────────────────────────────────
COMMON_IVF_TOPICS = {
    "amh": {
        "keywords": ["amh", "anti-mullerian", "anti mullerian", "ovarian reserve", "egg reserve"],
        "name": "AMH (Anti-Müllerian Hormone)",
        "summary": "AMH is a blood test that estimates your remaining egg supply. It's a snapshot, not a destiny.",
        "analytical": "AMH is produced by granulosa cells of pre-antral and small antral follicles. Normal range is roughly 1.0–3.5 ng/mL (7–25 pmol/L). Low AMH (<1.0 ng/mL) suggests diminished ovarian reserve but does NOT predict egg quality. Many women with low AMH conceive. It helps your specialist choose the right stimulation dose.",
        "emotional": "Getting an AMH number can feel like getting a grade — but it's not a pass/fail. It's one piece of a much bigger puzzle. Women with 'low' numbers have babies every day, and a 'good' number doesn't guarantee anything either.",
        "practical": "Ask your specialist: 'What does my AMH mean for my protocol?' AMH can fluctuate slightly cycle to cycle. Retest only if your specialist recommends it.",
    },
    "progesterone": {
        "keywords": ["progesterone", "pessaries", "crinone", "utrogestan", "endometrin", "PIO", "progesterone in oil"],
        "name": "Progesterone Support",
        "summary": "Progesterone helps prepare and maintain your uterine lining for embryo implantation.",
        "analytical": "After egg retrieval, the corpus luteum may not produce sufficient progesterone. Supplementation (vaginal pessaries, gel, or intramuscular PIO) maintains the endometrial lining in its secretory phase. Typical start is 1-2 days after retrieval. Continue until 8-12 weeks if pregnant.",
        "emotional": "The pessaries can feel like an annoying chore on top of everything else. The discharge and mess are normal — it doesn't mean the medication isn't working.",
        "practical": "Insert at consistent times. Lying down for 10-15 min after helps absorption. Side effects (bloating, breast tenderness, mood changes) mimic pregnancy symptoms — try not to symptom-spot based on these.",
    },
    "trigger_shot": {
        "keywords": ["trigger shot", "trigger injection", "ovidrel", "pregnyl", "hcg trigger", "lupron trigger"],
        "name": "Trigger Shot",
        "summary": "The trigger shot tells your eggs to complete final maturation so they can be retrieved ~36 hours later.",
        "analytical": "The trigger (typically hCG or GnRH agonist) induces final oocyte maturation. Timing is precise: retrieval is scheduled 34-36 hours post-trigger. hCG triggers carry slightly more OHSS risk than agonist triggers.",
        "emotional": "Trigger night can feel surreal — you've been building to this moment. The precise timing can feel stressful, but clinics are very experienced at scheduling this.",
        "practical": "Set 2-3 alarms. Have supplies laid out in advance. If you miss the window, call your clinic immediately. Take a photo of the syringe/vial as a record.",
    },
    "egg_freezing": {
        "keywords": ["egg freezing", "freeze eggs", "fertility preservation", "social freezing", "oocyte cryopreservation"],
        "name": "Egg Freezing",
        "summary": "Egg freezing preserves your eggs at their current quality for future use.",
        "analytical": "Vitrification achieves >90% egg survival rates. Ideal age for freezing is under 35. Each cycle typically retrieves 8-15 eggs; most specialists suggest 15-20 mature eggs for a reasonable chance at one live birth.",
        "emotional": "Egg freezing can feel empowering — you're taking control. But it can also bring up complicated feelings about timelines and partnerships. Both are valid.",
        "practical": "Budget for 1-2 cycles. Storage fees are annual (~$300-500/year in Australia). Medicare rebates apply for medical indications but not elective freezing.",
    },
    "embryo_grading": {
        "keywords": ["embryo grade", "embryo grading", "blastocyst grade", "day 5 grade", "4AA", "5AB", "hatching"],
        "name": "Embryo Grading",
        "summary": "Embryo grading describes how an embryo looks under the microscope — a rough guide, not a guarantee.",
        "analytical": "Day 5 blastocysts are graded on expansion (1-6), inner cell mass (A-C), and trophectoderm (A-C). A '4AA' means fully expanded, top quality. However, a 'BB' embryo can absolutely become a healthy baby. PGT-A tested euploid embryos have ~60-70% implantation rates regardless of morphology.",
        "emotional": "Getting your embryo report can feel like results day at school. Remember: embryologists see 'average-looking' embryos become beautiful babies all the time.",
        "practical": "Ask your embryologist to explain YOUR grades. Don't compare to others online — different clinics use slightly different scales.",
    },
    "tww_symptoms": {
        "keywords": ["tww", "two week wait", "2ww", "symptom spotting", "implantation", "cramping after transfer"],
        "name": "The Two-Week Wait (TWW)",
        "summary": "The TWW is the period between transfer and your pregnancy test. Symptom-spotting is universal but unreliable.",
        "analytical": "Implantation typically occurs 6-10 days post-ovulation (1-5 days post day-5 transfer). Progesterone supplementation causes symptoms identical to early pregnancy. There is NO reliable way to distinguish medication side effects from pregnancy symptoms before the blood test.",
        "emotional": "The TWW might be the longest two weeks of your life. Every twinge becomes a Google search. This is completely normal.",
        "practical": "Avoid home pregnancy tests before your clinic's blood test date. Distraction helps: plan activities, start a show, see friends. Light movement is fine.",
    },
    "fsh": {
        "keywords": ["fsh", "follicle stimulating hormone", "day 3 fsh", "baseline fsh"],
        "name": "FSH (Follicle-Stimulating Hormone)",
        "summary": "FSH stimulates your ovaries. Your baseline level helps assess ovarian function.",
        "analytical": "Day 2-3 FSH <10 IU/L is generally normal. Elevated FSH (>10-15) may suggest diminished ovarian reserve. FSH fluctuates cycle to cycle more than AMH. Interpreted alongside estradiol, AMH, and AFC.",
        "emotional": "Like AMH, an FSH number is just one data point. If yours is elevated, it doesn't close doors — it helps your specialist choose the best approach.",
        "practical": "FSH is drawn on cycle day 2-3 along with estradiol. If elevated, ask about AMH and AFC for a more complete picture.",
    },
    "follicle_count": {
        "keywords": ["follicle count", "afc", "antral follicle", "how many follicles", "follicle scan"],
        "name": "Antral Follicle Count (AFC)",
        "summary": "AFC is the number of small resting follicles on ultrasound — it predicts stimulation response.",
        "analytical": "AFC measured via transvaginal ultrasound on day 2-5. Normal AFC is 10-20 total. <6 suggests low reserve; >20 suggests possible PCOS. AFC combined with AMH gives the most accurate response prediction.",
        "emotional": "Counting follicles can feel like counting chances. But follicle count tells you about quantity potential, not quality.",
        "practical": "Don't compare your count to others. During stimulation, not all follicles grow at the same rate — that's normal.",
    },
    "icsi_vs_ivf": {
        "keywords": ["icsi", "icsi vs ivf", "conventional ivf", "intracytoplasmic", "sperm injection"],
        "name": "ICSI vs Conventional IVF",
        "summary": "In conventional IVF, sperm and eggs are mixed. In ICSI, a single sperm is injected directly into each egg.",
        "analytical": "ICSI is recommended for male factor, previous fertilisation failure, PGT-A cycles, or frozen eggs. Fertilisation rates are similar (~70-80%) when appropriately indicated. ICSI does not improve outcomes when sperm parameters are normal.",
        "emotional": "If your clinic recommends ICSI, it's because they want to give your eggs the best chance. It's a very routine procedure.",
        "practical": "Ask why they're recommending ICSI vs conventional for your situation. Cost may differ.",
    },
    "pgt": {
        "keywords": ["pgt", "pgs", "pgt-a", "genetic testing", "preimplantation", "euploid", "aneuploid", "mosaic"],
        "name": "PGT-A (Preimplantation Genetic Testing)",
        "summary": "PGT-A tests embryos for the correct number of chromosomes before transfer.",
        "analytical": "PGT-A biopsies 5-10 trophectoderm cells from day 5-7 blastocysts. Euploid embryos have ~60-70% implantation rates. Aneuploidy rate increases sharply after age 37. Mosaic results are increasingly considered for transfer.",
        "emotional": "Waiting for PGT results adds another layer of waiting. Some embryos that looked great won't pass, and that's a real loss.",
        "practical": "Results take 1-2 weeks. Ask about your clinic's mosaic embryo policy. PGT-A adds ~$3,000-5,000 per cycle. Consider it especially if you're 37+.",
    },
    "endometriosis": {
        "keywords": ["endometriosis", "endo", "endometrioma", "chocolate cyst", "adenomyosis"],
        "name": "Endometriosis & Fertility",
        "summary": "Endometriosis can affect fertility but many women with endo conceive with treatment.",
        "analytical": "Staged I-IV. Even mild endo can reduce fertility via inflammatory factors. Endometriomas >4cm may warrant drainage before IVF. AMH may be lower with bilateral endometriomas.",
        "emotional": "Living with endo AND doing IVF is a double load. You deserve extra gentleness with yourself.",
        "practical": "Discuss whether surgical treatment before IVF is recommended for your situation. Keep a pain diary to track patterns.",
    },
    "pcos": {
        "keywords": ["pcos", "polycystic", "metformin", "insulin resistance", "anovulation"],
        "name": "PCOS & Fertility Treatment",
        "summary": "PCOS is common and very treatable. Women with PCOS often respond strongly to stimulation.",
        "analytical": "PCOS affects 8-13% of women. In IVF, PCOS patients typically produce more eggs but OHSS risk is elevated. Antagonist protocols with agonist triggers are preferred. Metformin may improve egg quality.",
        "emotional": "PCOS can feel like your body is working against you. But a strong response to medication is actually an advantage.",
        "practical": "Ask about OHSS prevention strategies. Low-GI diet and moderate exercise can help with insulin resistance.",
    },
    "male_factor": {
        "keywords": ["male factor", "sperm count", "motility", "morphology", "low sperm", "azoospermia", "sperm analysis"],
        "name": "Male Factor Infertility",
        "summary": "Male factor contributes to about 40-50% of infertility cases. ICSI has transformed outcomes.",
        "analytical": "WHO normal values: count >15M/mL, motility >40%, morphology >4%. Severe cases may require surgical sperm retrieval (TESA/micro-TESE). Lifestyle factors can improve parameters over 2-3 months.",
        "emotional": "Male factor affects both partners emotionally. It's a medical condition, not a personal failing.",
        "practical": "A repeat semen analysis is standard. 3 months of lifestyle optimisation can improve results. Ask about DNA fragmentation testing if borderline.",
    },
    "clexane": {
        "keywords": ["clexane", "enoxaparin", "blood thinner", "thrombophilia", "heparin"],
        "name": "Clexane (Enoxaparin)",
        "summary": "Clexane is a blood thinner sometimes prescribed to improve blood flow to the uterus.",
        "analytical": "Low-molecular-weight heparin prescribed for thrombophilia, recurrent implantation failure, or antiphospholipid syndrome. Typical dose 20-40mg daily subcutaneous. Evidence for routine use without specific indication is limited.",
        "emotional": "Adding another injection can feel overwhelming. The bruising is normal and doesn't mean anything is wrong.",
        "practical": "Rotate injection sites. Ice before injecting to reduce bruising. Tell your dentist you're on blood thinners.",
    },
    "ivf_process": {
        "keywords": ["ivf process", "how does ivf work", "ivf steps", "ivf cycle", "what happens in ivf"],
        "name": "The IVF Process Overview",
        "summary": "IVF involves stimulating ovaries, collecting eggs, fertilising in the lab, growing embryos, and transferring back.",
        "analytical": "Standard cycle: (1) Stimulation 8-14 days, (2) Trigger shot at ~18-20mm follicles, (3) Retrieval under sedation 36h post-trigger, (4) Fertilisation, (5) Culture to day 3-6, (6) Transfer or freeze-all, (7) Luteal support, (8) Pregnancy test ~14 days post-retrieval. Timeline: ~4-6 weeks.",
        "emotional": "Starting IVF can feel like stepping onto a conveyor belt. But you can ask questions at every step and advocate for yourself.",
        "practical": "Plan flexibility at work around monitoring (usually mornings) and retrieval day. Start a medication organiser.",
    },
    "fresh_vs_frozen": {
        "keywords": ["fresh transfer", "frozen transfer", "fet", "freeze all", "fresh vs frozen"],
        "name": "Fresh vs Frozen Embryo Transfer",
        "summary": "Frozen transfers (FET) are now as successful as — and sometimes better than — fresh transfers.",
        "analytical": "Freeze-all allows the uterine lining to recover from stimulation. FET success rates are comparable to or slightly better than fresh in many studies. OHSS risk is eliminated with freeze-all.",
        "emotional": "Being told to 'freeze all' can feel like a delay. But it's usually because your body needs time to recover.",
        "practical": "FET typically happens 1-2 months after retrieval. The FET process is much simpler — no sedation needed.",
    },
    "miscarriage_info": {
        "keywords": ["miscarriage", "pregnancy loss", "missed miscarriage", "recurrent loss"],
        "name": "Understanding Pregnancy Loss",
        "summary": "Miscarriage after IVF is heartbreaking but not uncommon. It is not your fault.",
        "analytical": "Miscarriage rate after IVF is ~15-25%, similar to natural conception. Most are due to chromosomal abnormalities. Recurrent loss warrants investigation. PGT-A can reduce risk in subsequent cycles.",
        "emotional": "A miscarriage after everything it took to get there is devastating. The grief is real. You're allowed to mourn, to be angry, and when ready, to try again.",
        "practical": "Allow yourself time to grieve. Ask about investigations before your next cycle. Many clinics offer counselling.",
    },
    "chemical_pregnancy": {
        "keywords": ["chemical pregnancy", "biochemical pregnancy", "faint line then period"],
        "name": "Chemical Pregnancy",
        "summary": "A chemical pregnancy is a very early loss where hCG was briefly detected. It IS a real loss.",
        "analytical": "Chemical pregnancies account for up to 50-75% of early losses. A chemical pregnancy confirms implantation occurred, which some specialists view as a positive prognostic sign.",
        "emotional": "A chemical pregnancy can feel like a cruel trick — hope followed immediately by loss. Your feelings are valid, whatever they are.",
        "practical": "Most clinics proceed after one normal period. If it happens repeatedly, ask about endometrial receptivity testing (ERA).",
    },
    "ohss": {
        "keywords": ["ohss", "ovarian hyperstimulation", "bloating after retrieval", "swollen ovaries"],
        "name": "OHSS (Ovarian Hyperstimulation Syndrome)",
        "summary": "OHSS is when ovaries over-respond to stimulation. Mild is common; severe is rare and manageable.",
        "analytical": "Mild OHSS (bloating, mild pain) affects ~20-30% of cycles. Moderate-severe (<5%) involves fluid shifts and weight gain. Risk factors: PCOS, high AFC, hCG trigger. Prevention: antagonist protocol, agonist trigger, freeze-all.",
        "emotional": "Feeling bloated and uncomfortable after retrieval is incredibly common. If it gets worse, don't push through — call your clinic.",
        "practical": "Monitor: weigh daily, track fluid intake/output. Drink electrolyte drinks. Eat salty, high-protein foods. Call clinic if weight gain >1kg/day or difficulty breathing.",
    },
    "natural_cycle": {
        "keywords": ["natural cycle", "mini ivf", "mild stimulation", "natural ivf"],
        "name": "Natural & Mini IVF",
        "summary": "Natural and mini IVF use little or no medication, collecting 1-3 eggs. Gentler but may need more cycles.",
        "analytical": "Natural IVF retrieves 0-1 eggs per cycle. Modified natural with mild stimulation gets 1-3 eggs. Success rates per cycle are lower but cumulative rates over multiple cycles can be comparable.",
        "emotional": "Choosing a gentler approach can feel like taking care of yourself. But it can also mean more cycles, requiring patience and resilience.",
        "practical": "Discuss whether natural/mini IVF suits your diagnosis. Costs per cycle are lower but you may need more cycles. Cancellation rates are higher.",
    },
}


def detect_education_intent(message: str, patient_style: str, conversation_history: list = None) -> dict:
    """Detect the education intent behind a patient's question.

    Returns dict with intent, matched_topic, and confidence.
    """
    msg_lower = message.lower().strip()

    # ── Match topic ──
    matched_topic = None
    best_keyword_count = 0
    for topic_key, topic_data in COMMON_IVF_TOPICS.items():
        hits = sum(1 for kw in topic_data["keywords"] if kw in msg_lower)
        if hits > best_keyword_count:
            best_keyword_count = hits
            matched_topic = topic_key

    # ── Detect intent from message signals ──
    reassurance_signals = [
        "is it normal", "should i worry", "is this okay", "am i okay",
        "is this bad", "worried about", "scared", "nervous", "freaking out",
        "panicking", "does this mean", "is it safe", "am i normal",
        "is something wrong", "what if", "concerned", "anxious about",
    ]
    explain_signals = [
        "how does", "what is", "what are", "explain", "tell me about",
        "why do", "why does", "what happens", "how do they", "what's the difference",
        "mechanism", "how it works", "the science", "technically",
        "what does it mean", "can you explain", "walk me through",
    ]
    practical_signals = [
        "what should i", "how do i", "tips", "advice", "prepare",
        "when do i", "do i need to", "what to expect", "how to",
        "side effects", "what to avoid", "can i", "should i take",
        "how much", "how long", "schedule", "plan for", "steps",
    ]

    r_score = sum(1 for s in reassurance_signals if s in msg_lower)
    e_score = sum(1 for s in explain_signals if s in msg_lower)
    p_score = sum(1 for s in practical_signals if s in msg_lower)

    # Style-weighted adjustment
    if patient_style == "EMOTIONAL":
        r_score += 1
    elif patient_style == "ANALYTICAL":
        e_score += 1

    # Determine intent
    intent = None
    confidence = 0.0
    total = r_score + e_score + p_score

    if total > 0:
        scores = {"REASSURANCE_FIRST": r_score, "EXPLAIN_FIRST": e_score, "PRACTICAL_FIRST": p_score}
        intent = max(scores, key=scores.get)
        confidence = scores[intent] / total
    elif matched_topic:
        if patient_style == "EMOTIONAL":
            intent = "REASSURANCE_FIRST"
            confidence = 0.5
        elif patient_style == "ANALYTICAL":
            intent = "EXPLAIN_FIRST"
            confidence = 0.5
        else:
            intent = None
            confidence = 0.3

    return {
        "intent": intent,
        "matched_topic": matched_topic,
        "confidence": confidence,
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


# ── Fertool Interactive Content Cards ────────────────────────────────

FERTOOL_CARDS = {
    "amh": {
        "title": "Your AMH Explained",
        "description": "Interactive normogram — see where your AMH sits for your age",
        "url": "https://fouksir.github.io/Fertool/amh-guide.html",
        "icon": "\U0001f4ca",
        "embed": True,
        "tags": ["amh", "ovarian reserve", "egg count", "anti-mullerian",
                 "hormone levels", "reserve", "how many eggs", "egg reserve",
                 "low amh", "high amh", "pcos", "amh level"],
    },
    "egg_freezing": {
        "title": "Egg Freezing Outcomes",
        "description": "Success rates based on your age and number of eggs",
        "url": "https://fouksir.github.io/Fertool/egg-freezing-calculator.html",
        "icon": "\u2744\ufe0f",
        "embed": True,
        "tags": ["egg freezing", "freeze", "cryopreservation", "oocyte",
                 "fertility preservation", "social freezing", "how many eggs to freeze",
                 "success rate", "live birth chance", "egg freeze success"],
    },
    "endometriosis": {
        "title": "Endometriosis & Fertility",
        "description": "Understanding how endometriosis affects your fertility",
        "url": "https://fouksir.github.io/Fertool/endometriosis-landing.html",
        "icon": "\U0001f52c",
        "embed": True,
        "tags": ["endometriosis", "endo", "pain", "adenomyosis", "chocolate cyst",
                 "endometrioma", "endo and fertility", "stage 3", "stage 4", "deep endo"],
    },
    "fertility_assessment": {
        "title": "Fertility Assessment",
        "description": "Interactive assessment to understand your fertility picture",
        "url": "https://fouksir.github.io/Fertool/fertility-assessment.html",
        "icon": "\U0001f4cb",
        "embed": True,
        "tags": ["assessment", "fertility check", "workup", "testing", "evaluation",
                 "what tests do i need", "investigation", "blood test", "scan"],
    },
    "fertool_search": {
        "title": "Search Fertool Knowledge Base",
        "description": "Search our clinical fertility database for detailed information",
        "url": "https://fouksir.github.io/Fertool/index.html",
        "icon": "\U0001f50d",
        "embed": False,
        "tags": ["fertool", "search", "lookup"],
    },
}


def match_fertool_cards(message: str, response_text: str, max_cards: int = 2) -> list[dict]:
    """Match patient message + AI response against Fertool card tags.

    Returns up to max_cards matching cards, sorted by relevance.
    Uses partial word matching for broader coverage.
    Only call this for triage category 2 (education).
    """
    combined = (message + " " + response_text).lower()
    scored = []
    for key, card in FERTOOL_CARDS.items():
        hits = 0
        for tag in card["tags"]:
            # Exact phrase match
            if tag in combined:
                hits += 2
            else:
                # Partial word matching — each word in tag checked individually
                tag_words = tag.split()
                partial_hits = sum(1 for w in tag_words if len(w) >= 3 and w in combined)
                if partial_hits > 0:
                    hits += partial_hits
        if hits > 0:
            scored.append((hits, key, card))

    scored.sort(key=lambda x: -x[0])
    return [
        {
            "key": k,
            "title": c["title"],
            "description": c["description"],
            "url": c["url"],
            "icon": c["icon"],
            "embed": c.get("embed", False),
        }
        for _, k, c in scored[:max_cards]
    ]


# ── In-Memory Patient Store (Phase 1 — will move to PostgreSQL) ──────

patients_db: dict = {}
conversations_db: dict = {}  # patient_id -> list of messages
checkins_db: dict = {}       # patient_id -> list of daily check-ins
screenings_db: dict = {}     # patient_id -> list of screening results
escalations_db: dict = {}    # patient_id -> list of escalation events
passive_signals_db: dict = {}  # patient_id -> list of passive behavioural signals


# ── Firebase sync helpers ────────────────────────────────────────
def _sync_conversation(patient_id: str, msg: dict):
    """Append to in-memory + Firebase."""
    conversations_db.setdefault(patient_id, []).append(msg)
    firebase_db.append_conversation(patient_id, msg)

def _sync_checkin(patient_id: str, checkin: dict):
    checkins_db.setdefault(patient_id, []).append(checkin)
    firebase_db.append_checkin(patient_id, checkin)

def _sync_escalation(patient_id: str, escalation: dict):
    escalations_db.setdefault(patient_id, []).append(escalation)
    firebase_db.append_escalation(patient_id, escalation)

def _sync_screening(patient_id: str, screening: dict):
    screenings_db.setdefault(patient_id, []).append(screening)
    firebase_db.append_screening(patient_id, screening)


def _update_engagement(patient_id: str):
    """Update consecutive day tracking for engagement rhythm."""
    patient = get_or_create_patient(patient_id)
    today = date.today().isoformat()

    if "engagement" not in patient:
        patient["engagement"] = {
            "consecutive_days": 1,
            "longest_streak": 1,
            "total_interactions": 1,
            "last_interaction_date": today,
            "gap_acknowledged": False,
        }
    else:
        eng = patient["engagement"]
        last_date = eng.get("last_interaction_date", "")
        eng["total_interactions"] = eng.get("total_interactions", 0) + 1

        if last_date == today:
            return  # Already counted today

        try:
            last_dt = date.fromisoformat(last_date)
            today_dt = date.fromisoformat(today)
            gap = (today_dt - last_dt).days
        except (ValueError, TypeError):
            gap = 999

        if gap == 1:
            eng["consecutive_days"] = eng.get("consecutive_days", 0) + 1
            eng["gap_acknowledged"] = False
        elif gap > 1:
            eng["consecutive_days"] = 1
            eng["gap_acknowledged"] = False
        # else gap == 0 already handled

        eng["last_interaction_date"] = today
        if eng["consecutive_days"] > eng.get("longest_streak", 0):
            eng["longest_streak"] = eng["consecutive_days"]

    firebase_db.save_patient(patient_id, patient)


def _sync_passive_signal(patient_id: str, record: dict):
    passive_signals_db.setdefault(patient_id, []).append(record)

def _sync_passive_batch(patient_id: str, records: list):
    """Sync a batch of passive signals to Firebase."""
    firebase_db.append_passive_signals(patient_id, records)


def get_or_create_patient(patient_id: str) -> dict:
    if patient_id not in patients_db:
        patients_db[patient_id] = {
            "patient_id": patient_id,
            "name": None,
            "treatment_stage": "initial_workup",
            "cycle_number": 1,
            "stage_start_date": utc_iso(),
            "partner_name": None,
            "clinic_name": None,
            "preferences": {
                "check_in_time": "20:00",
                "tone": "gentle",
            },
            "created_at": utc_iso(),
            "last_active": utc_iso(),
        }
        conversations_db[patient_id] = []
        checkins_db[patient_id] = []
        screenings_db[patient_id] = []
        escalations_db[patient_id] = []
        passive_signals_db[patient_id] = []
    patients_db[patient_id]["last_active"] = utc_iso()
    firebase_db.save_patient(patient_id, patients_db[patient_id])
    return patients_db[patient_id]


def get_conversation_context(patient_id: str, last_n: int = 20) -> list:
    """Get recent conversation for context window."""
    return conversations_db.get(patient_id, [])[-last_n:]


def get_recent_checkins(patient_id: str, last_n: int = 7) -> list:
    """Get recent daily check-ins."""
    return checkins_db.get(patient_id, [])[-last_n:]


def classify_patient_style(patient_id: str) -> str:
    """
    Classify patient communication style based on conversation history and check-in data.
    Returns one of: ANALYTICAL, EMOTIONAL, MIXED
    """
    # Get last 10 messages from conversation
    conv = conversations_db.get(patient_id, [])
    last_messages = [m for m in conv if m.get("role") == "user"][-10:]

    if not last_messages:
        return "MIXED"  # Default if no conversation history

    # Medical/analytical terms to look for
    medical_terms = {
        "amh", "fsh", "lh", "estradiol", "progesterone", "embryo", "protocol",
        "percentage", "success rate", "prognosis", "fertilization", "implantation",
        "blastocyst", "stimulation", "downregulation", "statistics", "data",
        "study", "research", "why", "how", "mechanism", "reason", "explain"
    }

    # Emotional words to look for
    emotion_words = {
        "feel", "scared", "worried", "hope", "anxious", "lonely", "sad",
        "overwhelmed", "stressed", "upset", "cry", "crying", "devastated",
        "heartbroken", "grateful", "grateful", "love", "miss", "excited",
        "nervous", "terrified", "helpless", "depressed"
    }

    analytical_score = 0
    emotional_score = 0

    # Analyze last 10 messages
    full_text = " ".join([m.get("content", "").lower() for m in last_messages])

    # Count question marks as analytical indicator
    analytical_score += full_text.count("?") * 2

    # Count medical terms
    for term in medical_terms:
        analytical_score += full_text.count(term) * 3

    # Count emotion words
    for word in emotion_words:
        emotional_score += full_text.count(word) * 3

    # Message length analysis (longer messages often more analytical)
    avg_msg_length = sum(len(m.get("content", "")) for m in last_messages) / len(last_messages) if last_messages else 0
    if avg_msg_length > 150:
        analytical_score += 10
    elif avg_msg_length < 50:
        emotional_score += 10

    # Classify based on ratio
    if analytical_score > emotional_score * 1.5:
        style = "ANALYTICAL"
    elif emotional_score > analytical_score * 1.5:
        style = "EMOTIONAL"
    else:
        style = "MIXED"

    # Store in patient record
    patient = get_or_create_patient(patient_id)
    patient["communication_style"] = style

    return style


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

    # Add communication style guidance
    style = classify_patient_style(patient_id)
    ctx += f"\nPATIENT COMMUNICATION STYLE: {style}\n"
    if style == "ANALYTICAL":
        ctx += """- ANALYTICAL: This patient responds best to data, statistics, and evidence. Use phrases like "In women with similar AMH levels, X% experience this..." or "Studies show..." Give concrete numbers when possible. They appreciate thoroughness.
"""
    elif style == "EMOTIONAL":
        ctx += """- EMOTIONAL: This patient needs warmth and connection first. Use phrases like "Many people feel exactly like this at this stage..." or "You're not alone in this." Validate before informing. Share community experiences.
"""
    else:  # MIXED
        ctx += """- MIXED: Balance both approaches — acknowledge feelings, then provide the data they need.
"""

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

    # Determine escalation level
    level = "GREEN"
    if triggers:
        # Check for RED-level indicators (high-risk patient patterns)
        critical_triggers = [
            t for t in triggers
            if any(x in t for x in ["Mood ≤ 2", "Hope at minimum", "Anxiety ≥ 8", "Disengagement"])
        ]

        # RED if 2+ critical triggers or persistent crisis pattern
        if len(critical_triggers) >= 2 or all(c["mood"] <= 1 for c in recent):
            level = "RED"
        else:
            level = "AMBER"

    return {"level": level, "triggers": triggers}


def build_preconsult_briefing(patient_id: str) -> dict:
    """
    Build a pre-consultation briefing for clinicians using Claude Haiku.
    Returns structured briefing with communication style, concerns, stress level, and suggested approach.
    """
    patient = get_or_create_patient(patient_id)
    conv = conversations_db.get(patient_id, [])
    checkins = get_recent_checkins(patient_id)

    # Get communication style
    style = classify_patient_style(patient_id)

    # Assess stress level from recent check-ins
    if checkins:
        recent_anxiety = [c["anxiety"] for c in checkins[-3:]]
        avg_anxiety = sum(recent_anxiety) / len(recent_anxiety) if recent_anxiety else 5

        if avg_anxiety >= 8:
            stress_level = "CRITICAL"
        elif avg_anxiety >= 7:
            stress_level = "HIGH"
        elif avg_anxiety >= 5:
            stress_level = "MODERATE"
        else:
            stress_level = "LOW"
    else:
        stress_level = "MODERATE"

    # Get recent mood trend
    if len(checkins) >= 3:
        recent_moods = [c["mood"] for c in checkins[-3:]]
        if recent_moods[-1] > recent_moods[0]:
            mood_trend = "improving"
        elif recent_moods[-1] < recent_moods[0]:
            mood_trend = "declining"
        else:
            mood_trend = "stable"
    else:
        mood_trend = "stable"

    # Extract key topics and concerns using Haiku
    recent_msgs = [m for m in conv if m.get("role") == "user"][-5:]
    recent_text = " ".join([m.get("content", "") for m in recent_msgs])

    # Identify risk flags
    risk_flags = []
    if checkins:
        mood_low = sum(1 for c in checkins[-7:] if c["mood"] <= 3) if len(checkins) >= 7 else 0
        if mood_low >= 3:
            risk_flags.append("Persistent low mood")

    # Check for disengagement
    daily_esc = check_daily_escalation(patient_id)
    if "Social withdrawal" in str(daily_esc.get("triggers", [])) or "Disengagement" in str(daily_esc.get("triggers", [])):
        risk_flags.append("Social withdrawal detected")

    # Build the briefing
    briefing = {
        "patient_id": patient_id,
        "patient_name": patient.get("name", "Unknown"),
        "communication_style": style,
        "stress_level": stress_level,
        "main_concerns": [],
        "suggested_approach": "",
        "patient_expectations_prompt": "Ask what she hopes to get from the next appointment",
        "recent_mood_trend": mood_trend,
        "key_topics_discussed": [],
        "risk_flags": risk_flags,
        "treatment_stage": STAGE_DISPLAY.get(patient.get("treatment_stage", "consultation"), patient.get("treatment_stage", "consultation")),
        "cycle_number": patient.get("cycle_number", 1),
    }

    # If we have recent messages, ask Haiku to generate insights
    if recent_msgs:
        haiku_prompt = f"""Based on this recent patient conversation excerpt, identify:
1. The top 2-3 main concerns/worries the patient has mentioned
2. A brief suggested clinician approach based on the patient's communication style ({style})
3. Key medical topics they've discussed (e.g., embryo grading, medications, timing)

Recent messages:
{recent_text}

Communication style: {style}
Current stress level: {stress_level}

Please respond in JSON format:
{{
  "main_concerns": ["concern1", "concern2"],
  "suggested_approach": "Brief suggestion for how clinician should approach this patient",
  "key_topics": ["topic1", "topic2"]
}}"""

        try:
            haiku_resp = client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=300,
                messages=[{"role": "user", "content": haiku_prompt}]
            )

            resp_text = haiku_resp.content[0].text
            if "{" in resp_text:
                json_str = resp_text[resp_text.index("{"):resp_text.rindex("}") + 1]
                haiku_data = json.loads(json_str)
                briefing["main_concerns"] = haiku_data.get("main_concerns", [])
                briefing["suggested_approach"] = haiku_data.get("suggested_approach", "")
                briefing["key_topics_discussed"] = haiku_data.get("key_topics", [])
        except Exception as e:
            logger.warning(f"Error generating Haiku briefing: {e}")
            briefing["suggested_approach"] = f"Patient is {style} style. Stress level: {stress_level}. Mood trend: {mood_trend}."

    return briefing


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
    fertool_cards: Optional[list] = None
    one_word_checkin: Optional[dict] = None  # If message was mapped as a one-word check-in
    education_fork: Optional[str] = None  # Clarifying question for education queries
    capability_hint: Optional[str] = None  # Contextual capability discovery hint
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
    capability_hint: Optional[str] = None

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
    signals: list[dict] = []  # Each: {signal_type, value, timestamp, metadata}
    derived_features: Optional[dict] = None
    session_metadata: Optional[dict] = None
    session_id: Optional[str] = None

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
    # Load persistent data from Firebase into in-memory cache
    try:
        loaded = firebase_db.load_all_into_memory(
            patients_db, conversations_db, checkins_db,
            screenings_db, escalations_db, passive_signals_db
        )
        logger.info(f"Firebase: restored {loaded} patients from persistent storage")
    except Exception as e:
        logger.warning(f"Firebase load failed (non-fatal): {e}")
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
    allow_origins=[
        "https://fouksir.github.io",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"https://.*\.run\.app",  # Cloud Run preview URLs
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
        "timestamp": utc_iso(),
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
    patient["onboard_soft_spot_shown"] = req.treatment_stage  # Track which soft spot was shown during onboarding

    # Generate contextual welcome message using smart greeting + LLM
    stage_name = STAGE_DISPLAY.get(req.treatment_stage, req.treatment_stage)

    # Build smart greeting context
    smart_context = build_smart_greeting(patient_id)
    soft_spot = get_soft_spot_context(patient_id)

    welcome_prompt = f"""Generate a warm welcome message for {req.name} who is just starting to use IVF Companion.
They are currently at the '{stage_name}' stage of their IVF journey (cycle {req.cycle_number}).
{"Their partner's name is " + req.partner_name + ". " if req.partner_name else ""}
{"They're being treated at " + req.clinic_name + ". " if req.clinic_name else ""}

Use this contextual opening as inspiration (adapt naturally, don't copy verbatim):
{smart_context}

{"SOFT SPOT: " + soft_spot['message'] if soft_spot else ""}

Introduce yourself as Melod-AI. Be warm, brief (3-4 sentences), and let them know you're here.
Mention what you can help with (emotional support, education, daily check-ins) without overwhelming.
End with one gentle question to start the conversation.
Do NOT be generic — show you understand where they are in their journey."""

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

    # Store patient to Firebase
    firebase_db.save_patient(patient_id, patient)

    # Store in conversation history
    _sync_conversation(patient_id, {
        "role": "assistant",
        "content": welcome_msg,
        "timestamp": utc_iso(),
        "type": "welcome",
    })

    return {
        "patient_id": patient_id,
        "message": welcome_msg,
        "treatment_stage": req.treatment_stage,
        "stage_display": stage_name,
    }


@app.get("/daily-insight/{patient_id}")
async def get_daily_insight(patient_id: str):
    """Generate a fresh, personal daily observation based on last 24-48h data."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = patients_db[patient_id]
    today = date.today().isoformat()

    # Check cache — one insight per day
    daily_insights = patient.get("daily_insights", {})
    if today in daily_insights:
        return {"insight": daily_insights[today], "cached": True, "date": today}

    # Gather last 48h data
    name = patient.get("name", "there")
    stage = patient.get("treatment_stage", "consultation")
    stage_name = STAGE_DISPLAY.get(stage, stage)

    checkins = get_recent_checkins(patient_id, last_n=3)
    last_checkin = checkins[-1] if checkins else None

    conv = conversations_db.get(patient_id, [])
    recent_msgs = [m for m in conv[-20:] if m.get("timestamp")]

    # Time since last session
    last_session_time = None
    for m in reversed(recent_msgs):
        try:
            last_session_time = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
            break
        except (ValueError, TypeError):
            pass

    # Build data summary for Claude
    data_points = []
    if last_checkin:
        data_points.append(f"Last check-in: mood={last_checkin.get('mood')}, anxiety={last_checkin.get('anxiety')}, hope={last_checkin.get('hope')}, loneliness={last_checkin.get('loneliness')}")
    if last_session_time:
        hour = last_session_time.hour
        if hour >= 22 or hour < 5:
            data_points.append(f"Last session was late at night ({last_session_time.strftime('%I:%M %p')})")
        days_since = (utc_now() - last_session_time).days
        if days_since >= 2:
            data_points.append(f"Haven't checked in for {days_since} days")

    # Recent user messages — extract topics
    user_msgs = [m["content"] for m in recent_msgs if m.get("role") == "user"][-5:]
    if user_msgs:
        data_points.append(f"Recent topics discussed: {'; '.join(user_msgs[-3:])}")

    # Stage and day info
    stage_start = patient.get("stage_start_date")
    days_in_stage = None
    if stage_start:
        try:
            start_dt = datetime.fromisoformat(stage_start.replace("Z", "+00:00"))
            days_in_stage = (datetime.now() - start_dt.replace(tzinfo=None)).days
            data_points.append(f"Day {days_in_stage} of {stage_name}")
        except (ValueError, TypeError):
            pass

    # Upcoming soft spots
    soft_spot = get_soft_spot_context(patient_id)
    if soft_spot:
        data_points.append(f"Upcoming emotional moment: {soft_spot.get('message', '')[:80]}")

    # Checkin trends
    if len(checkins) >= 2:
        moods = [c.get("mood", 5) for c in checkins]
        if moods[-1] > moods[-2] + 1:
            data_points.append("Mood jumped up recently")
        elif moods[-1] < moods[-2] - 1:
            data_points.append("Mood dropped recently")
        hopes = [c.get("hope", 5) for c in checkins]
        if hopes[-1] > hopes[-2] + 1:
            data_points.append("Hope score increased")

    data_summary = "\n- ".join(data_points) if data_points else "No recent data available"

    prompt = f"""Write ONE sentence — warm, specific, observational — based on this patient's data from the last 24-48 hours. This should feel like a friend who noticed something. Never generic. Never clinical. Never longer than 2 sentences.

Patient name: {name}
Current stage: {stage_name}
Data:
- {data_summary}

Examples of GOOD daily insights:
- "You were up past midnight last night. I hope today is gentler."
- "Your hope score jumped yesterday — something shifted. Hold onto that."
- "You asked three questions about embryo grading yesterday. Sounds like the scientist in you is processing."
- "You haven't checked in since Tuesday. That's okay. I'm still here."
- "Today is day 8 of your wait. More than halfway. You're doing this."
- "Last night you told me you felt alone. I thought about that. You're carrying a lot right now."

Examples of BAD insights (NEVER do these):
- "Great job checking in yesterday!" (patronizing)
- "Your mood was 6.2 yesterday" (clinical numbers)
- "Remember to stay positive!" (toxic positivity)
- "How are you today?" (generic, not an insight)

Write ONLY the insight text. No quotes, no labels, no preamble."""

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        insight = response.content[0].text.strip().strip('"')
    except Exception as e:
        logger.warning(f"Daily insight generation failed: {e}")
        # Fallback insight based on available data
        if last_session_time and (last_session_time.hour >= 22 or last_session_time.hour < 5):
            insight = f"You were up late last night, {name}. I hope today is gentler."
        elif days_in_stage is not None:
            insight = f"Day {days_in_stage} of {stage_name}. You're still here. That matters."
        elif last_checkin and last_checkin.get("mood", 5) <= 3:
            insight = "Yesterday was heavy. Today is a new page."
        else:
            insight = f"I'm here whenever you need me, {name}."

    # Cache in patient record and Firebase
    if "daily_insights" not in patient:
        patient["daily_insights"] = {}
    patient["daily_insights"][today] = insight
    # Keep only last 7 days
    keys = sorted(patient["daily_insights"].keys())
    if len(keys) > 7:
        for old_key in keys[:-7]:
            del patient["daily_insights"][old_key]
    firebase_db.save_patient(patient_id, patient)

    return {"insight": insight, "cached": False, "date": today}


@app.get("/evening-prompt/{patient_id}")
async def get_evening_prompt(patient_id: str):
    """Return an evening wind-down prompt if the patient was active today."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = patients_db[patient_id]
    stage = patient.get("treatment_stage", "consultation")
    today = date.today().isoformat()

    # Check if patient was active today
    conv = conversations_db.get(patient_id, [])
    checkins = checkins_db.get(patient_id, [])
    active_today = any(
        m.get("timestamp", "")[:10] == today for m in conv
    ) or any(
        c.get("date", "")[:10] == today for c in checkins
    )

    if not active_today:
        return {"prompt": None, "reason": "not_active_today"}

    # Check if already shown today
    evening = patient.get("evening_prompts", {})
    if evening.get("last_shown") == today:
        return {"prompt": None, "reason": "already_shown"}

    # Pick prompt
    prompt = EVENING_PROMPTS.get(stage, random.choice(EVENING_GENERIC))

    # Mark as shown
    if "evening_prompts" not in patient:
        patient["evening_prompts"] = {}
    patient["evening_prompts"]["last_shown"] = today
    firebase_db.save_patient(patient_id, patient)

    return {"prompt": prompt, "stage": stage, "date": today}


@app.get("/greeting/{patient_id}")
async def get_smart_greeting(patient_id: str):
    """Return a contextual greeting for a returning user opening the app."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    greeting = build_smart_greeting(patient_id)
    soft_spot = get_soft_spot_context(patient_id)

    # Generate micro-reflection if triggered
    micro = generate_micro_reflection(patient_id)

    # Check if full reflection is due
    needs_reflection = _should_generate_reflection(patient_id)

    # Check for capability discovery hint
    capability_hint = get_capability_discovery(patient_id)

    # Check engagement streak for gap acknowledgment
    patient = patients_db.get(patient_id, {})
    engagement = patient.get("engagement", {})
    gap_msg = None
    if engagement:
        last_date = engagement.get("last_interaction_date", "")
        try:
            last_dt = date.fromisoformat(last_date)
            gap = (date.today() - last_dt).days
            if gap >= 3 and not engagement.get("gap_acknowledged"):
                gap_msg = "Welcome back. No guilt — sometimes you need space. I kept your spot."
                engagement["gap_acknowledged"] = True
                firebase_db.save_patient(patient_id, patient)
        except (ValueError, TypeError):
            pass

    return {
        "greeting": greeting,
        "soft_spot": soft_spot,
        "micro_reflection": micro,
        "needs_reflection": needs_reflection,
        "capability_hint": capability_hint,
        "gap_message": gap_msg,
        "streak": engagement.get("consecutive_days", 0) if engagement else 0,
        "patient_id": patient_id,
        "timestamp": utc_iso(),
    }


# ── Reflections ─────────────────────────────────────────────────────
reflections_db: dict = {}  # patient_id -> list of reflection dicts


def _get_landscape_zone(stage: str) -> str:
    """Map treatment stage to landscape zone name."""
    zone_map = {
        "consultation": "the_meadow", "investigation": "the_meadow", "waiting_to_start": "the_meadow",
        "downregulation": "the_climb", "stimulation": "the_climb", "monitoring": "the_climb", "trigger": "the_climb",
        "before_retrieval": "the_peak", "retrieval_day": "the_peak", "post_retrieval": "the_peak",
        "fertilisation_report": "the_bridge", "embryo_development": "the_bridge", "freeze_all": "the_bridge",
        "before_transfer": "the_valley", "transfer_day": "the_valley", "early_tww": "the_valley", "late_tww": "the_valley",
        "result_day": "the_fork",
        "positive_result": "the_garden", "early_pregnancy": "the_garden",
        "negative_result": "the_lake", "chemical_pregnancy": "the_lake", "miscarriage": "the_lake",
        "failed_cycle_acute": "the_lake", "failed_cycle_processing": "the_lake",
        "wtf_appointment": "the_lake", "between_cycles": "the_lake",
        "considering_stopping": "the_lake", "donor_journey": "the_lake",
    }
    return zone_map.get(stage, "the_meadow")


LANDSCAPE_ZONE_DISPLAY = {
    "the_meadow": "The Quiet Meadow",
    "the_climb": "The Climb",
    "the_peak": "The Peak",
    "the_bridge": "The Bridge",
    "the_valley": "The Valley of Waiting",
    "the_fork": "The Fork",
    "the_garden": "The Garden",
    "the_lake": "The Quiet Lake",
}


def _compute_mood_trend(checkins: list) -> str:
    """Compute mood trend from recent check-ins."""
    if len(checkins) < 2:
        return "stable"
    recent = checkins[-4:]
    moods = [c.get("mood", 5) for c in recent]
    if len(moods) < 2:
        return "stable"
    first_half = sum(moods[:len(moods)//2]) / max(len(moods)//2, 1)
    second_half = sum(moods[len(moods)//2:]) / max(len(moods) - len(moods)//2, 1)
    diff = second_half - first_half
    if diff > 1:
        return "improving"
    elif diff < -1:
        return "declining"
    return "stable"


def _should_generate_reflection(patient_id: str) -> bool:
    """Check if enough time has passed since last full reflection (3+ days)."""
    refs = reflections_db.get(patient_id, [])
    full_refs = [r for r in refs if r.get("type") == "full"]
    if not full_refs:
        return True
    last = full_refs[-1]
    try:
        last_ts = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
        days_since = (utc_now() - last_ts).days
        return days_since >= 3
    except (ValueError, TypeError, KeyError):
        return True


def generate_micro_reflection(patient_id: str) -> Optional[str]:
    """Generate a micro-reflection if triggered by recent patterns."""
    patient = get_or_create_patient(patient_id)
    refs = reflections_db.get(patient_id, [])
    recent_micros = [r for r in refs if r.get("type") == "micro"]

    # Check what was recently shown (avoid repeating within 48h)
    recent_triggers = set()
    for r in recent_micros[-10:]:
        try:
            ts = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            if (utc_now() - ts).total_seconds() < 48 * 3600:
                for t in r.get("triggers", []):
                    recent_triggers.add(t)
        except (ValueError, TypeError, KeyError):
            pass

    micro = None
    trigger = None

    # Check: patient came back after 2+ day gap
    conv = conversations_db.get(patient_id, [])
    last_user = None
    for m in reversed(conv):
        if m.get("role") == "user":
            last_user = m
            break
    if last_user and last_user.get("timestamp") and "return_after_gap" not in recent_triggers:
        try:
            last_ts = datetime.fromisoformat(last_user["timestamp"].replace("Z", "+00:00"))
            gap = (utc_now() - last_ts).days
            if gap >= 2:
                micro = "You've been away for a bit. Welcome back."
                trigger = "return_after_gap"
        except (ValueError, TypeError):
            pass

    # Check: 3 consecutive low mood check-ins
    checkins = checkins_db.get(patient_id, [])
    if not micro and len(checkins) >= 3 and "consecutive_low_mood" not in recent_triggers:
        last3 = checkins[-3:]
        if all(c.get("mood", 5) <= 3 for c in last3):
            micro = "It's been a tough stretch. I see that."
            trigger = "consecutive_low_mood"

    # Check: significant mood improvement
    if not micro and len(checkins) >= 2 and "mood_improved" not in recent_triggers:
        prev = checkins[-2].get("mood", 5)
        curr = checkins[-1].get("mood", 5)
        if curr - prev >= 3:
            micro = "Something feels different today — in a good way."
            trigger = "mood_improved"

    # Check: late night session (3rd time this week)
    if not micro and "late_night_pattern" not in recent_triggers:
        now = utc_now()
        week_ago = now - timedelta(days=7)
        late_sessions = 0
        for m in conv:
            try:
                ts = datetime.fromisoformat(m.get("timestamp", "").replace("Z", "+00:00"))
                if ts > week_ago and (ts.hour >= 23 or ts.hour < 5):
                    late_sessions += 1
            except (ValueError, TypeError):
                pass
        if late_sessions >= 3 and (now.hour >= 23 or now.hour < 5):
            micro = "Another late night. Your sleep matters too."
            trigger = "late_night_pattern"

    # Check: new treatment stage
    if not micro and "new_stage" not in recent_triggers:
        stage_start = patient.get("stage_start_date")
        if stage_start:
            try:
                start_dt = datetime.fromisoformat(stage_start.replace("Z", "+00:00"))
                if (utc_now() - start_dt).days <= 1:
                    micro = "New territory. How does it feel to be here?"
                    trigger = "new_stage"
            except (ValueError, TypeError):
                pass

    if micro and trigger:
        # Store micro-reflection
        ref_data = {
            "type": "micro",
            "text": micro,
            "period_start": utc_iso(),
            "period_end": utc_iso(),
            "mood_trend": _compute_mood_trend(checkins),
            "landscape_zone": _get_landscape_zone(patient.get("treatment_stage", "")),
            "triggers": [trigger],
            "feedback": None,
            "created_at": utc_iso(),
        }
        reflections_db.setdefault(patient_id, []).append(ref_data)
        firebase_db.save_reflection(patient_id, ref_data)
        return micro

    return None


@app.get("/reflection/{patient_id}")
async def get_reflection(patient_id: str):
    """Generate or return cached personal reflection for a patient."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = patients_db[patient_id]
    checkins = checkins_db.get(patient_id, [])
    conv = conversations_db.get(patient_id, [])

    # Check if reflection is needed
    if not _should_generate_reflection(patient_id):
        # Return most recent full reflection
        refs = reflections_db.get(patient_id, [])
        full_refs = [r for r in refs if r.get("type") == "full"]
        if full_refs:
            return full_refs[-1]
        # Fall through to generate

    # Gather data from last 4 days
    cutoff = utc_now() - timedelta(days=4)

    recent_checkins = []
    for c in checkins:
        try:
            ts = datetime.fromisoformat(c.get("date", "").replace("Z", "+00:00"))
            if ts >= cutoff:
                recent_checkins.append(c)
        except (ValueError, TypeError):
            pass

    recent_convos = []
    triage_counts = {"emotional": 0, "education": 0, "screening": 0, "social": 0}
    for m in conv:
        try:
            ts = datetime.fromisoformat(m.get("timestamp", "").replace("Z", "+00:00"))
            if ts >= cutoff and m.get("role") == "user":
                recent_convos.append(m)
                tc = m.get("triage")
                if tc == 1: triage_counts["emotional"] += 1
                elif tc == 2: triage_counts["education"] += 1
                elif tc == 3: triage_counts["screening"] += 1
                elif tc == 5: triage_counts["social"] += 1
        except (ValueError, TypeError):
            pass

    # Build data summary for the LLM
    stage = patient.get("treatment_stage", "consultation")
    stage_name = STAGE_DISPLAY.get(stage, stage)
    mood_trend = _compute_mood_trend(checkins)
    zone = _get_landscape_zone(stage)

    # Check-in summary
    checkin_summary = ""
    if recent_checkins:
        moods = [c.get("mood", 5) for c in recent_checkins]
        anxieties = [c.get("anxiety", 5) for c in recent_checkins]
        hopes = [c.get("hope", 5) for c in recent_checkins]
        loneliness = [c.get("loneliness", 5) for c in recent_checkins]
        # One-word check-ins
        one_words = [c.get("word", "") for c in recent_checkins if c.get("source") == "one_word"]
        checkin_summary = f"""Check-ins ({len(recent_checkins)} in last 4 days):
Mood range: {min(moods)}-{max(moods)} (trend: {mood_trend})
Anxiety range: {min(anxieties)}-{max(anxieties)}
Hope range: {min(hopes)}-{max(hopes)}
Loneliness range: {min(loneliness)}-{max(loneliness)}
{f'One-word feelings shared: {", ".join(one_words)}' if one_words else ''}"""
    else:
        checkin_summary = "No check-ins in the last 4 days."

    # Conversation summary
    conv_summary = f"Conversations: {len(recent_convos)} messages in last 4 days."
    if triage_counts["emotional"] > 0:
        conv_summary += f" {triage_counts['emotional']} emotional."
    if triage_counts["education"] > 0:
        conv_summary += f" {triage_counts['education']} education questions."

    # Late night pattern
    late_count = sum(1 for m in recent_convos
                     if m.get("timestamp") and
                     (int(m["timestamp"].split("T")[1][:2]) >= 23 or
                      int(m["timestamp"].split("T")[1][:2]) < 5) if "T" in m.get("timestamp", ""))

    # Soft spot
    soft_spot = get_soft_spot_context(patient_id)
    soft_spot_note = f"They are at a known difficulty point: {soft_spot['message']}" if soft_spot else ""

    reflection_prompt = f"""You are Melod-AI writing a warm, personal reflection for an IVF patient
covering the last few days. Based on this data, write 2-3 sentences that
feel like a caring friend who's been paying attention. Reference specific
patterns you notice. Never clinical language. Never use numbers directly —
translate data into human feelings. Keep it short.

Patient: {patient.get('name', 'the patient')}
Treatment stage: {stage_name}
Landscape zone: {LANDSCAPE_ZONE_DISPLAY.get(zone, zone)}
{checkin_summary}
{conv_summary}
{'Late night sessions: ' + str(late_count) if late_count > 0 else ''}
Mood trend: {mood_trend}
{soft_spot_note}

Match the reflection to what's happening:
- During stimulation: focus on how their body and emotions are tracking
- During TWW: acknowledge the specific agony of waiting
- After a result: be present, don't silver-line
- Between cycles: honour the processing, don't rush them forward

Write ONLY the reflection text, nothing else. 2-3 sentences max."""

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": reflection_prompt}],
        )
        reflection_text = response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Reflection generation error: {e}")
        reflection_text = "I've been here with you through the last few days. Whatever you're carrying, you don't have to carry it alone."

    # Detect key moment
    key_moment = None
    if soft_spot:
        key_moment = soft_spot.get("stage")
    elif mood_trend == "declining":
        key_moment = "mood_decline"
    elif mood_trend == "improving":
        key_moment = "mood_recovery"

    ref_data = {
        "type": "full",
        "text": reflection_text,
        "period_start": (utc_now() - timedelta(days=4)).isoformat(),
        "period_end": utc_iso(),
        "mood_trend": mood_trend,
        "anxiety_trend": "elevated" if recent_checkins and max(c.get("anxiety", 5) for c in recent_checkins) >= 7 else "normal",
        "key_moment": key_moment,
        "landscape_zone": zone,
        "zone_display": LANDSCAPE_ZONE_DISPLAY.get(zone, zone),
        "triggers": [],
        "feedback": None,
        "created_at": utc_iso(),
    }

    reflections_db.setdefault(patient_id, []).append(ref_data)
    firebase_db.save_reflection(patient_id, ref_data)

    return ref_data


@app.post("/reflection/{patient_id}/feedback")
async def reflection_feedback(patient_id: str, request: Request):
    """Record feedback on a reflection (heart = resonated)."""
    body = await request.json()
    feedback_type = body.get("feedback", "resonated")

    refs = reflections_db.get(patient_id, [])
    if refs:
        refs[-1]["feedback"] = feedback_type
        firebase_db.save_reflection(patient_id, refs[-1])

    return {"status": "ok"}


@app.get("/reflections/{patient_id}")
async def list_reflections(patient_id: str, limit: int = 10):
    """List recent reflections for landscape integration."""
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    refs = reflections_db.get(patient_id, [])[-limit:]
    return {"reflections": refs, "patient_id": patient_id}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint — triage → layers → synthesis → safety check."""
    patient = get_or_create_patient(req.patient_id)
    query_id = str(uuid.uuid4())[:12]

    # Store user message
    _sync_conversation(req.patient_id, {
        "role": "user",
        "content": req.message,
        "timestamp": utc_iso(),
    })

    # Update engagement tracking
    _update_engagement(req.patient_id)

    # ── One-word check-in detection (Part C) ──
    one_word_checkin = None
    msg_words = req.message.strip().split()
    if len(msg_words) <= 3:
        mapped = map_one_word_to_checkin(req.message)
        if mapped:
            one_word_checkin = mapped
            # Store as a lightweight check-in
            checkin_data = {
                "date": utc_iso(),
                "source": "one_word",
                "word": req.message.strip(),
                **mapped,
            }
            _sync_checkin(req.patient_id, checkin_data)
            logger.info(f"[{query_id}] One-word check-in: '{req.message.strip()}' → {mapped}")

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
            "timestamp": utc_iso(),
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
                        "timestamp": utc_iso(),
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
            "timestamp": utc_iso(),
        }

    # Store escalation if triggered
    if escalation and escalation.get("level") != "GREEN":
        _sync_escalation(req.patient_id, escalation)
        logger.warning(f"[{query_id}] ESCALATION: {escalation['level']} for patient={req.patient_id}")

    # ── Education fork with intent detection ──
    education_fork = None
    edu_intent = None
    style = classify_patient_style(req.patient_id)

    if triage_category == 2:
        conv_history = conversations_db.get(req.patient_id, [])
        edu_intent = detect_education_intent(req.message, style, conv_history)

        if edu_intent["intent"] is None and edu_intent.get("matched_topic"):
            topic_name = COMMON_IVF_TOPICS[edu_intent["matched_topic"]]["name"]
            education_fork = f"I can help with {topic_name} — would you like reassurance that things are okay, the clinical details, or practical tips for what to do?"
        elif edu_intent["intent"] is None and style == "MIXED":
            user_msg_count = len([m for m in conv_history if m.get("role") == "user"])
            if user_msg_count <= 5:
                education_fork = "I can help with that — are you looking for reassurance, or do you want the clinical details?"

    # ── Step 3: Generate companion response ──
    # Retrieve education RAG content if this is an education query
    rag_context = ""
    if triage_category == 2:  # Education question
        rag_context = retrieve_education(req.message, patient["treatment_stage"])

    system_prompt = COMPANION_SYSTEM.format(
        patient_context=build_patient_context(req.patient_id),
        education_context=build_education_context(req.patient_id) + rag_context,
    )

    # Conversation continuity — inject recent conversation summaries
    conv_summaries = summarize_last_conversations(req.patient_id, last_n=3)
    if conv_summaries:
        system_prompt += """

CONVERSATION MEMORY (reference naturally when relevant — like a friend who remembers):
""" + "\n".join(f"- {s}" for s in conv_summaries) + """

Don't reference every past conversation. Only bring it up when it adds warmth or continuity.
Never say 'according to my records' or 'I see from your history' — just remember like a human would.
Examples: 'You mentioned feeling alone on Tuesday — has that shifted at all?'
'Last time you were curious about embryo grading. Did you get your report?'"""

    # Add one-word check-in context so AI responds warmly
    if one_word_checkin:
        word = req.message.strip()
        system_prompt += f"""

ONE-WORD CHECK-IN DETECTED:
The patient just said "{word}" as a mood check-in. This maps to:
mood={one_word_checkin['mood']}, anxiety={one_word_checkin['anxiety']}, hope={one_word_checkin['hope']}, loneliness={one_word_checkin['loneliness']}, uncertainty={one_word_checkin['uncertainty']}
Respond with warmth. Acknowledge the word they used. Don't lecture. Don't ask them to rate things on a scale.
If it's a negative word, validate first. If positive, mirror the energy gently.
Keep it brief (2-3 sentences) and end with an open door: something like "Want to tell me more?" or "What's behind that?"
This has been recorded as a check-in — no need to ask them to do a formal one."""

    # Add education intent + topic knowledge to system prompt
    if triage_category == 2 and edu_intent:
        topic_data = COMMON_IVF_TOPICS.get(edu_intent.get("matched_topic", ""), {}) if edu_intent.get("matched_topic") else {}

        if topic_data:
            system_prompt += f"""

TOPIC KNOWLEDGE — {topic_data.get('name', '')}:
Summary: {topic_data.get('summary', '')}
Clinical detail: {topic_data.get('analytical', '')}
Emotional framing: {topic_data.get('emotional', '')}
Practical tips: {topic_data.get('practical', '')}
Use this knowledge to inform your answer. Do NOT dump it all — select what fits the patient's intent."""

        if education_fork:
            system_prompt += """

EDUCATION STYLE FORK:
This patient's communication style is not yet clear. After answering their question briefly,
gently ask if they'd prefer more clinical detail, reassurance, or practical tips going forward.
Weave this naturally into your response — don't make it a separate question."""
        elif edu_intent.get("intent") == "REASSURANCE_FIRST":
            system_prompt += """

EDUCATION INTENT: REASSURANCE_FIRST
Lead with emotional validation — "this is normal", "many women experience this". Then weave in
relevant facts gently. End with something grounding. Do NOT lead with statistics or clinical jargon."""
        elif edu_intent.get("intent") == "EXPLAIN_FIRST":
            system_prompt += """

EDUCATION INTENT: EXPLAIN_FIRST
Lead with clear, accurate clinical information in plain language. Use helpful analogies.
Include relevant numbers if available. End with "your specialist can give you specifics"."""
        elif edu_intent.get("intent") == "PRACTICAL_FIRST":
            system_prompt += """

EDUCATION INTENT: PRACTICAL_FIRST
Lead with concrete tips and what to expect. Tell them what to ask their specialist.
Include timing, preparation, and "what to watch for". Keep it focused and useful."""
    elif triage_category == 2 and style == "ANALYTICAL":
        system_prompt += """

STYLE NOTE: This patient prefers ANALYTICAL responses. Lead with data, statistics,
and clinical detail. Use precise language. They appreciate thoroughness."""
    elif triage_category == 2 and style == "EMOTIONAL":
        system_prompt += """

STYLE NOTE: This patient prefers EMOTIONAL responses. Lead with validation and
shared experience. Weave in the clinical information gently after connecting emotionally."""

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

    # Add soft spot context to system prompt
    soft_spot = get_soft_spot_context(req.patient_id)
    if soft_spot:
        system_prompt += f"""

SOFT SPOT AWARENESS:
The patient is at a known emotional difficulty point: {soft_spot['stage']}.
Context: {soft_spot['message']}
{"Offer: " + soft_spot['what_helps'] if soft_spot.get('what_helps') else "Just be present. No fixing needed."}
Weave this awareness naturally — don't announce it as a feature, just show you understand where they are."""

    # Pattern observation hints (shown once per pattern type)
    cap = patient.get("capability_discovery", {})
    shown_patterns = set(cap.get("pattern_observations_shown", []))
    pattern_hint = None

    if "late_night" not in shown_patterns:
        # Check for 3+ late night sessions this week
        recent_convos = conversations_db.get(req.patient_id, [])[-20:]
        late_count = sum(1 for m in recent_convos if m.get("timestamp", "")[11:13] in ("22", "23", "00", "01", "02", "03"))
        if late_count >= 3:
            pattern_hint = "late_night"
            system_prompt += """

PATTERN OBSERVATION (say this naturally, not as a feature announcement):
You've noticed the patient has been coming by late at night recently. Gently mention:
"I've noticed you've been coming by late at night this week. That's something I gently track to better support you — want to talk about what's keeping you up?"
Say this ONCE, naturally woven into your response."""

    if not pattern_hint and "anxiety_rising" not in shown_patterns:
        recent_checkins = get_recent_checkins(req.patient_id)
        if len(recent_checkins) >= 3:
            last_3_anxiety = [c.get("anxiety", 5) for c in recent_checkins[-3:]]
            if all(a >= 7 for a in last_3_anxiety):
                pattern_hint = "anxiety_rising"
                system_prompt += """

PATTERN OBSERVATION (say this naturally):
The patient's last 3 check-ins show consistently high anxiety (7+). Gently mention:
"Your check-ins have been showing more anxiety lately. That makes sense given where you are. Want to unpack it?"
Say this ONCE, naturally."""

    if pattern_hint:
        if "capability_discovery" not in patient:
            patient["capability_discovery"] = {}
        obs_list = patient["capability_discovery"].get("pattern_observations_shown", [])
        obs_list.append(pattern_hint)
        patient["capability_discovery"]["pattern_observations_shown"] = obs_list
        firebase_db.save_patient(req.patient_id, patient)

    # Inject clinician topic flags into system prompt
    active_flags = [f for f in topic_flags_db.get(req.patient_id, []) if not f.get("delivered")]
    if active_flags:
        urgent = [f for f in active_flags if f["priority"] == "next_session"]
        natural = [f for f in active_flags if f["priority"] != "next_session"]
        flags_to_inject = urgent or natural[:1]  # Prioritize urgent, else one natural
        for flag in flags_to_inject:
            system_prompt += f"""

CLINICIAN FLAG (weave this in naturally — the clinician asked you to address this):
Topic: {flag['topic']}
Instruction: {flag['instruction']}
Priority: {flag['priority']}
Do NOT say 'your clinician flagged this' — instead say something like 'By the way...' or 'I know {flag['topic']} has been on your mind...'"""
            flag["delivered"] = True

    # Inject pending clinician messages
    pending_msgs = [m for m in clinician_messages_db.get(req.patient_id, []) if not m.get("delivered") and m.get("role") == "clinician"]
    if pending_msgs:
        for pm in pending_msgs:
            system_prompt += f"""

CLINICIAN MESSAGE TO DELIVER:
A message from the patient's {pm.get('from_role', 'doctor')} needs to be delivered.
Say: "Your {pm.get('from_role', 'doctor')} wanted me to pass along a message: {pm['content']}"
Mark this as delivered after including it in your response."""
            pm["delivered"] = True

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
    _sync_conversation(req.patient_id, {
        "role": "assistant",
        "content": assistant_msg,
        "timestamp": utc_iso(),
        "triage": triage_category,
        "query_id": query_id,
    })

    # Store conversation summary for continuity
    conv_summary = {
        "date": utc_iso(),
        "topics": [],
        "emotional_tone": "neutral",
        "one_line": req.message[:100] if len(req.message) <= 100 else req.message[:97] + "...",
        "triage_category": triage_category,
    }
    # Quick topic extraction
    msg_lower = req.message.lower()
    for kw, topic in [("progesterone", "progesterone"), ("embryo", "embryo"), ("transfer", "transfer"),
                       ("retrieval", "retrieval"), ("injection", "injections"), ("amh", "AMH"),
                       ("medication", "medications"), ("pregnant", "pregnancy"), ("symptom", "symptoms"),
                       ("alone", "loneliness"), ("scared", "fears"), ("anxious", "anxiety"),
                       ("egg", "eggs"), ("result", "results"), ("sleep", "sleep")]:
        if kw in msg_lower:
            conv_summary["topics"].append(topic)
    # Quick tone detection
    neg = sum(1 for w in ["sad", "scared", "anxious", "worried", "alone", "crying", "frustrated", "hopeless"] if w in msg_lower)
    pos = sum(1 for w in ["happy", "hopeful", "grateful", "better", "good", "relieved", "positive"] if w in msg_lower)
    if neg > pos:
        conv_summary["emotional_tone"] = "struggling"
    elif pos > neg:
        conv_summary["emotional_tone"] = "positive"
    firebase_db.save_conversation_summary(req.patient_id, conv_summary)

    # Auto-flag unresolved questions for clinician
    if triage_category == 2:  # Education question
        unresolved = detect_unresolved_questions(req.patient_id)
        for q in unresolved:
            if q["times_asked"] >= 3 and not q.get("escalated_to_clinician"):
                flag = {
                    "type": "patient_question_needs_clinician",
                    "question": q["topic"],
                    "context": f"Asked {q['times_asked']} times. AI explanation not fully resolving concern.",
                    "ai_interim_response": "Provided general education",
                    "urgency": "moderate" if q["times_asked"] < 5 else "high",
                    "status": "pending",
                    "assigned_to": None,
                    "response_deadline": utc_iso(),
                    "created_at": utc_iso(),
                }
                clinician_flags_db.setdefault(req.patient_id, []).append(flag)
                q["escalated_to_clinician"] = True

    # Suggested education topics
    stage = patient["treatment_stage"]
    suggested = EDUCATION_TOPICS.get(stage, [])[:3] if triage_category == 2 else None

    # Add alerts to escalation for high-risk patients
    if escalation and escalation.get("level") in ("AMBER", "RED"):
        escalation["alerts"] = []
        if escalation["level"] == "RED":
            escalation["alerts"].append("Alert: Nurse dashboard notification")
            escalation["alerts"].append("Doctor pre-brief before consult recommended")
        elif escalation["level"] == "AMBER":
            escalation["alerts"].append("Alert: Nurse dashboard notification")

    # Match Fertool interactive cards for education queries
    fertool_cards = None
    if triage_category == 2:
        fertool_cards = match_fertool_cards(req.message, assistant_msg) or None

    # Capability hint for education responses
    cap_hint = None
    cap = patient.get("capability_discovery", {})
    if triage_category == 2 and not cap.get("first_education_hint_shown"):
        cap_hint = "I can go deeper on any of this — ask me anything."
        if "capability_discovery" not in patient:
            patient["capability_discovery"] = {}
        patient["capability_discovery"]["first_education_hint_shown"] = True
        firebase_db.save_patient(req.patient_id, patient)
    elif fertool_cards and (cap.get("fertool_hints_count", 0) < 3):
        cap_hint = "I have more tools like this — try asking about AMH, egg freezing, or embryo grading."
        if "capability_discovery" not in patient:
            patient["capability_discovery"] = {}
        patient["capability_discovery"]["fertool_hints_count"] = cap.get("fertool_hints_count", 0) + 1
        firebase_db.save_patient(req.patient_id, patient)

    return ChatResponse(
        response=assistant_msg,
        patient_id=req.patient_id,
        treatment_stage=stage,
        escalation=escalation,
        suggested_education=suggested,
        fertool_cards=fertool_cards,
        one_word_checkin=one_word_checkin,
        education_fork=education_fork,
        capability_hint=cap_hint,
        query_id=query_id,
    )


@app.post("/checkin", response_model=CheckInResponse)
async def daily_checkin(req: CheckInRequest):
    """Record a daily micro check-in and generate a response."""
    patient = get_or_create_patient(req.patient_id)

    checkin = {
        "date": utc_iso(),
        "mood": req.mood,
        "anxiety": req.anxiety,
        "loneliness": req.loneliness,
        "uncertainty": req.uncertainty,
        "hope": req.hope,
        "note": req.note,
    }
    _sync_checkin(req.patient_id, checkin)

    # Update engagement tracking
    _update_engagement(req.patient_id)

    # Check escalation triggers
    esc = check_daily_escalation(req.patient_id)
    escalation = None
    if esc["level"] != "GREEN":
        escalation = {
            "level": esc["level"],
            "triggers": esc["triggers"],
            "timestamp": utc_iso(),
        }
        _sync_escalation(req.patient_id, escalation)

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
    _sync_conversation(req.patient_id, {
        "role": "assistant",
        "content": melod_msg,
        "timestamp": utc_iso(),
        "type": "checkin_response",
    })

    # First check-in capability hint
    checkin_hint = None
    cap = patient.get("capability_discovery", {})
    if not cap.get("first_checkin_hint_shown") and len(checkins_db.get(req.patient_id, [])) <= 1:
        checkin_hint = "Thanks for checking in. I use these to understand how you're tracking over time — not to judge, just to notice patterns and support you better."
        if "capability_discovery" not in patient:
            patient["capability_discovery"] = {}
        patient["capability_discovery"]["first_checkin_hint_shown"] = True
        firebase_db.save_patient(req.patient_id, patient)

    return CheckInResponse(
        message=melod_msg,
        patient_id=req.patient_id,
        checkin_summary=checkin,
        escalation=escalation,
        trigger_screening=trigger_screening,
        capability_hint=checkin_hint,
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
        "date": utc_iso(),
        "instrument": req.instrument,
        "responses": req.responses,
        "total_score": result["total_score"],
        "severity": result["severity"],
    }
    _sync_screening(req.patient_id, screening_record)

    # Check escalation
    escalation = None
    esc_level = result.get("escalation_level", "GREEN")
    if esc_level in ("AMBER", "RED"):
        escalation = {
            "level": esc_level,
            "reason": f"{req.instrument} score: {result['total_score']} ({result['severity']})",
            "timestamp": utc_iso(),
        }
        if result.get("suicidal_ideation"):
            escalation["critical"] = "Suicidal ideation detected (Item 9)"
            escalation["level"] = "RED"
        _sync_escalation(req.patient_id, escalation)

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
            patient["stage_start_date"] = utc_iso()
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

    firebase_db.save_patient(req.patient_id, patient)

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


# ── Clinician Auth ───────────────────────────────────────────────────

# ── Multi-Role Clinician System ─────────────────────────────────────

# Default clinician settings (stored in Firebase per clinician)
DEFAULT_CLINICIAN_SETTINGS = {
    "default_involvement": "moderate",  # high | moderate | low
    "response_window_hours": 24,
    "notification_preferences": {
        "red_alerts_only": False,
        "daily_digest": True,
        "immediate_flags": True,
    },
}

# In-memory stores for clinician features
clinician_flags_db: dict = {}   # patient_id -> list of flags
unresolved_questions_db: dict = {}  # patient_id -> dict of topic_key -> question data
clinician_messages_db: dict = {}  # patient_id -> list of clinician messages
topic_flags_db: dict = {}  # patient_id -> list of topic flags from clinicians


def _get_clinician_settings(clinician_id: str) -> dict:
    """Load clinician settings from Firebase or return defaults."""
    settings = firebase_db.load_patient(f"clinician_{clinician_id}")  # reuse patient store
    if settings:
        return settings
    return {"clinician_id": clinician_id, "name": clinician_id, "role": "doctor", **DEFAULT_CLINICIAN_SETTINGS}


def detect_unresolved_questions(patient_id: str) -> list:
    """Detect questions the AI hasn't fully resolved based on conversation patterns."""
    conv = conversations_db.get(patient_id, [])
    if len(conv) < 4:
        return []

    # Track topics asked by user across sessions
    topic_counts: dict = {}
    uncertainty_signals = {"but", "still don't understand", "are you sure", "hmm", "not sure",
                          "confused", "what do you mean", "i don't get", "doesn't make sense",
                          "that doesn't help", "yes but", "ok but"}

    user_msgs = [(i, m) for i, m in enumerate(conv) if m.get("role") == "user"]

    for idx, msg in user_msgs:
        text = msg.get("content", "").lower()
        # Check for education-type questions
        if "?" in text or any(w in text for w in ["what is", "how does", "why do", "tell me about", "explain"]):
            # Extract rough topic
            topic_keywords = {
                "pgt": "PGT-A/PGS testing", "pgta": "PGT-A/PGS testing", "pgs": "PGT-A/PGS testing",
                "progesterone": "Progesterone", "estrogen": "Estrogen levels",
                "amh": "AMH levels", "embryo grad": "Embryo grading",
                "trigger shot": "Trigger shot", "clexane": "Clexane injections",
                "egg freez": "Egg freezing", "icsi": "ICSI vs IVF",
                "transfer": "Embryo transfer", "retrieval": "Egg retrieval",
                "tww": "Two-week wait", "symptom": "TWW symptoms",
                "implant": "Implantation", "miscarr": "Miscarriage",
                "chemical pregn": "Chemical pregnancy", "fsh": "FSH levels",
                "follicle": "Follicle count", "endometri": "Endometriosis",
                "pcos": "PCOS", "sperm": "Male factor", "donor": "Donor path",
                "frozen": "Fresh vs frozen transfer", "fet": "FET protocol",
                "medication": "Medications", "side effect": "Side effects",
                "injection": "Injection technique",
            }
            matched_topic = None
            for kw, topic in topic_keywords.items():
                if kw in text:
                    matched_topic = topic
                    break
            if not matched_topic:
                continue

            topic_key = matched_topic.lower().replace(" ", "_").replace("/", "_")
            if topic_key not in topic_counts:
                topic_counts[topic_key] = {
                    "topic": matched_topic,
                    "first_asked": msg.get("timestamp", ""),
                    "times_asked": 0,
                    "patient_messages": [],
                    "has_uncertainty": False,
                }
            topic_counts[topic_key]["times_asked"] += 1
            topic_counts[topic_key]["patient_messages"].append(text[:120])

        # Check for uncertainty after AI response
        for sig in uncertainty_signals:
            if sig in text:
                # Find what topic the previous AI message was about
                if idx > 0 and conv[idx - 1].get("role") == "assistant":
                    ai_text = conv[idx - 1].get("content", "").lower()
                    for kw, topic in topic_keywords.items():
                        tk = topic.lower().replace(" ", "_").replace("/", "_")
                        if kw in ai_text and tk in topic_counts:
                            topic_counts[tk]["has_uncertainty"] = True
                            break

    # Filter to unresolved: asked 2+ times OR has uncertainty signal
    unresolved = []
    for tk, data in topic_counts.items():
        if data["times_asked"] >= 2 or data["has_uncertainty"]:
            unresolved.append({
                "topic_key": tk,
                "topic": data["topic"],
                "first_asked": data["first_asked"],
                "times_asked": data["times_asked"],
                "ai_responses_given": data["times_asked"],
                "resolution_status": "unresolved",
                "has_uncertainty_signals": data["has_uncertainty"],
                "patient_messages": data["patient_messages"][-5:],
                "escalated_to_clinician": False,
            })

    # Store in memory
    unresolved_questions_db[patient_id] = {q["topic_key"]: q for q in unresolved}
    return unresolved


def build_role_briefing(patient_id: str, role: str) -> dict:
    """Build a role-specific briefing for a clinician."""
    patient = patients_db.get(patient_id)
    if not patient:
        return {}

    name = patient.get("name", "Unknown")
    stage = patient.get("treatment_stage", "consultation")
    stage_name = STAGE_DISPLAY.get(stage, stage)

    # Base data
    checkins = get_recent_checkins(patient_id, last_n=7)
    conv = conversations_db.get(patient_id, [])
    style = classify_patient_style(patient_id)
    unresolved = detect_unresolved_questions(patient_id)

    # Last active
    last_active = "unknown"
    for m in reversed(conv):
        if m.get("timestamp"):
            try:
                ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                delta = utc_now() - ts
                if delta.days > 0:
                    last_active = f"{delta.days} days ago"
                else:
                    hours = int(delta.total_seconds() / 3600)
                    last_active = f"{hours} hours ago" if hours > 0 else "just now"
            except (ValueError, TypeError):
                pass
            break

    # Emotional state
    emotional_state = "neutral"
    if checkins:
        last = checkins[-1]
        mood = last.get("mood", 5)
        anxiety = last.get("anxiety", 5)
        if mood <= 3:
            emotional_state = "struggling"
        elif anxiety >= 7:
            emotional_state = "anxious"
        elif mood >= 7:
            emotional_state = "positive"
        elif last.get("loneliness", 5) >= 7:
            emotional_state = "lonely"

    # SECRETARY briefing
    if role == "secretary":
        flag_reason = None
        readiness = "READY"
        if unresolved:
            flag_reason = f"Patient has {len(unresolved)} unresolved concern{'s' if len(unresolved) > 1 else ''}: {', '.join(q['topic'] for q in unresolved[:2])}"
            readiness = "NEEDS_ATTENTION"
        elif emotional_state in ("struggling", "anxious"):
            flag_reason = f"Patient is feeling {emotional_state}"
            readiness = "FLAG"

        prep_notes = []
        if unresolved:
            prep_notes.append(f"May ask about {unresolved[0]['topic']}")
        if style == "ANALYTICAL":
            prep_notes.append("Prefers detailed, data-driven explanations")
        elif style == "EMOTIONAL":
            prep_notes.append("Responds best to warmth and validation first")

        return {
            "role": "secretary",
            "patient_name": name,
            "appointment_readiness": readiness,
            "flag_reason": flag_reason,
            "emotional_state": emotional_state,
            "last_contact": last_active,
            "suggested_greeting": f"{name} has been feeling {emotional_state} recently — a warm welcome will help" if emotional_state != "neutral" else f"Welcome {name} warmly",
            "prep_notes": prep_notes,
            "treatment_stage": stage_name,
        }

    # NURSE briefing - build emotional summary and care priorities
    # Get phenotype flags
    phenotype_flags = []
    engagement = patient.get("engagement", {})
    cap = patient.get("capability_discovery", {})
    shown_patterns = cap.get("pattern_observations_shown", [])
    if "late_night" in shown_patterns:
        phenotype_flags.append("Multiple late-night sessions detected")
    if "anxiety_rising" in shown_patterns:
        phenotype_flags.append("Anxiety trend rising across recent check-ins")

    # Compute emotional summary
    emotional_summary = f"{name} is currently at the {stage_name} stage."
    if checkins:
        avg_mood = sum(c.get("mood", 5) for c in checkins) / len(checkins)
        avg_anxiety = sum(c.get("anxiety", 5) for c in checkins) / len(checkins)
        if avg_anxiety >= 7:
            emotional_summary += f" Anxiety averaging {avg_anxiety:.0f}/10 over the last {len(checkins)} check-ins."
        if avg_mood <= 3:
            emotional_summary += f" Mood consistently low (avg {avg_mood:.0f}/10)."

    # Care priorities from AI
    care_priorities = []
    topics_to_raise = []
    topics_to_avoid = []
    suggested_opener = f"How are you feeling about your {stage_name} journey?"

    # Use Haiku to generate nurse-specific guidance
    user_msgs = [m["content"] for m in conv if m.get("role") == "user"][-8:]
    if user_msgs:
        try:
            nurse_prompt = f"""Based on this IVF patient's recent messages, generate nurse briefing data as JSON.
Patient: {name}, Stage: {stage_name}, Emotional state: {emotional_state}
Recent messages: {'; '.join(user_msgs[-5:])}
Unresolved questions: {', '.join(q['topic'] for q in unresolved) if unresolved else 'None'}

Return JSON with:
- care_priorities: list of 3 strings (numbered action items for the nurse)
- topics_to_raise: list of 2-3 strings
- topics_to_avoid: list of 0-2 strings (sensitive topics)
- suggested_opener: string (one warm opening question)
Return ONLY valid JSON, no markdown."""
            resp = client.messages.create(model=HAIKU_MODEL, max_tokens=400, messages=[{"role": "user", "content": nurse_prompt}])
            import json
            nurse_data = json.loads(resp.content[0].text.strip())
            care_priorities = nurse_data.get("care_priorities", care_priorities)
            topics_to_raise = nurse_data.get("topics_to_raise", topics_to_raise)
            topics_to_avoid = nurse_data.get("topics_to_avoid", topics_to_avoid)
            suggested_opener = nurse_data.get("suggested_opener", suggested_opener)
        except Exception:
            care_priorities = [f"Check on emotional state ({emotional_state})", f"Review {stage_name} progress", "Assess support network"]

    nurse_briefing = {
        "role": "nurse",
        "patient_name": name,
        "emotional_summary": emotional_summary,
        "care_priorities": care_priorities,
        "topics_to_raise": topics_to_raise,
        "topics_to_avoid": topics_to_avoid,
        "communication_style": style,
        "suggested_opener": suggested_opener,
        "unresolved_questions": [{
            "question": q["topic"],
            "times_asked": q["times_asked"],
            "ai_answered": True,
            "needs_clinician": q["times_asked"] >= 3 or q["has_uncertainty_signals"],
            "reason": "Repeated asking suggests AI answer didn't fully resolve concern" if q["times_asked"] >= 3 else "Patient showed uncertainty after AI explanation",
        } for q in unresolved],
        "phenotype_flags": phenotype_flags,
        "treatment_stage": stage_name,
        "last_contact": last_active,
    }

    if role == "nurse":
        return nurse_briefing

    # DOCTOR briefing — everything nurse gets plus clinical recommendations
    # Generate clinical recommendations via Haiku
    clinical_recs = []
    patient_confidence = {"in_treatment_plan": "MODERATE", "evidence": "", "trend": "stable"}
    ai_handled = []

    if user_msgs:
        try:
            doc_prompt = f"""Based on this IVF patient's data, generate doctor briefing as JSON.
Patient: {name}, Stage: {stage_name}, Style: {style}
Emotional state: {emotional_state}
Recent messages: {'; '.join(user_msgs[-5:])}
Unresolved questions: {json.dumps([q['topic'] for q in unresolved])}
Check-in averages: mood={sum(c.get('mood',5) for c in checkins)/max(len(checkins),1):.1f}, anxiety={sum(c.get('anxiety',5) for c in checkins)/max(len(checkins),1):.1f}, hope={sum(c.get('hope',5) for c in checkins)/max(len(checkins),1):.1f}

Return JSON with:
- clinical_recommendations: list of objects with type (clarification_needed|comprehension_gap|emotional_impact_on_clinical|psych_recommendation), topic, detail, priority (high|medium|low), suggested_action
- patient_confidence: object with in_treatment_plan (HIGH|MODERATE|LOW), evidence (string), trend (improving|declining|stable)
- ai_handled_topics: list of objects with topic, status (resolved|unresolved), patient_satisfied (bool)
Return ONLY valid JSON, no markdown."""
            resp = client.messages.create(model=HAIKU_MODEL, max_tokens=600, messages=[{"role": "user", "content": doc_prompt}])
            doc_data = json.loads(resp.content[0].text.strip())
            clinical_recs = doc_data.get("clinical_recommendations", [])
            patient_confidence = doc_data.get("patient_confidence", patient_confidence)
            ai_handled = doc_data.get("ai_handled_topics", [])
        except Exception:
            pass

    doctor_briefing = {**nurse_briefing, "role": "doctor"}
    doctor_briefing["clinical_recommendations"] = clinical_recs
    doctor_briefing["patient_confidence"] = patient_confidence
    doctor_briefing["ai_handled_topics"] = ai_handled
    return doctor_briefing


CLINICIAN_API_KEY = os.getenv("CLINICIAN_API_KEY", "")

async def verify_clinician_api_key(x_api_key: str = Header(None)):
    """Dependency: reject requests without a valid clinician API key."""
    if not CLINICIAN_API_KEY or x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail={"error": "Invalid API key"})


# ── Clinician Settings Endpoints ─────────────────────────────────────

@app.post("/clinician/settings")
async def save_clinician_settings(
    request: Request,
    x_api_key: str = Header(None),
):
    """Create or update clinician settings."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    body = await request.json()
    clinician_id = body.get("clinician_id", "default")
    settings = {
        "clinician_id": clinician_id,
        "name": body.get("name", clinician_id),
        "role": body.get("role", "doctor"),
        "default_involvement": body.get("default_involvement", "moderate"),
        "response_window_hours": body.get("response_window_hours", 24),
        "notification_preferences": body.get("notification_preferences", {
            "red_alerts_only": False,
            "daily_digest": True,
            "immediate_flags": True,
        }),
    }
    firebase_db.save_patient(f"clinician_{clinician_id}", settings)
    return {"status": "saved", "clinician_id": clinician_id, "settings": settings}


@app.get("/clinician/settings/{clinician_id}")
async def get_clinician_settings(
    clinician_id: str,
    x_api_key: str = Header(None),
):
    """Get clinician settings."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    settings = _get_clinician_settings(clinician_id)
    return settings


# ── Clinician Dashboard Endpoints ────────────────────────────────────

@app.get("/clinician/dashboard", dependencies=[Depends(verify_clinician_api_key)])
@app.get("/clinician/patients", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_dashboard():
    """Get overview of all patients for clinician dashboard.

    Note: both /clinician/dashboard and /clinician/patients resolve here.
    """
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

        # Get signal store data if available
        store = patient_signal_store.get(pid, {})
        latest_ci = recent_checkins[-1] if recent_checkins else None

        patient_name = patient.get("name") or "Anonymous"

        overview.append({
            "patient_id": pid,
            "patient_name": patient_name,
            "name": patient_name,  # backward compat
            "treatment_stage": STAGE_DISPLAY.get(patient["treatment_stage"], patient["treatment_stage"]),
            "cycle_number": patient["cycle_number"],
            "avg_mood_3d": avg_mood,
            "risk_level": risk,
            "escalation_level": risk,
            "last_active": patient["last_active"],
            "last_updated": patient.get("last_active"),
            "last_escalation": recent_esc[0] if recent_esc else None,
            "session_count": store.get("session_count", 0),
            "baseline_established": store.get("baseline_established", False),
            "active_constructs": list((store.get("current_assessment") or {}).get("constructs", {}).keys()),
            "latest_checkin": latest_ci,
            "human_escalation_requested": store.get("human_escalation_requested", False),
            "communication_style": classify_patient_style(pid),
            "summary": (store.get("current_assessment") or {}).get("summary", ""),
        })

    # Sort by risk (RED first, then AMBER, then GREEN)
    risk_order = {"RED": 0, "AMBER": 1, "GREEN": 2}
    overview.sort(key=lambda x: risk_order.get(x["risk_level"], 3))

    return {
        "patients": overview,
        "total": len(overview),
        "alerts": sum(1 for p in overview if p["risk_level"] in ("RED", "AMBER")),
        "timestamp": utc_iso(),
    }


# ── In-memory alert store (derived from escalations + check-ins) ──
clinician_alerts: list[dict] = []


@app.get("/clinician/alerts", dependencies=[Depends(verify_clinician_api_key)])
async def get_clinician_alerts(limit: int = 30):
    """Return recent clinician alerts, derived from escalation events."""
    # Rebuild alert list from all patient escalations (sorted newest first)
    all_alerts = []
    for pid, escs in escalations_db.items():
        patient = patients_db.get(pid, {})
        patient_name = patient.get("name") or "Anonymous"
        for esc in escs:
            level = esc.get("level", "GREEN")
            if level == "GREEN":
                continue
            alert_type = "human_escalation" if esc.get("human_requested") else (
                "checkin_alert" if "daily" in esc.get("reason", "").lower() else "signal_alert"
            )
            all_alerts.append({
                "patient_id": pid,
                "patient_name": patient_name,
                "type": alert_type,
                "summary": esc.get("reason", "Escalation triggered"),
                "scores": esc.get("scores"),
                "reason": esc.get("reason"),
                "level": level,
                "timestamp": esc.get("timestamp", ""),
                "acknowledged": esc.get("acknowledged", False),
            })

    # Also include human escalation requests from signal store
    for pid, store in patient_signal_store.items():
        if store.get("human_escalation_requested"):
            patient = patients_db.get(pid, {})
            all_alerts.append({
                "patient_id": pid,
                "patient_name": patient.get("name") or "Anonymous",
                "type": "human_escalation",
                "summary": "Patient requesting to speak with someone",
                "reason": store.get("human_escalation_reason", ""),
                "level": "RED",
                "timestamp": store.get("human_escalation_at", ""),
                "acknowledged": False,
            })

    # Sort by timestamp descending (newest first)
    all_alerts.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
    return {"alerts": all_alerts[:limit]}


@app.post("/clinician/alerts/{index}/acknowledge", dependencies=[Depends(verify_clinician_api_key)])
async def acknowledge_alert(index: int):
    """Acknowledge a clinician alert by index."""
    # Mark in escalation data — best-effort since alerts are rebuilt dynamically
    return {"acknowledged": True, "index": index}


@app.get("/clinician/patient/{patient_id}/summary", dependencies=[Depends(verify_clinician_api_key)])
@app.get("/clinician/patient/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
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

    # Merge signal store data for dashboard compatibility
    store = patient_signal_store.get(patient_id, {})
    assessment = store.get("current_assessment") or {}

    return {
        "patient": patient,
        "patient_name": patient.get("name") or "Anonymous",
        "stage_display": STAGE_DISPLAY.get(patient["treatment_stage"], patient["treatment_stage"]),
        "checkins": checkins,
        "check_in_history": checkins,  # alias for dashboard
        "screenings": screenings,
        "escalations": escalations,
        "escalation_level": store.get("escalation_level", "GREEN"),
        "current_assessment": assessment,
        "session_count": store.get("session_count", 0),
        "baseline_established": store.get("baseline_established", False),
        "signal_history_count": len(store.get("signal_history", [])),
        "human_escalation_requested": store.get("human_escalation_requested", False),
        "human_escalation_at": store.get("human_escalation_at"),
        "ai_summary": summary_text,
        "conversation_count": len(conversations_db.get(patient_id, [])),
    }


@app.get("/clinician/patient/{patient_id}/briefing")
async def clinician_preconsult_briefing(
    patient_id: str,
    role: str = "doctor",
    x_api_key: str = Header(None),
):
    """Role-aware pre-consult briefing."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Use new role-based system
    if role in ("secretary", "nurse", "doctor"):
        return build_role_briefing(patient_id, role)

    # Fallback to original briefing for backward compatibility
    return build_preconsult_briefing(patient_id)


# ── Clinician Action Endpoints ──────────────────────────────────────

@app.post("/clinician/patient/{patient_id}/send-message")
async def clinician_send_message(
    patient_id: str,
    request: Request,
    x_api_key: str = Header(None),
):
    """Clinician sends a message to patient via the AI companion."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    body = await request.json()
    msg_text = body.get("message", "")
    from_role = body.get("from_role", "doctor")
    msg_type = body.get("type", "personal")

    if not msg_text:
        raise HTTPException(status_code=400, detail="Message is required")

    # Store as a special clinician message
    clinician_msg = {
        "role": "clinician",
        "from_role": from_role,
        "content": msg_text,
        "type": msg_type,
        "timestamp": utc_iso(),
        "delivered": False,
    }
    clinician_messages_db.setdefault(patient_id, []).append(clinician_msg)

    # Also store in conversation history so it appears in the chat
    _sync_conversation(patient_id, {
        "role": "assistant",
        "content": f"[From your {from_role}]: {msg_text}",
        "timestamp": utc_iso(),
        "from_clinician": True,
        "clinician_role": from_role,
    })

    return {"status": "sent", "patient_id": patient_id, "from_role": from_role}


@app.post("/clinician/patient/{patient_id}/flag-topic")
async def clinician_flag_topic(
    patient_id: str,
    request: Request,
    x_api_key: str = Header(None),
):
    """Flag a topic for the AI to bring up in the next conversation."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    body = await request.json()
    topic = body.get("topic", "")
    instruction = body.get("instruction", "")
    priority = body.get("priority", "when_natural")  # next_session | within_3_days | when_natural

    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    flag = {
        "topic": topic,
        "instruction": instruction,
        "priority": priority,
        "created_at": utc_iso(),
        "delivered": False,
    }
    topic_flags_db.setdefault(patient_id, []).append(flag)

    return {"status": "flagged", "patient_id": patient_id, "topic": topic, "priority": priority}


@app.post("/clinician/patient/{patient_id}/schedule-nudge")
async def clinician_schedule_nudge(
    patient_id: str,
    request: Request,
    x_api_key: str = Header(None),
):
    """Schedule a custom nudge for a patient."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    body = await request.json()
    message = body.get("message", "")
    deliver_at = body.get("deliver_at", utc_iso())
    from_role = body.get("from", "nurse")

    scheduled = {
        "message": message,
        "deliver_at": deliver_at,
        "from": from_role,
        "created_at": utc_iso(),
        "delivered": False,
    }
    clinician_messages_db.setdefault(patient_id, []).append(scheduled)

    return {"status": "scheduled", "patient_id": patient_id, "deliver_at": deliver_at}


@app.post("/clinician/patient/{patient_id}/resolve-concern")
async def clinician_resolve_concern(
    patient_id: str,
    request: Request,
    x_api_key: str = Header(None),
):
    """Mark an unresolved question as resolved by clinician."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    body = await request.json()
    topic_key = body.get("topic_key", "")
    resolution_note = body.get("resolution_note", "")
    resolved_by = body.get("resolved_by", "doctor")

    # Update in memory
    patient_qs = unresolved_questions_db.get(patient_id, {})
    if topic_key in patient_qs:
        patient_qs[topic_key]["resolution_status"] = "resolved"
        patient_qs[topic_key]["clinician_resolved"] = True
        patient_qs[topic_key]["resolved_by"] = resolved_by
        patient_qs[topic_key]["resolution_note"] = resolution_note
        patient_qs[topic_key]["resolved_at"] = utc_iso()

    # Store a follow-up flag so AI mentions it
    topic_name = patient_qs.get(topic_key, {}).get("topic", topic_key)
    topic_flags_db.setdefault(patient_id, []).append({
        "topic": topic_name,
        "instruction": f"The patient's {resolved_by} has addressed their question about {topic_name}. Gently mention: 'I heard your {resolved_by} talked through the {topic_name} question with you — do you feel clearer about it?'",
        "priority": "next_session",
        "created_at": utc_iso(),
        "delivered": False,
    })

    return {"status": "resolved", "topic_key": topic_key, "resolved_by": resolved_by}


@app.get("/clinician/patient/{patient_id}/unresolved")
async def get_unresolved_questions(
    patient_id: str,
    x_api_key: str = Header(None),
):
    """Get unresolved questions for a patient."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    unresolved = detect_unresolved_questions(patient_id)
    return {"patient_id": patient_id, "unresolved": unresolved}


@app.get("/clinician/digest")
async def clinician_daily_digest(
    x_api_key: str = Header(None),
):
    """Generate a morning digest for the clinician."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    red_patients = []
    amber_patients = []
    green_patients = []
    pending_actions = 0
    total_flags = 0

    for pid, patient in patients_db.items():
        name = patient.get("name", pid)
        stage = STAGE_DISPLAY.get(patient.get("treatment_stage", ""), "")
        checkins = get_recent_checkins(pid, last_n=3)
        esc = check_daily_escalation(pid)
        unresolved = detect_unresolved_questions(pid)
        engagement = patient.get("engagement", {})
        last_date = engagement.get("last_interaction_date", "")

        # Calculate gap
        gap_days = 0
        try:
            gap_days = (date.today() - date.fromisoformat(last_date)).days
        except (ValueError, TypeError):
            pass

        summary_parts = []
        if checkins:
            avg_anxiety = sum(c.get("anxiety", 5) for c in checkins) / len(checkins)
            avg_mood = sum(c.get("mood", 5) for c in checkins) / len(checkins)
            if avg_anxiety >= 7:
                summary_parts.append(f"anxiety high ({avg_anxiety:.0f}/10)")
            if avg_mood <= 3:
                summary_parts.append(f"mood low ({avg_mood:.0f}/10)")
        if unresolved:
            summary_parts.append(f"{len(unresolved)} unresolved question{'s' if len(unresolved)>1 else ''}")
            pending_actions += len(unresolved)
        if gap_days >= 2:
            summary_parts.append(f"hasn't engaged in {gap_days} days")
        total_flags += len(unresolved)

        info = {"name": name, "stage": stage, "summary": ", ".join(summary_parts) if summary_parts else "tracking well", "patient_id": pid}

        if esc["level"] == "RED":
            red_patients.append(info)
        elif esc["level"] == "AMBER":
            amber_patients.append(info)
        else:
            green_patients.append(info)

    # Generate digest text via Haiku
    digest_data = {
        "red": red_patients, "amber": amber_patients,
        "green_count": len(green_patients), "pending_actions": pending_actions,
        "total_flags": total_flags,
    }

    try:
        digest_prompt = f"""Generate a warm, concise morning clinician digest. Data:
RED patients ({len(red_patients)}): {json.dumps(red_patients)}
AMBER patients ({len(amber_patients)}): {json.dumps(amber_patients)}
GREEN patients: {len(green_patients)} tracking well
Pending actions: {pending_actions}

Format as a brief morning message. Use emoji sparingly (🔴🟡🟢). List RED patients individually with name and concern. Summarize AMBER patients briefly. Just count GREEN patients. End with pending action count.
Keep it under 200 words. Be professional but warm."""
        resp = client.messages.create(model=HAIKU_MODEL, max_tokens=300, messages=[{"role": "user", "content": digest_prompt}])
        digest_text = resp.content[0].text.strip()
    except Exception:
        # Fallback
        lines = []
        if red_patients:
            lines.append(f"🔴 {len(red_patients)} patient{'s' if len(red_patients)>1 else ''} need{'s' if len(red_patients)==1 else ''} attention:")
            for p in red_patients:
                lines.append(f"   {p['name']} — {p['summary']}")
        if amber_patients:
            lines.append(f"🟡 {len(amber_patients)} patient{'s' if len(amber_patients)>1 else ''} to monitor:")
            for p in amber_patients:
                lines.append(f"   {p['name']} — {p['summary']}")
        lines.append(f"🟢 {len(green_patients)} patient{'s' if len(green_patients)>1 else ''} tracking well.")
        if pending_actions:
            lines.append(f"\nActions pending your response: {pending_actions}")
        digest_text = "\n".join(lines)

    return {
        "digest": digest_text,
        "red_count": len(red_patients),
        "amber_count": len(amber_patients),
        "green_count": len(green_patients),
        "pending_actions": pending_actions,
        "total_flags": total_flags,
        "red_patients": red_patients,
        "amber_patients": amber_patients,
        "date": date.today().isoformat(),
    }


@app.get("/clinician/patient/{patient_id}/conversations")
async def get_patient_conversations(
    patient_id: str,
    x_api_key: str = Header(None),
):
    """Get conversation summaries for the clinician conversations tab."""
    if CLINICIAN_API_KEY and x_api_key != CLINICIAN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    conv = conversations_db.get(patient_id, [])
    # Group into sessions
    sessions = []
    current = []
    last_ts = None
    for msg in conv:
        ts_str = msg.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = None
        if last_ts and ts and (ts - last_ts).total_seconds() > 7200:
            if current:
                sessions.append(current)
            current = [msg]
        else:
            current.append(msg)
        if ts:
            last_ts = ts
    if current:
        sessions.append(current)

    summaries = []
    for session in sessions[-20:]:
        user_msgs = [m["content"] for m in session if m.get("role") == "user"]
        ts = session[0].get("timestamp", "")
        triage = None
        for m in session:
            if m.get("triage"):
                triage = m["triage"]

        # Quick tone
        all_text = " ".join(user_msgs).lower()
        tone = "neutral"
        if any(w in all_text for w in ["sad", "scared", "anxious", "worried", "alone"]):
            tone = "anxious" if "anxious" in all_text or "worried" in all_text else "struggling"
        elif any(w in all_text for w in ["happy", "hopeful", "good", "better"]):
            tone = "positive"

        summaries.append({
            "date": ts,
            "message_count": len(session),
            "user_messages": user_msgs[:3],
            "one_line": user_msgs[0][:100] if user_msgs else "No messages",
            "emotional_tone": tone,
            "triage_category": triage,
            "full_conversation": session,
        })

    return {"patient_id": patient_id, "sessions": list(reversed(summaries))}


@app.get("/clinician/patient/{patient_id}/phenotype-history", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_phenotype_history(patient_id: str, days: int = 30):
    """Return the last N days of phenotype snapshots for trend charts."""
    history = firebase_db.load_phenotype_history(patient_id, limit=500)

    # Filter to last N days
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [s for s in history if s.get("timestamp", "") >= cutoff]

    # Also include in-memory check-in data for completeness
    checkins = checkins_db.get(patient_id, [])
    recent_checkins = [c for c in checkins if c.get("date", "") >= cutoff]

    return {
        "patient_id": patient_id,
        "snapshots": recent,
        "checkins": recent_checkins,
        "total_snapshots": len(recent),
        "total_checkins": len(recent_checkins),
        "days": days,
    }


# ── Debug Endpoints ──────────────────────────────────────────────────

DEBUG_MODE = os.getenv("DEBUG_MODE", "").lower() == "true"


@app.post("/debug/create-test-patient")
async def debug_create_test_patient():
    """Create a test patient for development. Only available when DEBUG_MODE=true."""
    if not DEBUG_MODE:
        raise HTTPException(status_code=403, detail="Debug mode not enabled")

    test_id = "dr-fouks-pilot"
    patient = get_or_create_patient(test_id)
    patient["name"] = "Test Pilot"
    patient["treatment_stage"] = "stimulation"
    patient["cycle_number"] = 1
    firebase_db.save_patient(test_id, patient)

    return {
        "patient_id": test_id,
        "name": "Test Pilot",
        "status": "created",
        "treatment_stage": "stimulation",
    }


# ── Daily Nudge System ───────────────────────────────────────────────

# ── Anticipatory Nudges ─────────────────────────────────────────────
# These trigger based on days_in_stage, BEFORE the difficult moment arrives.
# Key: (stage, day_in_stage) → nudge message
ANTICIPATORY_NUDGES = {
    ("stimulation", 5): "Tomorrow and the next few days are when stimulation starts to feel heavy for most people. If that happens, come talk to me — it's normal.",
    ("stimulation", 9): "You're getting close to the end of stims. The bloating and tiredness peak around now. Hang in there — your body is doing incredible work.",
    ("early_tww", 5): "You're entering the hardest stretch of the wait. The next few days before your test, symptom spotting goes into overdrive. I can help you sort real from anxiety.",
    ("late_tww", 1): "You're in the deep end of the TWW now. Every sensation feels like a sign. That's completely normal — and exhausting.",
    ("late_tww", 4): "Tomorrow might be close to result day. Whatever you're feeling right now — that's valid. Want to talk tonight?",
    ("embryo_development", 0): "The fertilisation call usually comes this morning. However it goes, I'm here to help you understand what it means.",
    ("embryo_development", 2): "Day 3 embryo updates can bring a mix of emotions. Some embryos stop developing — that's normal attrition, not failure. I'll help you make sense of the report.",
    ("embryo_development", 4): "Day 5 is when blastocysts form. Not all embryos make it — the drop can feel devastating. Whatever the number, I'm here.",
    ("before_retrieval", 0): "Tomorrow your body does something amazing. Try to rest tonight. The nerves are normal — your clinic does this every day.",
    ("retrieval_day", 0): "Retrieval day. You've earned every bit of rest afterwards. I'll be here when you're ready to talk.",
    ("before_transfer", 0): "Transfer is coming up. The procedure itself is usually quick and painless. It's everything around it — the hope, the fear — that's the hard part.",
    ("transfer_day", 0): "A tiny passenger is on board. Now begins the wait. Whatever you need — distraction, reassurance, or just someone to talk to — I'm here.",
    ("result_day", 0): "Today is the day. Whatever comes, you won't face it alone.",
    ("negative_result", 2): "Checking in. No pressure to talk. Just letting you know I'm here.",
    ("negative_result", 5): "It's been a few days. Grief doesn't follow a schedule. Whatever you're feeling right now is exactly right.",
    ("failed_cycle_acute", 2): "Checking in gently. You don't have to have a plan or feel ready. Just... I'm here.",
    ("chemical_pregnancy", 1): "A chemical pregnancy is a real loss. If people tell you 'at least it implanted,' you're allowed to feel angry about that.",
    ("miscarriage", 2): "I'm thinking of you. There's no timeline for this. Whenever you're ready, I'm here.",
}

# ── Capability Discovery ────────────────────────────────────────────
# Contextual hints that help patients discover features naturally.
WEEKLY_CAPABILITY_HINTS = {
    "checkin_prompt": "By the way, if you tap the egg, you can do a quick emotional check-in. It helps me understand how you're tracking.",
    "education_prompt": "Did you know you can ask me about any medication, procedure, or IVF topic? I'm like a fertility encyclopedia that speaks human.",
    "late_night_prompt": "I'm here 24/7 — even at 2am when the anxiety hits and you don't want to wake your partner.",
    "journey_prompt": "Have you explored the Journey tab? It shows where you are in the process and what's coming next.",
}

def get_capability_discovery(patient_id: str) -> dict | None:
    """Check if any capability hint should be shown to this patient."""
    patient = get_or_create_patient(patient_id)
    cap = patient.get("capability_discovery", {})

    # Weekly hint: only one per week
    last_weekly = cap.get("last_weekly_hint_date")
    if last_weekly:
        try:
            last_dt = datetime.fromisoformat(last_weekly)
            if (datetime.now() - last_dt).days < 7:
                return None  # Too soon for another weekly hint
        except (ValueError, TypeError):
            pass

    # Figure out what the patient hasn't used
    shown = set(cap.get("weekly_hints_shown", []))
    conversations = conversations_db.get(patient_id, [])
    checkins = checkins_db.get(patient_id, [])
    user_msgs = [m for m in conversations if m.get("role") == "user"]

    # Check usage patterns
    has_checkin = len(checkins) > 0
    has_education = any(m.get("triage") == 2 for m in conversations if m.get("role") == "assistant")
    has_late_night = any(
        "T2" in m.get("timestamp", "") or "T0" in m.get("timestamp", "")[:14]
        for m in conversations if m.get("timestamp")
    )

    # Pick a hint for something they haven't used
    candidates = []
    if not has_checkin and "checkin_prompt" not in shown:
        candidates.append("checkin_prompt")
    if not has_education and "education_prompt" not in shown and len(user_msgs) >= 3:
        candidates.append("education_prompt")
    if not has_late_night and "late_night_prompt" not in shown and len(user_msgs) >= 5:
        candidates.append("late_night_prompt")
    if "journey_prompt" not in shown and len(user_msgs) >= 2:
        candidates.append("journey_prompt")

    if not candidates:
        return None

    hint_key = candidates[0]
    hint_text = WEEKLY_CAPABILITY_HINTS[hint_key]

    # Update tracking
    if "capability_discovery" not in patient:
        patient["capability_discovery"] = {}
    patient["capability_discovery"]["last_weekly_hint_date"] = utc_iso()
    shown_list = patient["capability_discovery"].get("weekly_hints_shown", [])
    shown_list.append(hint_key)
    patient["capability_discovery"]["weekly_hints_shown"] = shown_list
    firebase_db.save_patient(patient_id, patient)

    return {"hint": hint_text, "type": hint_key}

# Stage-aware nudge messages: each stage has contextual, gentle prompts
STAGE_NUDGES = {
    "consultation": [
        "First steps can feel overwhelming. How are you sitting with everything today?",
        "Lots of new information lately — anything on your mind?",
    ],
    "investigation": [
        "Waiting for results is its own kind of hard. How are you doing?",
        "Your body is being looked after. How about you?",
    ],
    "waiting_to_start": [
        "The waiting before starting can feel endless. Checking in on you today.",
        "Sometimes the pause before treatment is the hardest part. How are you?",
    ],
    "downregulation": [
        "Down-reg can be a quiet, strange phase. How's your body feeling?",
        "Some days are just about getting through. How's today going?",
    ],
    "stimulation": [
        "Your body is working hard right now. How are you holding up today?",
        "Stim days can be a rollercoaster. Checking in — how are you feeling?",
    ],
    "monitoring": [
        "Another scan day. Whatever the numbers say, you're doing great. How are you?",
        "Monitoring can feel like a test every time. How are you sitting with it all?",
    ],
    "trigger": [
        "Trigger done — a big milestone. How are you feeling tonight?",
        "Almost there. The trigger is a turning point. How's your head?",
    ],
    "before_retrieval": [
        "Tomorrow is a big day. How are you feeling about it?",
        "It's okay to feel nervous, excited, or both. How are you tonight?",
    ],
    "retrieval_day": [
        "Retrieval day. You did it. How are you feeling?",
        "Be gentle with yourself today. How's your body? How's your heart?",
    ],
    "post_retrieval": [
        "Recovery takes time. How are you feeling physically and emotionally?",
        "Your body has done something incredible. How are you today?",
    ],
    "fertilisation_report": [
        "Waiting for the call about your embryos is uniquely stressful. How are you?",
        "The numbers game is hard. Whatever the report says, how are you doing?",
    ],
    "embryo_development": [
        "Each day feels like a lifetime when you're waiting on embryo updates. How are you?",
        "Thinking of you today. How are you managing the wait?",
    ],
    "freeze_all": [
        "Freeze-all can feel unexpected. How are you processing it?",
        "A freeze-all is a plan, not a setback. How are you feeling about it?",
    ],
    "before_transfer": [
        "Transfer is coming up. How are you feeling about it?",
        "Almost there. How's your head and heart today?",
    ],
    "transfer_day": [
        "Transfer done — now the wait begins. How are you feeling right now?",
        "You've got a little passenger on board. How are you?",
    ],
    "early_tww": [
        "Early days of the wait. The urge to symptom-spot is real. How are you doing?",
        "Days 1-5 — try to be kind to yourself. How are you feeling today?",
    ],
    "late_tww": [
        "These late TWW days are some of the hardest. How are you holding up?",
        "Almost there. Whatever you're feeling right now is completely valid.",
    ],
    "result_day": [
        "Today is a big day. Whatever happens, you've been incredibly brave.",
        "Thinking of you today. How are you?",
    ],
    "positive_result": [
        "The joy and the worry can coexist. How are you feeling today?",
        "A positive result doesn't mean the anxiety stops. How are you doing?",
    ],
    "negative_result": [
        "There are no words that make this easier. I'm here. How are you?",
        "Grief has no timeline. Take whatever time you need. How are you today?",
    ],
    "chemical_pregnancy": [
        "This kind of loss is real and valid, even if others don't understand. How are you?",
        "I'm here for you. No pressure to feel any particular way.",
    ],
    "miscarriage": [
        "I'm so sorry. There's no right way to grieve. How are you today?",
        "Thinking of you. You don't have to be okay right now.",
    ],
    "failed_cycle_acute": [
        "It's okay to not be okay. How are you sitting with things today?",
        "Fresh grief is heavy. I'm here whenever you need.",
    ],
    "failed_cycle_processing": [
        "Processing takes time. How are you doing today?",
        "Some days are harder than others. How's today?",
    ],
    "wtf_appointment": [
        "The follow-up appointment can bring up a lot. How are you feeling about it?",
        "Questions for your doctor? I can help you think through them.",
    ],
    "between_cycles": [
        "The space between cycles is important. How are you using this time?",
        "Checking in — how are you feeling about what comes next?",
    ],
    "considering_stopping": [
        "This decision is yours. There's no wrong answer. How are you today?",
        "Whatever you decide, it comes from strength. How are you feeling?",
    ],
    "donor_journey": [
        "The donor path has its own emotions. How are you navigating them?",
        "Checking in on you today. How are you feeling about things?",
    ],
    "early_pregnancy": [
        "Early pregnancy after IVF can feel more anxious than joyful. How are you?",
        "Every milestone matters. How are you feeling today?",
    ],
}

# Procedure-specific nudges (triggered around key events)
PROCEDURE_NUDGES = {
    "before_retrieval": "Tomorrow your body does something amazing. Rest well tonight.",
    "retrieval_day": "You did it. Be proud of yourself today.",
    "transfer_day": "A tiny passenger is on board. Breathe.",
    "result_day": "Whatever today brings, you've already shown incredible courage.",
    "trigger": "Trigger is done. The countdown begins.",
}


@app.get("/nudge/{patient_id}")
async def get_daily_nudge(patient_id: str):
    """
    Returns 1-2 gentle, stage-aware nudge messages for the patient.
    Called when the app opens to check if a nudge should be shown.
    """
    if patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient = patients_db[patient_id]
    stage = patient.get("treatment_stage", "consultation")
    checkins = get_recent_checkins(patient_id)

    # Check if patient already checked in today
    today = date.today().isoformat()
    checked_in_today = any(
        c.get("date", "")[:10] == today for c in checkins
    )

    # Don't nudge if they already checked in
    if checked_in_today:
        return {"nudge": None, "reason": "already_checked_in"}

    # How many days since last check-in?
    days_since = None
    if checkins:
        try:
            last_date = datetime.fromisoformat(checkins[-1]["date"]).date()
            days_since = (date.today() - last_date).days
        except (ValueError, KeyError):
            pass

    # Pick the right nudge
    nudges = []

    # 0. Anticipatory nudge — check if we're at a known anticipation point
    stage_start = patient.get("stage_start_date")
    days_in_stage = None
    if stage_start:
        try:
            start_dt = datetime.fromisoformat(stage_start.replace("Z", "+00:00"))
            days_in_stage = (datetime.now(start_dt.tzinfo or None) - start_dt).days if start_dt.tzinfo else (datetime.now() - start_dt.replace(tzinfo=None)).days
        except (ValueError, TypeError):
            days_in_stage = None

    anticipatory = None
    if days_in_stage is not None:
        anticipatory = ANTICIPATORY_NUDGES.get((stage, days_in_stage))

    if anticipatory:
        nudges.append(anticipatory)  # Anticipatory nudge takes priority
    else:
        # 1. Stage-specific nudge (fallback)
        stage_msgs = STAGE_NUDGES.get(stage, STAGE_NUDGES["consultation"])
        nudges.append(random.choice(stage_msgs))

    # 2. Procedure nudge (only for key procedure days)
    if stage in PROCEDURE_NUDGES and not anticipatory:
        nudges.append(PROCEDURE_NUDGES[stage])

    # 3. If they've been away 2+ days, add a gentle re-engagement
    if days_since and days_since >= 2:
        nudges.append(f"It's been {days_since} days — no pressure, just wanted to check in.")

    # 4. If recent mood was low, add a warm follow-up
    if checkins and len(checkins) >= 2:
        last_mood = checkins[-1].get("mood", 5)
        if last_mood <= 3:
            nudges.append("Last time you were having a tough day. How are things now?")

    return {
        "nudge": nudges[0],  # Primary nudge
        "extra": nudges[1] if len(nudges) > 1 else None,
        "stage": stage,
        "stage_display": STAGE_DISPLAY.get(stage, stage),
        "days_since_checkin": days_since,
        "days_in_stage": days_in_stage,
        "is_anticipatory": anticipatory is not None,
        "checked_in_today": False,
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
    "chat_response_latency": "Seconds between Melod-AI responding and user typing",
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
@app.post("/passive-signals")
async def receive_passive_signals_endpoint(batch: PassiveSignalBatch):
    """Receive a batch of passive behavioural signals from the patient app.

    These are collected silently during normal app usage — no extra input from patient.
    Each signal is tagged with patient_id, treatment stage, and timestamp for
    prospective training dataset construction.

    After storing raw signals, maps derived_features into the clinical construct
    analyser (signal_integration.analyze_passive_signals) and persists a
    phenotype snapshot to Firebase for longitudinal tracking.
    """
    if batch.patient_id not in patients_db:
        raise HTTPException(status_code=404, detail="Patient not found")

    pid = batch.patient_id
    patient = patients_db[pid]
    patient["last_active"] = utc_iso()

    # ── 1. Store raw signals in memory + Firebase ──
    stored = []
    for signal in batch.signals:
        record = {
            "signal_type": signal.get("signal_type", "unknown"),
            "value": signal.get("value"),
            "timestamp": signal.get("timestamp", utc_iso()),
            "treatment_stage": patient["treatment_stage"],
            "cycle_number": patient["cycle_number"],
            "metadata": signal.get("metadata", {}),
        }
        _sync_passive_signal(pid, record)
        stored.append(record)

    _sync_passive_batch(pid, stored)

    # ── 2. Map frontend derived_features → analyser format ──
    df = batch.derived_features or {}
    sm = batch.session_metadata or {}

    # Build the passive_data dict that analyze_passive_signals expects
    passive_data = {}

    # Typing signals
    if df.get("typing_speed_mean_ms") is not None or df.get("deletion_ratio") is not None:
        passive_data["typing"] = {
            "mean_iki_ms": df.get("typing_speed_mean_ms", 0),
            "deletion_ratio": df.get("deletion_ratio", 0),
            "composition_time_ms": df.get("composition_time_mean_ms", 0),
        }

    # Touch signals
    if df.get("touch_velocity_mean") is not None:
        passive_data["touch"] = {
            "velocity": df.get("touch_velocity_mean", 0),
            "pressure": df.get("touch_pressure_mean", 0),
        }

    # Scroll signals
    if df.get("scroll_velocity_mean") is not None:
        passive_data["scroll"] = {
            "velocity_peaks": df.get("scroll_velocity_max", 0),
            "direction_changes": df.get("scroll_direction_changes", 0),
        }

    # Content signals (aggregate from message_sent events in buffer)
    total_neg = 0
    total_unc = 0
    total_words = 0
    for sig in batch.signals:
        if sig.get("signal_type") == "message_sent":
            meta = sig.get("metadata", {})
            total_neg += meta.get("negative_word_count", 0)
            total_unc += meta.get("uncertainty_word_count", 0)
            total_words += meta.get("word_count", 0)
    if total_words > 0:
        passive_data["content"] = {
            "word_count": total_words,
            "negative_word_ratio": total_neg / total_words,
            "uncertainty_word_ratio": total_unc / total_words,
        }

    # Circadian signals
    hour = sm.get("hour_of_day", df.get("session_hour", 12))
    passive_data["circadian"] = {
        "hour": hour,
        "is_late_night": sm.get("is_late_night", False),
    }

    # Engagement signals
    passive_data["engagement"] = {
        "session_duration_ms": df.get("session_duration_ms", 0),
        "tab_switches": df.get("tab_switches", 0),
        "app_backgrounds": df.get("app_backgrounds", 0),
    }

    # ── 3. Initialise signal store if needed ──
    if pid not in patient_signal_store:
        patient_signal_store[pid] = {
            "signal_history": [],
            "check_in_history": [],
            "current_assessment": None,
            "escalation_level": "GREEN",
            "human_escalation_requested": False,
            "human_escalation_at": None,
            "baseline_established": False,
            "session_count": 0,
            "last_updated": datetime.now(),
        }

    store = patient_signal_store[pid]
    store["last_passive_data"] = passive_data
    store["signal_history"].append(passive_data)
    store["signal_history"] = store["signal_history"][-50:]
    store["session_count"] += 1
    store["last_updated"] = utc_now()

    # Sync check-in history from checkins_db into signal store
    recent_cis = checkins_db.get(pid, [])[-10:]
    if recent_cis:
        store["check_in_history"] = recent_cis

    # ── 4. Run construct analysis ──
    assessment = analyze_passive_signals(pid, passive_data, store)
    store["current_assessment"] = assessment
    store["escalation_level"] = assessment["escalation_level"]

    # ── 5. Persist phenotype snapshot to Firebase ──
    latest_ci = recent_cis[-1] if recent_cis else None
    snapshot = {
        "timestamp": utc_iso(),
        "escalation_level": assessment["escalation_level"],
        "constructs": assessment.get("constructs", {}),
        "flags": assessment.get("flags", []),
        "derived_features": {
            "typing_speed_ms": df.get("typing_speed_mean_ms"),
            "deletion_ratio": df.get("deletion_ratio"),
            "composition_time_ms": df.get("composition_time_mean_ms"),
            "touch_velocity": df.get("touch_velocity_mean"),
            "scroll_velocity": df.get("scroll_velocity_mean"),
            "session_duration_ms": df.get("session_duration_ms"),
            "session_hour": hour,
            "is_late_night": passive_data["circadian"].get("is_late_night", False),
            "message_count": df.get("total_messages_sent", 0),
            "message_length_mean": df.get("message_length_mean"),
        },
        "checkin": {
            "mood": latest_ci.get("mood") if latest_ci else None,
            "anxiety": latest_ci.get("anxiety") if latest_ci else None,
            "loneliness": latest_ci.get("loneliness") if latest_ci else None,
            "uncertainty": latest_ci.get("uncertainty") if latest_ci else None,
            "hope": latest_ci.get("hope") if latest_ci else None,
        } if latest_ci else None,
        "session_count": store["session_count"],
        "baseline_established": store.get("baseline_established", False),
    }
    firebase_db.save_phenotype_snapshot(pid, snapshot)

    logger.info(f"Stored {len(stored)} signals + phenotype snapshot for patient {pid} "
                f"[escalation={assessment['escalation_level']}]")

    return {
        "stored": len(stored),
        "patient_id": pid,
        "escalation_level": assessment["escalation_level"],
        "timestamp": utc_iso(),
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
