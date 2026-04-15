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
from nice_ng257_evidence import match_nice_evidence
import os
import json
import uuid
import hashlib
import logging
import io
import pdfplumber
from datetime import datetime, timedelta, date, timezone
from typing import Optional


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    """Return ISO 8601 timestamp with Z suffix for consistent frontend parsing."""
    return utc_now().isoformat()
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Header, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field
import anthropic
import asyncio


# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ivf-companion")

# ── Constants ────────────────────────────────────────────────────────
SONNET_MODEL = "claude-sonnet-4-20250514"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ── Pre-IVF Clearance Checklist ─────────────────────────────────────
PRE_IVF_CHECKLIST = {
    "mandatory": [
        {"id": "hiv", "label": "HIV 1 & 2 Ab/Ag", "category": "Infectious Serology", "partner": "both"},
        {"id": "hep_b_sag", "label": "Hepatitis B Surface Antigen (HBsAg)", "category": "Infectious Serology", "partner": "both"},
        {"id": "hep_b_core", "label": "Anti-HBc (if required)", "category": "Infectious Serology", "partner": "both"},
        {"id": "hep_c", "label": "Hepatitis C Ab", "category": "Infectious Serology", "partner": "both"},
        {"id": "syphilis", "label": "Syphilis Serology", "category": "Infectious Serology", "partner": "both"},
        {"id": "rubella", "label": "Rubella IgG", "category": "Infectious Serology", "partner": "female"},
        {"id": "varicella", "label": "Varicella IgG", "category": "Infectious Serology", "partner": "female"},
        {"id": "cst", "label": "Cervical Screening Test (CST)", "category": "Cervical Screening", "partner": "female"},
        {"id": "semen_analysis", "label": "Semen Analysis (WHO)", "category": "Semen Analysis", "partner": "male"},
        {"id": "blood_group", "label": "ABO Group & Rh Status", "category": "Blood Group", "partner": "female"},
        {"id": "antibody_screen", "label": "Antibody Screen", "category": "Blood Group", "partner": "female"},
        {"id": "amh", "label": "AMH", "category": "Baseline Hormones", "partner": "female"},
        {"id": "fsh", "label": "FSH", "category": "Baseline Hormones", "partner": "female"},
        {"id": "lh", "label": "LH", "category": "Baseline Hormones", "partner": "female"},
        {"id": "e2", "label": "Estradiol (E2)", "category": "Baseline Hormones", "partner": "female"},
        {"id": "tsh", "label": "TSH", "category": "Baseline Hormones", "partner": "female"},
        {"id": "prolactin", "label": "Prolactin", "category": "Baseline Hormones", "partner": "female"},
        {"id": "pelvic_us", "label": "Baseline Pelvic Ultrasound (AFC + Uterus)", "category": "Imaging", "partner": "female"},
        {"id": "carrier_cf", "label": "CF Carrier Screening", "category": "Genetic Screening", "partner": "both"},
        {"id": "carrier_sma", "label": "SMA Carrier Screening", "category": "Genetic Screening", "partner": "both"},
        {"id": "carrier_fragx", "label": "Fragile X (Female)", "category": "Genetic Screening", "partner": "female"},
        {"id": "consent_ivf", "label": "IVF Consent Forms", "category": "Administrative", "partner": "both"},
        {"id": "consent_genetics", "label": "Genetics Counselling Consent", "category": "Administrative", "partner": "both"},
        {"id": "id_verification", "label": "ID Verification", "category": "Administrative", "partner": "both"},
    ],
    "often_required": [
        {"id": "hsg", "label": "HSG / Tubal Assessment", "category": "Often Required", "partner": "female"},
        {"id": "hba1c", "label": "HbA1c", "category": "Often Required", "partner": "female"},
        {"id": "vitd", "label": "Vitamin D", "category": "Often Required", "partner": "female"},
        {"id": "dna_frag", "label": "DNA Fragmentation", "category": "Often Required", "partner": "male"},
        {"id": "karyotype", "label": "Karyotype", "category": "Often Required", "partner": "both"},
    ]
}

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


# ── Common IVF Topics Knowledge Base ─────────────────────────────────
# Each topic has: keywords (for matching), a plain-language summary,
# an analytical detail, an emotional framing, and practical tips.

COMMON_IVF_TOPICS = {
    "amh": {
        "keywords": ["amh", "anti-mullerian", "anti mullerian", "ovarian reserve", "egg reserve"],
        "name": "AMH (Anti-Müllerian Hormone)",
        "summary": "AMH is a blood test that estimates your remaining egg supply. It's a snapshot, not a destiny.",
        "analytical": "AMH is produced by granulosa cells of pre-antral and small antral follicles. Normal range is roughly 1.0–3.5 ng/mL (7–25 pmol/L). Low AMH (<1.0 ng/mL) suggests diminished ovarian reserve but does NOT predict egg quality. Many women with low AMH conceive. It helps your specialist choose the right stimulation dose.",
        "emotional": "Getting an AMH number can feel like getting a grade — but it's not a pass/fail. It's one piece of a much bigger puzzle. Women with 'low' numbers have babies every day, and a 'good' number doesn't guarantee anything either. Try not to let one number define your story.",
        "practical": "Ask your specialist: 'What does my AMH mean for my protocol?' AMH can fluctuate slightly cycle to cycle. Retest only if your specialist recommends it — obsessive retesting adds anxiety without changing the plan.",
    },
    "progesterone": {
        "keywords": ["progesterone", "pessaries", "crinone", "utrogestan", "endometrin", "PIO", "progesterone in oil"],
        "name": "Progesterone Support",
        "summary": "Progesterone helps prepare and maintain your uterine lining for embryo implantation. It's standard after transfer.",
        "analytical": "After egg retrieval, the corpus luteum may not produce sufficient progesterone for implantation. Supplementation (vaginal pessaries, gel, or intramuscular PIO) maintains the endometrial lining in its secretory phase. Typical start is 1-2 days after retrieval or as prescribed for FET. Continue until 8-12 weeks if pregnant.",
        "emotional": "The pessaries can feel like an annoying chore on top of everything else. The discharge and mess are normal — it doesn't mean the medication isn't working. Many women find a routine that makes it more bearable.",
        "practical": "Insert pessaries at consistent times. Lying down for 10-15 min after helps absorption. Panty liners are your friend. Side effects (bloating, breast tenderness, mood changes) mimic pregnancy symptoms — try not to symptom-spot based on these.",
    },
    "trigger_shot": {
        "keywords": ["trigger shot", "trigger injection", "ovidrel", "pregnyl", "hcg trigger", "lupron trigger"],
        "name": "Trigger Shot",
        "summary": "The trigger shot tells your eggs to complete their final maturation so they can be retrieved ~36 hours later.",
        "analytical": "The trigger (typically hCG or GnRH agonist) induces final oocyte maturation and loosens the cumulus-oocyte complex from the follicle wall. Timing is precise: retrieval is scheduled 34-36 hours post-trigger. hCG triggers (Ovidrel, Pregnyl) carry slightly more OHSS risk than agonist triggers (Lupron).",
        "emotional": "Trigger night can feel surreal — you've been building to this moment. The precise timing can feel stressful, but clinics are very experienced at scheduling this. It's okay to set multiple alarms.",
        "practical": "Set 2-3 alarms. Have your injection supplies laid out in advance. The timing must be exact — if you miss the window, call your clinic immediately (most have an after-hours line). Take a photo of the syringe/vial as a record.",
    },
    "egg_freezing": {
        "keywords": ["egg freezing", "freeze eggs", "fertility preservation", "social freezing", "oocyte cryopreservation"],
        "name": "Egg Freezing",
        "summary": "Egg freezing preserves your eggs at their current quality for future use. The stimulation process is similar to IVF.",
        "analytical": "Vitrification (flash-freezing) achieves >90% egg survival rates upon thawing. Ideal age for freezing is under 35, but benefit exists up to 38-40. Each cycle typically retrieves 8-15 eggs; most specialists suggest 15-20 mature eggs for a reasonable chance at one live birth. Success rates correlate strongly with age at freezing.",
        "emotional": "Egg freezing can feel empowering — you're taking control. But it can also bring up complicated feelings about timelines and partnerships. Both are completely valid.",
        "practical": "Budget for 1-2 cycles. Storage fees are annual (~$300-500/year in Australia). Medicare rebates apply for medical indications but not elective freezing. Ask about your clinic's thaw survival rates specifically.",
    },
    "embryo_grading": {
        "keywords": ["embryo grade", "embryo grading", "blastocyst grade", "day 5 grade", "AA", "AB", "BB", "4AA", "5AB", "hatching"],
        "name": "Embryo Grading",
        "summary": "Embryo grading describes how an embryo looks under the microscope. It's a rough guide, not a guarantee.",
        "analytical": "Day 5 blastocysts are graded on expansion (1-6), inner cell mass (A-C), and trophectoderm (A-C). A '4AA' means fully expanded, top-quality ICM and trophectoderm. However, a 'BB' embryo can absolutely become a healthy baby. Grading predicts implantation probability but not baby health. PGT-A tested euploid embryos have ~60-70% implantation rates regardless of morphology grade.",
        "emotional": "Getting your embryo report can feel like results day at school. Remember: embryologists see 'average-looking' embryos become beautiful babies all the time. The grade is not your baby's first test score.",
        "practical": "Ask your embryologist to explain YOUR grades specifically. Don't compare to others online — different clinics use slightly different scales. If doing PGT-A, the genetic result matters more than the visual grade.",
    },
    "tww_symptoms": {
        "keywords": ["tww", "two week wait", "2ww", "symptom spotting", "implantation", "cramping after transfer", "spotting after transfer"],
        "name": "The Two-Week Wait (TWW)",
        "summary": "The TWW is the period between embryo transfer and your pregnancy test. Symptom-spotting is universal but unreliable.",
        "analytical": "Implantation typically occurs 6-10 days post-ovulation (or 1-5 days post day-5 transfer). Progesterone supplementation causes symptoms identical to early pregnancy: breast tenderness, bloating, cramping, fatigue, mood swings. There is NO reliable way to distinguish medication side effects from pregnancy symptoms before the blood test.",
        "emotional": "The TWW might be the longest two weeks of your life. Every twinge becomes a Google search. This is completely normal. Try to be gentle with yourself — you cannot think or worry your way to a different outcome.",
        "practical": "Avoid home pregnancy tests before your clinic's blood test date — early testing causes more anxiety than answers. Distraction helps: plan activities, start a show, see friends. Light movement is fine. Your clinic will tell you what to avoid.",
    },
    "fsh": {
        "keywords": ["fsh", "follicle stimulating hormone", "day 3 fsh", "baseline fsh"],
        "name": "FSH (Follicle-Stimulating Hormone)",
        "summary": "FSH is a hormone that stimulates your ovaries. Your baseline FSH level helps assess ovarian function.",
        "analytical": "Day 2-3 FSH <10 IU/L is generally considered normal. Elevated FSH (>10-15) may suggest diminished ovarian reserve — the pituitary is working harder to stimulate the ovaries. FSH fluctuates cycle to cycle more than AMH. It's interpreted alongside estradiol, AMH, and AFC for the full picture.",
        "emotional": "Like AMH, an FSH number is just one data point. It can fluctuate. If yours is elevated, it doesn't close doors — it helps your specialist choose the best approach for you.",
        "practical": "FSH is drawn on cycle day 2-3 along with estradiol. If your FSH is elevated, ask about AMH and AFC for a more complete picture. Some clinics use FSH to adjust stimulation doses.",
    },
    "follicle_count": {
        "keywords": ["follicle count", "afc", "antral follicle", "how many follicles", "follicle scan"],
        "name": "Antral Follicle Count (AFC)",
        "summary": "AFC is the number of small resting follicles seen on ultrasound. It predicts how your ovaries may respond to stimulation.",
        "analytical": "AFC is measured via transvaginal ultrasound on day 2-5. Normal AFC is 10-20 total (both ovaries). <6 suggests low reserve; >20 suggests possible PCOS and higher OHSS risk. AFC combined with AMH gives the most accurate prediction of stimulation response. Not every follicle will produce a mature egg.",
        "emotional": "Counting follicles can feel like counting chances. But follicle count tells you about quantity potential, not quality. Some women with fewer follicles get excellent quality eggs.",
        "practical": "Don't compare your count to others — everyone's baseline is different. Your specialist uses AFC to choose your medication dose. During stimulation, not all follicles grow at the same rate — that's normal.",
    },
    "icsi_vs_ivf": {
        "keywords": ["icsi", "icsi vs ivf", "conventional ivf", "intracytoplasmic", "sperm injection"],
        "name": "ICSI vs Conventional IVF",
        "summary": "In conventional IVF, sperm and eggs are mixed together. In ICSI, a single sperm is injected directly into each egg.",
        "analytical": "ICSI is recommended for male factor infertility (low count/motility/morphology), previous fertilisation failure, PGT-A cycles, or frozen eggs. Fertilisation rates are similar (~70-80%) when appropriately indicated. ICSI does not improve outcomes over conventional IVF when sperm parameters are normal. Some clinics default to ICSI for all cycles.",
        "emotional": "If your clinic recommends ICSI, it's because they want to give your eggs the best chance at fertilisation. It's a very routine procedure — embryologists do this all day, every day.",
        "practical": "Ask your specialist why they're recommending ICSI vs conventional for your situation. Cost may differ. If using ICSI, the lab selects the best-looking sperm for each egg.",
    },
    "pgt": {
        "keywords": ["pgt", "pgs", "pgt-a", "genetic testing", "preimplantation", "euploid", "aneuploid", "mosaic"],
        "name": "PGT-A (Preimplantation Genetic Testing)",
        "summary": "PGT-A tests embryos for the correct number of chromosomes before transfer, aiming to improve transfer success rates.",
        "analytical": "PGT-A biopsies 5-10 trophectoderm cells from day 5-7 blastocysts. Euploid (normal) embryos have ~60-70% implantation rates. Aneuploidy rate increases sharply after age 37. Mosaic results (mix of normal/abnormal cells) are increasingly being considered for transfer. The test does not guarantee a healthy pregnancy — it improves odds per transfer.",
        "emotional": "Waiting for PGT results adds another layer of waiting to an already difficult process. Some embryos that looked great won't pass, and that's a real loss. But the information helps you make informed decisions about which embryo to transfer.",
        "practical": "Results take 1-2 weeks. Ask about your clinic's mosaic embryo policy. PGT-A adds ~$3,000-5,000 per cycle. Consider it especially if you're 37+, have had recurrent loss, or have limited transfer attempts.",
    },
    "endometriosis": {
        "keywords": ["endometriosis", "endo", "endometrioma", "chocolate cyst", "adenomyosis"],
        "name": "Endometriosis & Fertility",
        "summary": "Endometriosis can affect fertility through inflammation, adhesions, and sometimes reduced egg quality, but many women with endo conceive with treatment.",
        "analytical": "Endometriosis is staged I-IV. Even mild endo (I-II) can reduce fertility via inflammatory factors in peritoneal fluid. Endometriomas >4cm may warrant drainage before IVF. Surgery can improve natural conception rates for mild-moderate endo but evidence is mixed for IVF outcomes. AMH may be lower in women with bilateral endometriomas.",
        "emotional": "Living with endo AND doing IVF is a double load — the pain, the treatments, the uncertainty. You deserve extra gentleness with yourself. Your body has been through a lot.",
        "practical": "Discuss with your specialist whether surgical treatment before IVF is recommended for your specific situation. Some protocols include 2-3 months of GnRH agonist suppression before stimulation. Keep a pain diary to track patterns.",
    },
    "pcos": {
        "keywords": ["pcos", "polycystic", "metformin", "insulin resistance", "irregular periods", "anovulation"],
        "name": "PCOS & Fertility Treatment",
        "summary": "PCOS is common and very treatable. Women with PCOS often respond strongly to stimulation medications.",
        "analytical": "PCOS affects 8-13% of women. In IVF, PCOS patients typically produce more eggs but may have higher aneuploidy rates. OHSS risk is elevated — antagonist protocols with agonist triggers are preferred. Metformin may improve egg quality. Letrozole is first-line for ovulation induction before moving to IVF.",
        "emotional": "PCOS can feel like your body is working against you. But in the IVF world, a strong response to medication is actually an advantage — your ovaries are responsive. Your specialist will manage the stimulation carefully.",
        "practical": "Ask about OHSS prevention strategies (antagonist protocol, agonist trigger, freeze-all). Low-GI diet and moderate exercise can help with insulin resistance. Metformin may be recommended alongside IVF medications.",
    },
    "male_factor": {
        "keywords": ["male factor", "sperm count", "motility", "morphology", "low sperm", "azoospermia", "varicocele", "sperm analysis"],
        "name": "Male Factor Infertility",
        "summary": "Male factor contributes to about 40-50% of infertility cases. ICSI has transformed outcomes for many couples.",
        "analytical": "WHO normal values: count >15M/mL, motility >40%, morphology >4% (strict criteria). Mild-moderate male factor is effectively treated with ICSI. Severe cases (cryptozoospermia, azoospermia) may require surgical sperm retrieval (TESA/micro-TESE). Lifestyle factors (heat, smoking, alcohol, supplements) can improve parameters over 2-3 months.",
        "emotional": "Male factor infertility affects both partners emotionally. For the male partner, it can feel deeply personal. For the couple, it helps to remember this is a medical condition, not a personal failing.",
        "practical": "A repeat semen analysis is standard before making treatment decisions. 3 months of lifestyle optimisation can improve results. Ask about DNA fragmentation testing if standard parameters are borderline.",
    },
    "clexane": {
        "keywords": ["clexane", "enoxaparin", "blood thinner", "clotting", "thrombophilia", "heparin", "blood clotting"],
        "name": "Clexane (Enoxaparin)",
        "summary": "Clexane is a blood thinner sometimes prescribed during IVF to improve blood flow to the uterus and reduce clotting risk.",
        "analytical": "Clexane (enoxaparin) is a low-molecular-weight heparin. It's prescribed for: known thrombophilia, recurrent implantation failure, recurrent miscarriage, or antiphospholipid syndrome. Typical dose is 20-40mg daily via subcutaneous injection. Evidence for routine use in IVF without specific indication is limited.",
        "emotional": "Adding another injection to the mix can feel overwhelming. The bruising at injection sites is normal and doesn't mean anything is wrong. Many women find the belly is less painful than the thigh.",
        "practical": "Rotate injection sites. Ice the area before injecting to reduce bruising. Don't rub after. Take it at the same time daily. Tell your dentist and any other doctors you're on blood thinners. Stop as directed before any procedures.",
    },
    "ivf_process": {
        "keywords": ["ivf process", "how does ivf work", "ivf steps", "ivf cycle", "what happens in ivf", "ivf overview"],
        "name": "The IVF Process Overview",
        "summary": "IVF involves stimulating your ovaries, collecting eggs, fertilising them in the lab, growing embryos, and transferring the best one back.",
        "analytical": "A standard IVF cycle: (1) Ovarian stimulation 8-14 days with gonadotropins, (2) Trigger shot when follicles reach ~18-20mm, (3) Egg retrieval under sedation 36h post-trigger, (4) Fertilisation (conventional or ICSI), (5) Embryo culture to day 3-6, (6) Fresh transfer or freeze-all, (7) Luteal support, (8) Pregnancy test ~14 days post-retrieval. Total timeline: ~4-6 weeks per cycle.",
        "emotional": "Starting IVF can feel like stepping onto a conveyor belt. But remember — you can ask questions at every step, take breaks if you need them, and advocate for yourself. You're not just a patient number.",
        "practical": "Plan for flexibility at work around monitoring appointments (usually mornings) and retrieval day. You'll need 1-2 days off for retrieval. Start a medication organiser. Ask for a written schedule from your clinic.",
    },
    "fresh_vs_frozen": {
        "keywords": ["fresh transfer", "frozen transfer", "fet", "freeze all", "fresh vs frozen", "frozen embryo"],
        "name": "Fresh vs Frozen Embryo Transfer",
        "summary": "Frozen transfers (FET) are now as successful as — and sometimes better than — fresh transfers.",
        "analytical": "Freeze-all strategies have increased due to improved vitrification. FET allows the uterine lining to recover from stimulation, potentially improving receptivity. OHSS risk is eliminated with freeze-all. Success rates for FET are comparable to or slightly better than fresh transfers in many studies. Medicated FET uses estrogen + progesterone; natural FET tracks ovulation.",
        "emotional": "Being told to 'freeze all' when you were hoping for a fresh transfer can feel like a delay. But it's usually because your body needs time to recover, and that patience often pays off with better outcomes.",
        "practical": "FET typically happens 1-2 months after retrieval. Ask whether your clinic recommends medicated or natural FET for your situation. The FET process itself is much simpler — no sedation needed, feels similar to a Pap smear.",
    },
    "miscarriage_info": {
        "keywords": ["miscarriage", "pregnancy loss", "missed miscarriage", "early loss", "recurrent loss"],
        "name": "Understanding Pregnancy Loss",
        "summary": "Miscarriage after IVF is heartbreaking but not uncommon. It is not your fault.",
        "analytical": "Miscarriage rate after IVF is ~15-25%, similar to natural conception. Most are due to chromosomal abnormalities in the embryo. Recurrent loss (3+) warrants investigation: karyotyping, thrombophilia screen, uterine assessment. PGT-A can reduce miscarriage risk in subsequent cycles by selecting euploid embryos.",
        "emotional": "A miscarriage after everything it took to get there is devastating. The grief is real, whether it happened at 6 weeks or 12. You're allowed to mourn. You're allowed to be angry. And whenever you're ready, you're allowed to try again.",
        "practical": "Allow yourself time to grieve before making decisions about next steps. Ask your specialist about investigations before your next cycle. Many clinics offer counselling — consider it even if you think you're 'fine'.",
    },
    "chemical_pregnancy": {
        "keywords": ["chemical pregnancy", "biochemical pregnancy", "early positive then negative", "faint line then period"],
        "name": "Chemical Pregnancy",
        "summary": "A chemical pregnancy is a very early loss where hCG was briefly detected but didn't progress. It IS a real loss.",
        "analytical": "Chemical pregnancies account for up to 50-75% of all early losses. hCG rises briefly (often <100) then declines. In IVF, they're detected more often because of early blood testing. A chemical pregnancy confirms that implantation occurred, which some specialists view as a positive prognostic sign for future cycles.",
        "emotional": "A chemical pregnancy can feel like a cruel trick — hope followed immediately by loss. Some people are told 'at least it implanted' but that doesn't help when you're grieving. Your feelings about this are valid, whatever they are.",
        "practical": "Most clinics proceed to the next cycle after one normal period. No special investigations are typically needed after a single chemical pregnancy. If it happens repeatedly, ask about endometrial receptivity testing (ERA) or adjusted luteal support.",
    },
    "ohss": {
        "keywords": ["ohss", "ovarian hyperstimulation", "bloating after retrieval", "swollen ovaries", "fluid retention"],
        "name": "OHSS (Ovarian Hyperstimulation Syndrome)",
        "summary": "OHSS is when ovaries over-respond to stimulation medications. Mild OHSS is common; severe OHSS is rare and manageable.",
        "analytical": "Mild OHSS (bloating, mild pain) affects ~20-30% of cycles. Moderate-severe OHSS (<5%) involves significant fluid shifts, weight gain >1kg/day, and rarely requires hospitalisation. Risk factors: PCOS, high AFC, high estradiol, hCG trigger. Prevention: antagonist protocol, agonist trigger, freeze-all, cabergoline.",
        "emotional": "Feeling bloated and uncomfortable after retrieval is incredibly common. If it gets worse rather than better, don't push through — call your clinic. You're not being dramatic; OHSS is a real medical condition that deserves attention.",
        "practical": "Monitor: weigh yourself daily, measure waist circumference, track fluid intake/output. Drink electrolyte drinks (Hydralyte). Eat salty, high-protein foods. Call your clinic if: weight gain >1kg/day, severe bloating, difficulty breathing, reduced urination, or vomiting.",
    },
    "natural_cycle": {
        "keywords": ["natural cycle", "mini ivf", "mild stimulation", "natural ivf", "modified natural"],
        "name": "Natural & Mini IVF",
        "summary": "Natural and mini IVF use little or no medication, collecting 1-3 eggs. It's gentler on the body but may need more cycles.",
        "analytical": "Natural IVF: no stimulation, retrieves 0-1 eggs per cycle. Modified natural: mild stimulation (low-dose FSH or letrozole) for 1-3 eggs. Success rates per cycle are lower than conventional IVF, but cumulative rates over multiple cycles can be comparable. Best suited for: low responders, personal preference, cost considerations.",
        "emotional": "Choosing a gentler approach can feel like taking care of yourself. Fewer medications, fewer side effects, less disruption. But it can also mean more cycles, and that requires patience and resilience.",
        "practical": "Discuss with your specialist whether natural/mini IVF suits your diagnosis. Costs per cycle are lower but you may need more cycles. Monitoring is still required. Cancellation rates are higher (if the single follicle doesn't develop).",
    },
}


def detect_education_intent(message: str, patient_style: str, conversation_history: list = None) -> dict:
    """Detect the education intent behind a patient's question.

    Returns:
        {
            "intent": "REASSURANCE_FIRST" | "EXPLAIN_FIRST" | "PRACTICAL_FIRST" | None,
            "matched_topic": str or None,  # key from COMMON_IVF_TOPICS
            "confidence": float,
        }
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
        "tell me it's okay", "tell me it will be okay", "please reassure",
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
        r_score += 1  # Bias toward reassurance for emotional communicators
    elif patient_style == "ANALYTICAL":
        e_score += 1  # Bias toward explanation for analytical communicators

    # Determine intent
    intent = None
    confidence = 0.0
    total = r_score + e_score + p_score

    if total > 0:
        scores = {"REASSURANCE_FIRST": r_score, "EXPLAIN_FIRST": e_score, "PRACTICAL_FIRST": p_score}
        intent = max(scores, key=scores.get)
        confidence = scores[intent] / total
    elif matched_topic:
        # If we matched a topic but no clear intent signals, use style
        if patient_style == "EMOTIONAL":
            intent = "REASSURANCE_FIRST"
            confidence = 0.5
        elif patient_style == "ANALYTICAL":
            intent = "EXPLAIN_FIRST"
            confidence = 0.5
        else:
            intent = None  # Will trigger the care fork question
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

DISTRESS_KEYWORDS = [
    "cannot stand", "can't stand", "cant stand", "can't do this", "cant do this",
    "terrified", "terrifying", "so scared", "really scared", "i am scared",
    "super stressed", "so stressed", "extremely stressed",
    "hopeless", "no hope", "giving up", "give up", "given up",
    "falling apart", "breaking down", "can't cope", "cant cope",
    "overwhelmed", "too much", "can't take", "cant take",
    "hate this", "hate myself", "worthless",
    "crying", "sobbing", "can't stop crying",
    "so alone", "nobody cares", "no one cares",
    "can't anymore", "cant anymore", "had enough",
    "i am terrified", "i'm terrified", "im terrified",
    "can't breathe", "panic", "panicking",
]
CRISIS_KEYWORDS = [
    "want to die", "don't want to live", "dont want to live", "end it all",
    "kill myself", "suicide", "self harm", "hurt myself",
    "no reason to live", "better off without me", "end my life",
]

TRIAGE_LABELS = {1: "emotional_distress", 2: "educational", 3: "clinical", 4: "crisis", 5: "social"}


def keyword_safety_check(message: str, triage_cat: int) -> tuple:
    """Override triage if keywords indicate distress/crisis that Haiku missed.
    Returns (category, keyword_trigger_or_None)."""
    msg = message.lower()
    for kw in CRISIS_KEYWORDS:
        if kw in msg:
            return 4, kw
    for kw in DISTRESS_KEYWORDS:
        if kw in msg:
            if triage_cat not in (1, 4):
                return 1, kw
            return triage_cat, kw
    return triage_cat, None


TRIAGE_PROMPT = """You are a triage classifier for an IVF patient support companion.
Classify the patient's message into ONE category. Reply with ONLY the number.

1 = EMOTIONAL DISTRESS — patient is expressing difficult emotions: sadness, fear, anxiety, hopelessness, frustration, anger, grief, feeling overwhelmed, crying, panic, or emotional pain. IMPORTANT: If there is ANY emotional pain or distress, classify as 1.
2 = EDUCATION — patient is asking a factual question about IVF, fertility treatment, medications, procedures, or their body
3 = SCREENING — patient is responding to a check-in or questionnaire prompt (e.g., rating mood, answering PHQ/GAD items)
4 = CRISIS — patient expresses suicidal ideation, self-harm, or hopelessness suggesting immediate danger to self
5 = SOCIAL — casual chat, greetings, logistics, or off-topic conversation

Err on the side of detecting distress. If unsure between 1 and 5, choose 1.
Reply ONLY with the number (1-5)."""

COMPANION_SYSTEM = """You are Melod-AI, a warm and knowledgeable AI companion supporting a patient through their IVF/ART journey.

CORE IDENTITY:
- You are a knowledgeable friend, NOT a therapist, NOT a doctor
- You ANSWER QUESTIONS directly with accurate fertility information in plain language
- You treat the patient as capable and resilient, not fragile
- You remember their story and reference it naturally

RESPONSE RULES:
1. If the patient asks a QUESTION about treatment, medications, procedures or their body, ANSWER IT with clear accurate information. Use plain language and helpful analogies.
2. If the patient wants PRACTICAL HELP, give them concrete useful information.
3. NEVER give the same generic response to different questions.
4. Keep responses 2-4 paragraphs. Be warm but substantive.

EMOTIONAL REGULATION FRAMEWORK (Gross Process Model):
When the patient expresses difficult emotions, follow this evidence-based sequence:

1. ACKNOWLEDGE (not amplify): Name what they seem to be feeling in one or two sentences. Do NOT repeat their distress back at length — that fuels rumination.

2. NORMALIZE (briefly): "Many patients feel this way at this stage" — a bridge, not a destination. One sentence, then move forward.

3. REAPPRAISE (your core move): Gently offer a different lens. Not toxic positivity — genuine cognitive reappraisal:
   - "What if we look at it this way..."
   - "One thing worth remembering is..."
   - "Something your doctor would probably point out is..."
   - Reframe uncertainty as openness rather than threat
   - Reframe waiting as the body doing its work, not passive suffering
   - If there is a clinical question underneath the emotion, ANSWERING it IS the reappraisal

4. REDIRECT TO ACTION: Offer something concrete:
   - A breathing exercise or grounding technique
   - A specific question to ask their nurse at the next appointment
   - Reviewing their medication schedule or upcoming steps
   - Writing down their thoughts in their journal

WHAT TO AVOID:
- Reflective listening loops that mirror negativity ("It sounds like you're really struggling... that must be so hard... I can see why you'd feel that way...") — this is rumination fuel
- More than 2 sentences of pure validation before moving forward
- Asking "how does that make you feel?" when they just told you
- Empty reassurance ("everything will be fine") — that is dismissal, not reappraisal
- Catastrophizing with them or agreeing their situation is hopeless
- Treating emotional and clinical messages as separate — they are often the same

WHAT TO DO:
- After acknowledging, move the conversation forward within 2-3 exchanges
- If the patient is stuck in a loop (repeating the same worry), gently name it: "I notice we keep coming back to this. That's your mind doing what minds do with uncertainty — let's try something different."
- Use their treatment stage to ground reappraisal in concrete reality
- If distress is severe (crisis-level), do NOT reappraise — activate safety protocol

WHAT YOU KNOW:
- IVF/ICSI procedures: stimulation protocols, egg retrieval, embryo culture, transfer, FET
- Medications: Gonal-F, Menopur, Cetrotide, Orgalutran, progesterone, trigger shots
- Conditions: endometriosis, PCOS, diminished ovarian reserve, male factor, unexplained
- Lab: AMH, FSH, AFC, embryo grading, blastocyst development, PGT-A
- Australian context: Medicare, PBS, clinic processes, referral pathways

EDUCATION APPROACH:
- Use plain language, not textbook terminology
- Use analogies: follicles as small fluid-filled pods, embryo transfer as a tiny passenger
- Always end educational answers with: Your specialist can give you specifics for your situation

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
        "title": "AMH Guide — Interactive Normogram",
        "description": "See where your AMH sits for your age, with percentile curves",
        "url": "https://fouksir.github.io/Fertool/amh-guide.html",
        "icon": "\U0001f4ca",
        "tags": ["amh", "ovarian reserve", "egg count", "anti-mullerian", "hormone levels", "amh level"],
    },
    "egg_freezing": {
        "title": "Egg Freezing Calculator",
        "description": "Explore success rates based on your age and number of eggs frozen",
        "url": "https://fouksir.github.io/Fertool/egg-freezing-calculator.html",
        "icon": "\u2744\ufe0f",
        "tags": ["egg freezing", "freeze", "cryopreservation", "oocyte", "fertility preservation", "social freezing"],
    },
    "endometriosis": {
        "title": "Endometriosis & Fertility",
        "description": "Understand how endometriosis affects fertility and your options",
        "url": "https://fouksir.github.io/Fertool/endometriosis-landing.html",
        "icon": "\U0001f52c",
        "tags": ["endometriosis", "endo", "adenomyosis", "chocolate cyst", "endometrioma"],
    },
    "fertility_assessment": {
        "title": "Fertility Assessment Tool",
        "description": "Interactive assessment to understand your fertility picture",
        "url": "https://fouksir.github.io/Fertool/fertility-assessment.html",
        "icon": "\U0001f4cb",
        "tags": ["assessment", "fertility check", "workup", "testing", "evaluation", "investigation"],
    },
    "fertool_search": {
        "title": "Search Fertool Knowledge Base",
        "description": "Search our clinical fertility database for detailed information",
        "url": "https://fouksir.github.io/Fertool/index.html",
        "icon": "\U0001f50d",
        "tags": ["fertool", "search", "lookup"],
    },
}


def match_fertool_cards(message: str, response_text: str, max_cards: int = 2) -> list[dict]:
    """Match patient message + AI response against Fertool card tags.

    Returns up to max_cards matching cards, sorted by relevance (number
    of tag hits). Only call this for triage category 2 (education).
    """
    combined = (message + " " + response_text).lower()
    scored = []
    for key, card in FERTOOL_CARDS.items():
        hits = sum(1 for tag in card["tags"] if tag in combined)
        if hits > 0:
            scored.append((hits, key, card))

    scored.sort(key=lambda x: -x[0])
    return [
        {"title": c["title"], "description": c["description"], "url": c["url"], "icon": c["icon"]}
        for _, _, c in scored[:max_cards]
    ]


def detect_fertool_inline_charts(message: str) -> list:
    """Detect if message should trigger inline Fertool charts (AMH normogram, egg freeze table)."""
    msg = message.lower()
    charts = []
    amh_kw = ['amh', 'anti-mullerian', 'anti mullerian', 'ovarian reserve', 'egg count',
              'egg supply', 'how many eggs do i have', 'egg reserve', 'diminished reserve',
              'low reserve', 'por', 'poor responder', 'pmol']
    if any(kw in msg for kw in amh_kw):
        charts.append('amh_normogram')
    freeze_kw = ['egg freezing', 'freeze my eggs', 'frozen eggs', 'thaw my eggs',
                 'warm my eggs', 'use my frozen', 'oocyte cryopreservation',
                 'fertility preservation', 'social freezing', 'elective freezing',
                 'how many eggs to freeze', 'eggs to live birth']
    if any(kw in msg for kw in freeze_kw):
        charts.append('egg_freeze_table')
    return charts


# ── ANZARD 2023 Infographic Charts ───────────────────────────────────
# Source: Kotevski DP et al. 2025. ART in Australia and New Zealand 2023. NPESU, UNSW Sydney.

ANZARD_CHARTS = {
    "age_outcomes": {
        "key": "age_outcomes",
        "title": "What are my chances by age?",
        "subtitle": "Live birth rate per initiated cycle, ANZARD 2023",
        "tags": ["chances", "success rate", "what are my chances", "how likely", "ivf success",
                 "live birth rate", "does age matter", "too old", "over 40", "over 35",
                 "chances at", "success at my age"],
    },
    "cumulative": {
        "key": "cumulative",
        "title": "Does persistence pay off?",
        "subtitle": "Cumulative live birth rate over multiple cycles",
        "tags": ["how many cycles", "how many rounds", "keep trying", "cumulative",
                 "multiple cycles", "first cycle", "second cycle", "didn't work",
                 "failed cycle", "should i try again", "persistence",
                 "chances over time", "stop trying"],
    },
    "fresh_vs_frozen": {
        "key": "fresh_vs_frozen",
        "title": "Fresh vs Frozen embryo transfers",
        "subtitle": "Autologous cycle outcomes, 2023",
        "tags": ["fresh vs frozen", "fresh or frozen", "freeze all", "freeze my embryos",
                 "frozen embryo transfer", "better fresh or frozen", "thaw cycle"],
    },
    "causes": {
        "key": "causes",
        "title": "Why people seek fertility treatment",
        "subtitle": "Cause of infertility, female-male couples, 2023",
        "tags": ["why infertile", "cause of infertility", "unexplained infertility",
                 "male factor", "is it me or my partner",
                 "why isn't it working", "common causes", "can't get pregnant"],
    },
    "baby_outcomes": {
        "key": "baby_outcomes",
        "title": "Healthy baby outcomes",
        "subtitle": "Over 20,000 babies born via ART in 2023",
        "tags": ["healthy baby", "ivf baby", "birth defects", "preterm",
                 "is ivf safe", "risks to baby", "baby outcomes",
                 "are ivf babies normal", "worried about baby"],
    },
    "trends": {
        "key": "trends",
        "title": "IVF success is improving over time",
        "subtitle": "Live birth rate per embryo transfer, 2019-2023",
        "tags": ["getting better", "improving", "over time", "better than before",
                 "has ivf improved", "advances", "new techniques"],
    },
    "egg_freezing_stats": {
        "key": "egg_freezing_stats",
        "title": "Egg freezing is surging",
        "subtitle": "Fertility preservation cycles, 2023",
        "tags": ["egg freezing", "freeze my eggs", "fertility preservation",
                 "social freezing", "should i freeze my eggs",
                 "what age to freeze", "is egg freezing worth it"],
    },
}


def match_anzard_charts(message: str, response_text: str, max_charts: int = 2) -> list[dict]:
    """Match patient message against ANZARD chart triggers.

    Uses message ONLY (not response text) to avoid false positives from
    Claude's own language triggering charts on unrelated topics.
    Applies exclusion filters so clinical-procedure/add-on questions
    don't trigger age_outcomes or other charts spuriously.
    """
    msg = message.lower()

    # ── Global exclusion: these topics should NEVER trigger ANY chart ──
    _NO_CHART_PATTERNS = [
        "pgt", "pgta", "pgs", "genetic testing", "embryo testing",
        "icsi", "intracytoplasmic",
        "scratch", "endometrial scratch",
        "era test", "emma", "alice", "receptivity",
        "immune", "intralipid", "ivig", "nk cell", "natural killer",
        "steroid", "prednisolone",
        "supplement", "coq10", "antioxidant", "vitamin",
        "dna fragmentation", "sperm dna",
        "orgalutran", "gonal", "menopur", "cetrotide", "synarel",
        "progesterone", "pessary", "crinone", "lubion",
        "hysteroscopy",
        "counsell", "therapist", "psycholog",
    ]
    if any(p in msg for p in _NO_CHART_PATTERNS):
        return []

    scored = []
    for key, chart in ANZARD_CHARTS.items():
        hits = 0
        has_phrase_match = False
        for tag in chart["tags"]:
            if tag in msg:
                hits += 2
                has_phrase_match = True
            else:
                tag_words = tag.split()
                partial = sum(1 for w in tag_words if len(w) >= 4 and w in msg)
                if partial > 0:
                    hits += partial

        # ── Per-chart tightening ──
        # age_outcomes: require BOTH an age indicator AND a chances/success indicator
        if key == "age_outcomes" and hits > 0:
            has_age = any(w in msg for w in [
                "age", "older", "younger", "35", "36", "37", "38", "39",
                "40", "41", "42", "over 3", "over 4", "too old",
            ])
            has_chances = any(w in msg for w in [
                "chance", "success", "rate", "likely", "odds", "birth rate",
                "how likely", "what are my", "does age", "affect",
                "work for me", "realistic",
            ])
            if not (has_age and has_chances):
                continue

        if hits >= 2 and has_phrase_match:
            scored.append((hits, key, chart))
    scored.sort(key=lambda x: -x[0])
    return [{"key": c["key"], "title": c["title"], "subtitle": c["subtitle"]} for _, _, c in scored[:max_charts]]


# ── In-Memory Patient Store (Phase 1 — will move to PostgreSQL) ──────

patients_db: dict = {}
conversations_db: dict = {}  # patient_id -> list of messages
checkins_db: dict = {}       # patient_id -> list of daily check-ins
screenings_db: dict = {}     # patient_id -> list of screening results
escalations_db: dict = {}    # patient_id -> list of escalation events
passive_signals_db: dict = {}  # patient_id -> list of passive behavioural signals
cycle_events_db: dict = {}  # {patient_id: [events]}
clinical_triggers_db: dict = {}  # {patient_id: [triggers]}
calendar_updates_db: dict = {}  # {patient_id: [updates]}
community_posts_db: list = []  # Global list of community posts
community_reactions_db: dict = {}  # {post_id: {patient_id: reaction_type}}


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
    if patient.get("name") or patient.get("patient_name"):
        ctx += f"- Name: {patient.get('name') or patient.get('patient_name', 'there')}\n"
    ctx += f"- Treatment stage: {STAGE_DISPLAY.get(patient.get('treatment_stage', 'consultation'), patient.get('treatment_stage', 'consultation'))}\n"
    ctx += f"- Cycle number: {patient.get('cycle_number', 1)}\n"
    if patient.get("partner_name"):
        ctx += f"- Partner: {patient['partner_name']}\n"
    if patient.get("clinic_name"):
        ctx += f"- Clinic: {patient['clinic_name']}\n"
    ctx += f"- Preferred tone: {patient.get('preferences', {}).get('tone', 'gentle')}\n"

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

    # Post-OPU context from Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            cycle = firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").get()
            if cycle and isinstance(cycle, dict):
                opu_sched = cycle.get("opu_schedule")
                if opu_sched and opu_sched.get("opu_date"):
                    ctx += f"\nPOST-OPU CONTEXT:\n"
                    ctx += f"- Egg collection date: {opu_sched['opu_date']}\n"
                    ctx += f"- Trigger drug: {opu_sched.get('trigger_drug', 'unknown')}\n"
                    # Latest patient-reported symptoms
                    reports = cycle.get("patient_reports", {})
                    if reports and isinstance(reports, dict):
                        latest_key = sorted(reports.keys())[-1] if reports else None
                        if latest_key:
                            r = reports[latest_key]
                            ctx += f"- Latest symptom report: pain {r.get('pain', '?')}/10, bloating {r.get('bloating', '?')}/10, nausea {r.get('nausea', '?')}/3\n"
                            if r.get("notes"):
                                ctx += f"- Patient note: \"{r['notes'][:100]}\"\n"
                            grade = r.get("calculated_grade", "unknown")
                            if grade in ("moderate", "severe", "critical"):
                                ctx += f"- OHSS grade: {grade.upper()} — recommend calling clinic\n"
                    comps = cycle.get("complications", {})
                    if comps and isinstance(comps, dict):
                        active = [k for k in ["ed_visit","excessive_pain","infection","torsion","bleeding","thromboembolic"] if comps.get(k)]
                        if active:
                            ctx += f"- ACTIVE COMPLICATIONS: {', '.join(active)}\n"
                            ctx += "- DO NOT try to diagnose or manage these — tell patient to call the clinic or go to ED immediately.\n"
    except Exception as e:
        logger.warning(f"Post-OPU context error for {patient_id}: {e}")

    return ctx


def build_education_context(patient_id: str) -> str:
    """Get relevant education topics for current stage."""
    patient = get_or_create_patient(patient_id)
    stage = patient.get("treatment_stage", "consultation")
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

    # Add community behavior flags
    community_flags = []
    sig_store = patient_signal_store.get(patient_id)
    if sig_store:
        sig_assessment = sig_store.get("current_assessment", {})
        for flag_name in ["SEEKING_CONNECTION", "LATE_NIGHT_COMMUNITY", "ANTICIPATORY_BROWSING"]:
            construct = sig_assessment.get("constructs", {}).get(flag_name, {})
            if construct.get("active"):
                community_flags.append(f"{flag_name}: {construct.get('signal', '')}")

    if community_flags:
        risk_flags.append(f"Community engagement: {'; '.join(community_flags)}")

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
    triage_label: Optional[str] = None  # emotional_distress, educational, clinical, crisis, social
    is_distress: bool = False
    is_crisis: bool = False
    suggested_education: Optional[list] = None
    fertool_cards: Optional[list] = None  # DEPRECATED — always None now
    fertool_inline_charts: Optional[list] = None  # Inline AMH normogram, egg freeze table
    one_word_checkin: Optional[dict] = None  # If message was mapped as a one-word check-in
    education_fork: Optional[str] = None  # Clarifying question for education queries
    anzard_charts: Optional[list] = None  # ANZARD 2023 infographic charts
    support_widgets: Optional[list] = None  # Clinical trigger support widgets
    clinical_triggers: Optional[list] = None  # Active trigger context
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
    support_widgets: Optional[list] = None

class ScreeningRequest(BaseModel):
    patient_id: str
    instrument: str  # "PHQ-2", "PHQ-9", "GAD-7", "FertiQoL"
    responses: list[int]

class ScreeningResponse(BaseModel):
    result: dict
    message: str
    escalation: Optional[dict] = None

class PHQ4Request(BaseModel):
    """PHQ-4 ultra-brief screening: 2 GAD-2 items + 2 PHQ-2 items, each scored 0-3."""
    q1: int = Field(ge=0, le=3)  # Nervous, anxious, on edge
    q2: int = Field(ge=0, le=3)  # Can't stop worrying
    q3: int = Field(ge=0, le=3)  # Feeling down, depressed, hopeless
    q4: int = Field(ge=0, le=3)  # Little interest or pleasure
    triggered_by: str = "daily"  # "daily" | "phenotype_alert"

class PHQ4Response(BaseModel):
    total: int
    anxiety_sub: int
    depression_sub: int
    severity: str  # normal | mild | moderate | severe
    flagged: bool = False

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
    patient_id: Optional[str] = None  # Firebase Auth UID — if provided, use this instead of random
    email: Optional[str] = None  # Store email for clinician dashboard
    phone: Optional[str] = None  # Patient phone number

class CommunityPostRequest(BaseModel):
    patient_id: str
    text: str
    anonymous: bool = True

class CommunityReactRequest(BaseModel):
    reaction: str  # "support", "same", "strength"
    patient_id: str

class OutcomeRequest(BaseModel):
    outcome_type: str  # "beta_hcg", "fertilisation", "pgt", "general"
    outcome_value: str  # "positive", "negative", or free text
    notes: Optional[str] = None

class OutcomeUpdateRequest(BaseModel):
    status: str  # "informed", "voicemail_left", "unreachable"
    call_notes: Optional[str] = None


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

@app.middleware("http")
async def audit_clinician_requests(request: Request, call_next):
    """Middleware: automatically audit-log all /clinician/ endpoint access."""
    response = await call_next(request)
    # Only audit clinician endpoints that succeeded
    if request.url.path.startswith("/clinician/") and response.status_code < 400:
        info = getattr(request.state, "clinician", None)
        if info:
            # Derive action name from method + path
            path = request.url.path
            method = request.method
            patient_id = None
            # Extract patient_id from path if present
            parts = path.split("/")
            if "patient" in parts:
                idx = parts.index("patient")
                if idx + 1 < len(parts):
                    patient_id = parts[idx + 1]
            # Build action name
            action = method.lower() + ":" + path.split("?")[0]
            # Map common paths to readable names
            action_map = {
                "get:/clinician/dashboard": "view_patient_list",
                "get:/clinician/patients-list": "view_patient_list",
                "get:/clinician/alerts": "view_alerts",
                "get:/clinician/outcomes/pending": "view_pending_outcomes",
                "get:/clinician/digest": "view_digest",
                "get:/clinician/audit-log": "view_audit_log",
            }
            if action in action_map:
                action = action_map[action]
            elif patient_id:
                # Patient-specific actions
                suffix = "/".join(parts[parts.index(patient_id) + 1:]) if parts.index(patient_id) + 1 < len(parts) else ""
                paction_map = {
                    ("get", ""): "view_patient_detail",
                    ("get", "summary"): "view_patient_detail",
                    ("get", "briefing"): "view_patient_detail",
                    ("get", "clearance"): "view_clearance",
                    ("get", "pronunciation"): "view_pronunciation",
                    ("get", "conversations"): "view_conversations",
                    ("get", "cycle"): "view_cycle",
                    ("post", "parse-labs"): "parse_labs",
                    ("post", "parse-document"): "parse_document",
                    ("post", "send-message"): "send_message",
                    ("post", "flag-topic"): "flag_topic",
                    ("post", "cycle"): "update_cycle",
                    ("post", "outcome"): "add_outcome",
                    ("post", "pronunciation"): "generate_pronunciation",
                    ("delete", ""): "delete_patient",
                }
                action = paction_map.get((method.lower(), suffix), method.lower() + "_" + suffix.replace("/", "_").replace("-", "_"))
            asyncio.create_task(_log_audit_safe(action, info, patient_id))
    return response

app.include_router(signal_router)
client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
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
    # Use Firebase Auth UID if provided, else generate random
    patient_id = req.patient_id if req.patient_id else str(uuid.uuid4())[:8]
    patient = get_or_create_patient(patient_id)
    patient["name"] = req.name
    patient["patient_name"] = req.name  # Also store as patient_name for dashboard
    patient["treatment_stage"] = req.treatment_stage
    patient["cycle_number"] = req.cycle_number
    patient["treatment_type"] = req.treatment_type
    patient["partner_name"] = req.partner_name
    patient["clinic_name"] = req.clinic_name
    if req.email:
        patient["email"] = req.email
    if req.phone:
        patient["phone"] = req.phone

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

    # Auto-generate pronunciation guide (fire-and-forget)
    async def _gen_pronunciation(pid, name):
        try:
            hr = client.messages.create(
                model=HAIKU_MODEL, max_tokens=200,
                messages=[{"role": "user", "content": f"You are a pronunciation guide for an Australian English speaker. Given the name below, provide:\n1. Phonetic pronunciation with syllable breaks and stress markers (CAPS for stressed syllable)\n2. A 'sounds like' approximation using common English words\nName: {name}\nRespond in this exact format:\nPhonetic: [phonetic]\nSounds like: [approximation]"}]
            )
            resp_text = hr.content[0].text
            phonetic = sounds_like = ""
            for line in resp_text.strip().split("\n"):
                if line.lower().startswith("phonetic:"): phonetic = line.split(":", 1)[1].strip()
                elif line.lower().startswith("sounds like:"): sounds_like = line.split(":", 1)[1].strip()
            from firebase_db import _fb_ref
            if _fb_ref:
                _fb_ref.child("patients").child(pid).child("pronunciation").update({
                    "phonetic": phonetic, "sounds_like": sounds_like,
                    "source": "haiku_auto", "generated_at": utc_iso()
                })
        except Exception as e:
            logging.warning(f"Auto-pronunciation failed for {pid}: {e}")
    asyncio.create_task(_gen_pronunciation(patient_id, req.name))

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

    return {
        "greeting": greeting,
        "soft_spot": soft_spot,
        "micro_reflection": micro,
        "needs_reflection": needs_reflection,
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


# ── Clinical Trigger Engine ─────────────────────────────────────────

def evaluate_clinical_triggers(patient_id: str) -> list:
    """Evaluate 6 clinical trigger rules based on mood + calendar + stage."""
    triggers = []
    patient = patients_db.get(patient_id, {})
    stage = patient.get("treatment_stage", "")
    checkins = checkins_db.get(patient_id, [])
    recent = checkins[-5:] if checkins else []
    latest = recent[-1] if recent else {}
    conversations = conversations_db.get(patient_id, [])
    events = cycle_events_db.get(patient_id, [])
    store = patient_signal_store.get(patient_id, {})
    now = utc_now()

    # Rule 1 — PRE-PROCEDURE ANXIETY
    procedure_types = ['retrieval', 'transfer', 'iui', 'scan']
    for evt in events:
        try:
            evt_date = datetime.fromisoformat(evt.get('date', '')[:10])
        except Exception:
            continue
        hours_until = (evt_date - now.replace(tzinfo=None)).total_seconds() / 3600
        if 0 < hours_until <= 48 and evt.get('type') in procedure_types:
            anxiety = latest.get('anxiety', 5)
            mood = latest.get('mood', 5)
            if anxiety >= 5 or mood < 6:
                triggers.append({
                    "rule": "pre_procedure_anxiety",
                    "event": f"{evt.get('type')} on {evt.get('date')}",
                    "mood_score": mood,
                    "anxiety_score": anxiety,
                    "support_widget": "pre_procedure_checklist",
                    "clinician_flag": f"Patient anxious ahead of {evt.get('type')} on {evt.get('date')}",
                    "priority": "moderate"
                })
                break

    # Rule 2 — POST-RESULT VULNERABILITY
    for evt in events:
        try:
            evt_date = datetime.fromisoformat(evt.get('date', '')[:10])
        except Exception:
            continue
        hours_since = (now.replace(tzinfo=None) - evt_date).total_seconds() / 3600
        if 0 < hours_since <= 72 and evt.get('type') == 'result':
            mood = latest.get('mood', 5)
            loneliness = latest.get('loneliness', 5)
            if mood < 4 or loneliness > 7:
                triggers.append({
                    "rule": "post_result_vulnerability",
                    "event": f"result on {evt.get('date')}",
                    "mood_score": mood,
                    "loneliness_score": loneliness,
                    "support_widget": "post_result_care",
                    "clinician_flag": "Patient struggling after result day — consider outreach",
                    "priority": "high"
                })
                break

    # Rule 3 — STIMULATION FATIGUE
    if stage == 'stimulation':
        injection_events = [e for e in events if e.get('type') == 'injection']
        if len(injection_events) >= 8:
            if len(recent) >= 3:
                moods = [c.get('mood', 5) for c in recent[-3:]]
                if moods[-1] < moods[0]:  # declining
                    triggers.append({
                        "rule": "stim_fatigue",
                        "day_count": len(injection_events),
                        "mood_trend": moods,
                        "support_widget": "stim_fatigue_support",
                        "clinician_flag": f"Stim fatigue detected — mood declining since day {len(injection_events)-2}",
                        "priority": "moderate"
                    })

    # Rule 4 — TWW SPIRAL
    tww_stages = ['early_tww', 'late_tww']
    if stage in tww_stages:
        anxiety = latest.get('anxiety', 5)
        mood = latest.get('mood', 5)
        assessment = store.get('current_assessment', {})
        flags = assessment.get('flags', [])
        has_hyper = 'HYPER_ENGAGEMENT' in str(flags)
        late_sessions = sum(1 for h in store.get('signal_history', [])[-7:]
                          if h.get('circadian', {}).get('hour', 12) >= 23 or h.get('circadian', {}).get('hour', 12) <= 4)
        # Lower threshold — TWW is inherently stressful; anxiety>=6 OR mood<5 suffices
        if anxiety >= 6 or mood < 5 or has_hyper or late_sessions > 2:
            triggers.append({
                "rule": "tww_spiral",
                "anxiety_score": anxiety,
                "late_sessions": late_sessions,
                "support_widget": "tww_survival_kit",
                "clinician_flag": "TWW anxiety elevated — consider reassurance",
                "priority": "moderate"
            })

    # Rule 5 — DISENGAGEMENT WARNING
    if checkins and conversations:
        last_checkin_date = checkins[-1].get('date', '')
        last_conv_date = conversations[-1].get('timestamp', '')
        try:
            last_activity = max(
                datetime.fromisoformat(last_checkin_date.replace('Z', '+00:00')) if last_checkin_date else datetime.min.replace(tzinfo=timezone.utc),
                datetime.fromisoformat(last_conv_date.replace('Z', '+00:00')) if last_conv_date else datetime.min.replace(tzinfo=timezone.utc)
            )
            days_silent = (now - last_activity).days
        except Exception:
            days_silent = 0

        last_mood = latest.get('mood', 5)
        if days_silent >= 3 and last_mood < 5:
            triggers.append({
                "rule": "disengagement_warning",
                "days_silent": days_silent,
                "last_mood": last_mood,
                "support_widget": "gentle_nudge",
                "clinician_flag": "Patient disengaged after low mood — dropout risk",
                "priority": "high"
            })

    # Rule 6 — MEDICATION CONFUSION
    med_keywords = ['medication', 'medicine', 'dose', 'dosage', 'injection', 'gonal', 'menopur', 'cetrotide', 'progesterone', 'estrogen', 'clomid', 'letrozole']
    recent_user_msgs = [m for m in conversations[-20:] if m.get('role') == 'user'] if conversations else []
    three_days_ago = (now - timedelta(days=3)).isoformat()
    med_msgs = [m for m in recent_user_msgs
                if m.get('timestamp', '') >= three_days_ago
                and any(kw in m.get('content', '').lower() for kw in med_keywords)]
    if len(med_msgs) >= 2:
        triggers.append({
            "rule": "medication_confusion",
            "med_messages_count": len(med_msgs),
            "support_widget": "medication_education",
            "clinician_flag": "Medication confusion persists — may need in-person explanation",
            "priority": "moderate"
        })

    # Store triggers in Firebase
    if triggers:
        trigger_record = {
            "timestamp": utc_iso(),
            "triggers": triggers,
            "patient_stage": stage,
        }
        clinical_triggers_db.setdefault(patient_id, []).append(trigger_record)
        try:
            firebase_db.save_clinical_trigger(patient_id, trigger_record)
        except Exception:
            pass

    return triggers


def _is_data_question(msg: str) -> bool:
    """Check if message is explicitly asking for medical data/statistics."""
    m = msg.lower()
    data_signals = [
        "what are my chances", "success rate", "how likely", "statistics",
        "how many cycles", "does age matter", "fresh vs frozen", "fresh or frozen",
        "cause of infertility", "ivf safe", "baby outcomes", "egg freezing",
        "freeze my eggs", "ivf improving", "how many rounds",
        "what percentage", "data", "numbers", "evidence"
    ]
    return any(s in m for s in data_signals)


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
        triage_category = 1  # Default to emotional — safer than social

    # Keyword safety net — override if Haiku missed obvious distress
    triage_category, kw_trigger = keyword_safety_check(req.message, triage_category)
    triage_label = TRIAGE_LABELS.get(triage_category, "emotional_distress")
    is_distress = triage_category in (1, 4)
    is_crisis = triage_category == 4

    if kw_trigger:
        logger.info(f"[{query_id}] Triage OVERRIDE: category={triage_category} (keyword: '{kw_trigger}') for patient={req.patient_id}")
    else:
        logger.info(f"[{query_id}] Triage: category={triage_category} ({triage_label}) for patient={req.patient_id}")

    # Update the last user message with triage info (for dashboard conversations)
    convs = conversations_db.get(req.patient_id, [])
    if convs and convs[-1].get("role") == "user":
        convs[-1]["triage"] = triage_category
        convs[-1]["triage_label"] = triage_label
        convs[-1]["is_distress"] = is_distress
        convs[-1]["is_crisis"] = is_crisis

    # ── Step 2: Safety check (parallel with response generation) ──
    context_msgs = get_conversation_context(req.patient_id, last_n=10)
    context_str = "\n".join([f"{m['role']}: {m['content']}" for m in context_msgs[-6:]])

    escalation = None

    # Crisis or distress escalation
    if triage_category == 4:
        escalation = {
            "level": "RED",
            "reason": "Crisis-level content detected" + (f" (keyword: {kw_trigger})" if kw_trigger else ""),
            "signals": ["triage_crisis_classification"],
            "timestamp": utc_iso(),
        }
        # Save alert to Firebase for dashboard
        try:
            if firebase_db and firebase_db._fb_ref:
                firebase_db._fb_ref.child("alerts").push({
                    "patient_id": req.patient_id, "level": "RED",
                    "message": req.message[:200], "triage_label": "crisis",
                    "timestamp": utc_iso(), "acknowledged": False,
                })
        except Exception:
            pass
    elif triage_category == 1 and kw_trigger:
        escalation = {
            "level": "AMBER",
            "reason": f"Emotional distress detected (keyword: {kw_trigger})",
            "signals": ["keyword_distress_detection"],
            "timestamp": utc_iso(),
        }
        # Save alert to Firebase
        try:
            if firebase_db and firebase_db._fb_ref:
                firebase_db._fb_ref.child("alerts").push({
                    "patient_id": req.patient_id, "level": "AMBER",
                    "message": req.message[:200], "triage_label": "emotional_distress",
                    "timestamp": utc_iso(), "acknowledged": False,
                })
        except Exception:
            pass
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
            # Topic matched but no clear intent — ask clarifying care fork
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
        rag_context = retrieve_education(req.message, patient.get("treatment_stage", "consultation"))

    nice_evidence = match_nice_evidence(req.message)
    system_prompt = COMPANION_SYSTEM.format(
        patient_context=build_patient_context(req.patient_id),
        education_context=build_education_context(req.patient_id) + rag_context + ("\n\n" + nice_evidence if nice_evidence else ""),
    )

    # Add one-word check-in context so AI responds warmly
    if one_word_checkin:
        word = req.message.strip()
        system_prompt += f"""

ONE-WORD CHECK-IN DETECTED:
The patient just said "{word}" as a mood check-in. This maps to:
mood={one_word_checkin['mood']}, anxiety={one_word_checkin['anxiety']}, hope={one_word_checkin['hope']}, loneliness={one_word_checkin['loneliness']}, uncertainty={one_word_checkin['uncertainty']}
Respond with warmth. Acknowledge the word they used. Don't lecture. Don't ask them to rate things.
If it's a negative word, acknowledge briefly (1 sentence) then offer a gentle reframe or something concrete — a thought, a next step, or a grounding observation about where they are in their journey.
If positive, mirror the energy and affirm it.
Keep it brief (2-3 sentences). Do NOT ask "what's behind that?" or "how does that make you feel?" — they just told you.
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
This patient is looking for reassurance. Acknowledge their concern briefly (1-2 sentences),
then provide the clinical answer — the facts themselves ARE the reassurance. Frame the
information through a hopeful but honest lens. End with something concrete they can do or
ask about. Do NOT loop in pure validation — answer the question, that is what reassures."""
        elif edu_intent.get("intent") == "EXPLAIN_FIRST":
            system_prompt += """

EDUCATION INTENT: EXPLAIN_FIRST
This patient wants to understand the science/mechanism. Lead with clear, accurate clinical
information using plain language. Use helpful analogies. Include relevant numbers if available.
End with "your specialist can give you specifics for your situation"."""
        elif edu_intent.get("intent") == "PRACTICAL_FIRST":
            system_prompt += """

EDUCATION INTENT: PRACTICAL_FIRST
This patient wants actionable information. Lead with concrete tips and what to expect.
Use bullet-point style thinking (even in prose). Tell them what to ask their specialist.
Include timing, preparation, and "what to watch for". Keep it focused and useful."""
    elif triage_category == 2 and style == "ANALYTICAL":
        system_prompt += """

STYLE NOTE: This patient prefers ANALYTICAL responses. Lead with data, statistics,
and clinical detail. Use precise language. They appreciate thoroughness."""
    elif triage_category == 2 and style == "EMOTIONAL":
        system_prompt += """

STYLE NOTE: This patient prefers EMOTIONAL responses. Acknowledge their feelings warmly
in 1-2 sentences, then move to reappraisal — offer a different lens on the situation.
Weave in the clinical information as grounding (the facts themselves are often the best
reappraisal). Do NOT loop in pure validation — move the conversation forward."""

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
The patient is showing elevated distress. Follow the emotion regulation framework more carefully:
- Acknowledge their pain genuinely (1-2 sentences max)
- Do NOT dwell in the distress or ask them to elaborate on their pain
- Move toward reappraisal: ground them in what is concretely true about their situation
- Offer one specific action they can take right now (breathing exercise, calling their nurse, journaling)
- Mention that their clinic support team is available if they want to talk to someone
- If they seem stuck in a rumination loop, gently name it and redirect
Do NOT be alarmist. Treat them as resilient. Move the conversation forward."""

    # Add soft spot context to system prompt
    soft_spot = get_soft_spot_context(req.patient_id)
    if soft_spot:
        system_prompt += f"""

SOFT SPOT AWARENESS:
The patient is at a known emotional difficulty point: {soft_spot['stage']}.
Context: {soft_spot['message']}
{"Offer: " + soft_spot['what_helps'] if soft_spot.get('what_helps') else "Just be present. No fixing needed."}
Weave this awareness naturally — don't announce it as a feature, just show you understand where they are."""

    # ── Clinical Triggers ──
    clinical_triggers = evaluate_clinical_triggers(req.patient_id)
    support_widgets = []
    if clinical_triggers:
        trigger_context = "\n\nCLINICAL CONTEXT — Active support triggers:\n"
        for t in clinical_triggers:
            trigger_context += f"- {t['rule']}: {t.get('clinician_flag', '')}\n"
            if t.get('support_widget'):
                support_widgets.append(t['support_widget'])
        trigger_context += """Adjust your response tone accordingly:
- For pre_procedure_anxiety: Lead with reassurance about the upcoming procedure
- For post_result_vulnerability: Lead with empathy, avoid clinical language
- For stim_fatigue: Acknowledge exhaustion, normalize it
- For tww_spiral: Offer distraction techniques, normalize symptom-checking anxiety
- For disengagement_warning: Be warm, no pressure, just presence
- For medication_confusion: Offer clear, simple medication guidance"""
        system_prompt += trigger_context

    # Inject flagged topics from clinician
    patient = patients_db.get(req.patient_id, {})
    flagged = [f for f in patient.get("flagged_topics", []) if not f.get("resolved")]
    if flagged:
        flag_context = "\n\nCLINICIAN FLAGGED TOPICS — weave these into the conversation naturally:\n"
        for f in flagged[:3]:  # max 3 active flags
            flag_context += f"- Topic: {f['topic']}"
            if f.get("instruction"):
                flag_context += f" (Instruction: {f['instruction']})"
            flag_context += f" [Priority: {f.get('priority', 'when_natural')}]\n"
        system_prompt += flag_context

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

    # Suggested education topics
    stage = patient.get("treatment_stage", "consultation")
    suggested = EDUCATION_TOPICS.get(stage, [])[:3] if triage_category == 2 else None

    # Add alerts to escalation for high-risk patients
    if escalation and escalation.get("level") in ("AMBER", "RED"):
        escalation["alerts"] = []
        if escalation["level"] == "RED":
            escalation["alerts"].append("Alert: Nurse dashboard notification")
            escalation["alerts"].append("Doctor pre-brief before consult recommended")
        elif escalation["level"] == "AMBER":
            escalation["alerts"].append("Alert: Nurse dashboard notification")

    # ANZARD charts — only on educational/medical context (triage 2) or explicit data questions
    anzard_charts = None
    if triage_category == 2 or _is_data_question(req.message):
        anzard_charts = match_anzard_charts(req.message, assistant_msg) or None
        if anzard_charts:
            logger.info(f"[ANZARD] Charts detected: {[c['key'] for c in anzard_charts]}")

    # Fertool inline charts (AMH normogram, egg freeze table)
    fertool_inline = detect_fertool_inline_charts(req.message)
    # If inline Fertool charts detected, remove overlapping ANZARD charts
    if fertool_inline and anzard_charts:
        if 'amh_normogram' in fertool_inline:
            anzard_charts = [c for c in anzard_charts if c['key'] != 'age_outcomes']
        if 'egg_freeze_table' in fertool_inline:
            anzard_charts = [c for c in anzard_charts if c['key'] != 'egg_freezing_stats']
        if not anzard_charts:
            anzard_charts = None

    return ChatResponse(
        response=assistant_msg,
        patient_id=req.patient_id,
        treatment_stage=stage,
        escalation=escalation,
        triage_label=triage_label,
        is_distress=is_distress,
        is_crisis=is_crisis,
        suggested_education=suggested,
        fertool_cards=None,  # Fertool link cards REMOVED
        fertool_inline_charts=fertool_inline if fertool_inline else None,
        one_word_checkin=one_word_checkin,
        education_fork=education_fork,
        anzard_charts=anzard_charts,
        support_widgets=support_widgets if support_widgets else None,
        clinical_triggers=[{"rule": t["rule"], "priority": t.get("priority", "moderate")} for t in clinical_triggers] if clinical_triggers else None,
        query_id=query_id,
    )


@app.get("/checkin/similar")
async def checkin_similar(mood: int = 3):
    """Count how many patients checked in today with similar mood (±1)."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        similar = 0
        # Check in-memory store
        for pid, sessions in checkins_db.items():
            for data in sessions:
                if isinstance(data, dict):
                    ts = data.get('date', '')
                    m = data.get('mood', 0)
                    if today in str(ts) and abs(m - mood) <= 1:
                        similar += 1
        # Check Firebase
        try:
            if firebase_db and firebase_db._fb_ref:
                ref = firebase_db._fb_ref.child('checkins')
                all_ci = ref.get() or {}
                for pid, sessions in all_ci.items():
                    if isinstance(sessions, dict):
                        for sid, data in sessions.items():
                            if isinstance(data, dict):
                                ts = data.get('timestamp', data.get('date', ''))
                                m = data.get('mood', 0)
                                if today in str(ts) and abs(m - mood) <= 1:
                                    similar += 1
        except Exception:
            pass
        similar = max(0, similar - 1)  # Don't count current user
        return {"similar_count": similar}
    except Exception:
        return {"similar_count": 0}


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

    # Run clinical triggers after check-in
    clinical_triggers = evaluate_clinical_triggers(req.patient_id)
    # Store any clinician flags
    for t in clinical_triggers:
        if t.get('priority') == 'high':
            _sync_escalation(req.patient_id, {
                "level": "AMBER",
                "reason": t.get('clinician_flag', 'Clinical trigger fired'),
                "signals": [t['rule']],
                "timestamp": utc_iso(),
            })

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

    # Collect support widgets from clinical triggers
    checkin_support_widgets = [t.get('support_widget') for t in clinical_triggers if t.get('support_widget')] if clinical_triggers else []

    return CheckInResponse(
        message=melod_msg,
        patient_id=req.patient_id,
        checkin_summary=checkin,
        escalation=escalation,
        trigger_screening=trigger_screening,
        support_widgets=checkin_support_widgets if checkin_support_widgets else None,
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


@app.post("/phq4/{patient_id}", response_model=PHQ4Response)
async def submit_phq4(patient_id: str, req: PHQ4Request):
    """Submit a PHQ-4 ultra-brief screening (2 GAD-2 + 2 PHQ-2 items).

    Scores: anxiety_sub = q1+q2 (0-6), depression_sub = q3+q4 (0-6), total = 0-12.
    Severity: 0-2 normal, 3-5 mild, 6-8 moderate, 9-12 severe.
    Flags clinician alert if total >= 6 or either subscale >= 4.
    """
    get_or_create_patient(patient_id)

    anxiety_sub = req.q1 + req.q2
    depression_sub = req.q3 + req.q4
    total = anxiety_sub + depression_sub

    if total <= 2:
        severity = "normal"
    elif total <= 5:
        severity = "mild"
    elif total <= 8:
        severity = "moderate"
    else:
        severity = "severe"

    flagged = total >= 6 or anxiety_sub >= 4 or depression_sub >= 4

    # Melbourne date string for Firebase key
    from datetime import timezone, timedelta
    melb_tz = timezone(timedelta(hours=10))
    date_key = datetime.now(melb_tz).strftime("%Y-%m-%d")

    phq4_record = {
        "date": date_key,
        "anxiety_sub": anxiety_sub,
        "depression_sub": depression_sub,
        "total": total,
        "severity": severity,
        "triggered_by": req.triggered_by,
        "responses": {"q1": req.q1, "q2": req.q2, "q3": req.q3, "q4": req.q4},
        "timestamp": utc_iso(),
    }

    # Store to Firebase: melod_ai/patients/{pid}/phq4_scores/{date}
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).child("phq4_scores").child(date_key).update(phq4_record)
    except Exception as e:
        logger.warning(f"PHQ-4 Firebase write error for {patient_id}: {e}")

    # Flag clinician alert if elevated
    if flagged:
        alert = {
            "patient_id": patient_id,
            "level": "AMBER" if total < 9 else "RED",
            "type": "phq4_elevated",
            "message": f"PHQ-4 total={total} (anxiety={anxiety_sub}, depression={depression_sub}, severity={severity})",
            "triggered_by": req.triggered_by,
            "timestamp": utc_iso(),
            "acknowledged": False,
        }
        try:
            if firebase_db and firebase_db._fb_ref:
                firebase_db._fb_ref.child("alerts").push(alert)
        except Exception as e:
            logger.warning(f"PHQ-4 alert write error for {patient_id}: {e}")

        _sync_escalation(patient_id, {
            "level": alert["level"],
            "reason": f"PHQ-4 elevated: total={total}, severity={severity}",
            "signals": ["phq4_screening"],
            "timestamp": utc_iso(),
        })

    # Clear pending_phq4 flag if this was triggered by phenotype
    if req.triggered_by == "phenotype_alert":
        try:
            if firebase_db and firebase_db._fb_ref:
                firebase_db._fb_ref.child("patients").child(patient_id).update({"pending_phq4": False})
        except Exception:
            pass

    logger.info(f"PHQ-4 submitted for {patient_id}: total={total}, severity={severity}, flagged={flagged}, triggered_by={req.triggered_by}")

    return PHQ4Response(
        total=total,
        anxiety_sub=anxiety_sub,
        depression_sub=depression_sub,
        severity=severity,
        flagged=flagged,
    )


@app.get("/phq4/{patient_id}/pending")
async def check_pending_phq4(patient_id: str):
    """Check if this patient has a pending PHQ-4 triggered by phenotyping."""
    try:
        if firebase_db and firebase_db._fb_ref:
            val = firebase_db._fb_ref.child("patients").child(patient_id).child("pending_phq4").get()
            if val:
                cycle_phase = None
                if isinstance(val, dict):
                    cycle_phase = val.get("cycle_phase")
                return {"pending": True, "cycle_phase": cycle_phase}
    except Exception:
        pass
    return {"pending": False, "cycle_phase": None}


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


@app.get("/patient/{patient_id}/profile")
async def get_patient_profile(patient_id: str):
    """Get basic patient profile — returns null fields if not found (for auth flow)."""
    patient = patients_db.get(patient_id, {})
    return {
        "name": patient.get("patient_name", patient.get("name", "")),
        "email": patient.get("email", ""),
        "treatment_stage": patient.get("treatment_stage", ""),
        "cycle_number": patient.get("cycle_number", 1),
        "treatment_type": patient.get("treatment_type", "ivf"),
        "created_at": patient.get("created_at", ""),
        "exists": bool(patient),
    }


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

CLINICIAN_API_KEY = os.getenv("CLINICIAN_API_KEY", "")
_recent_login_uids = set()  # deduplicate login audit logs


async def verify_clinician_api_key(request: Request, x_api_key: str = Header(None), authorization: str = Header(None)):
    """Dependency: accept Bearer token (Firebase ID token) OR X-API-Key.
    Stores clinician info in request.state.clinician for audit logging."""
    clinician_info = {"uid": "api_key_user", "email": "api_key", "role": "doctor"}
    # Try Bearer token first
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            import firebase_admin.auth as fb_auth
            decoded = fb_auth.verify_id_token(token)
            claims = decoded.get("clinician")
            if not claims and not decoded.get("clinician_role"):
                # Check custom claims
                user_record = fb_auth.get_user(decoded["uid"])
                cc = user_record.custom_claims or {}
                if not cc.get("clinician"):
                    raise HTTPException(status_code=403, detail={"error": "Not a clinician account"})
                claims = True
                role = cc.get("role", "doctor")
            else:
                role = decoded.get("clinician_role") or decoded.get("role", "doctor")
            clinician_info = {
                "uid": decoded["uid"],
                "email": decoded.get("email", ""),
                "role": role
            }
            # Capture IP for audit
            ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
            clinician_info["ip"] = ip.split(",")[0].strip()
            request.state.clinician = clinician_info
            # Log first-seen login (deduplicated)
            uid = decoded["uid"]
            if uid not in _recent_login_uids:
                _recent_login_uids.add(uid)
                if len(_recent_login_uids) > 100:
                    _recent_login_uids.clear()
                asyncio.create_task(_log_audit_safe("login", clinician_info))
            return
        except HTTPException:
            raise
        except Exception as e:
            logging.warning(f"Token verification failed: {e}")
            raise HTTPException(status_code=401, detail={"error": "Invalid or expired token"})
    # Fall back to API key
    if CLINICIAN_API_KEY and x_api_key == CLINICIAN_API_KEY:
        ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
        clinician_info["ip"] = ip.split(",")[0].strip()
        request.state.clinician = clinician_info
        return
    raise HTTPException(status_code=403, detail={"error": "Invalid API key or token"})


def require_role(*allowed_roles):
    """Dependency factory: restrict endpoint to specific clinician roles."""
    async def _check_role(request: Request):
        info = getattr(request.state, "clinician", None)
        if not info:
            raise HTTPException(status_code=403, detail={"error": "Authentication required"})
        if info.get("role") not in allowed_roles and info.get("uid") != "api_key_user":
            raise HTTPException(status_code=403, detail={"error": f"Requires role: {', '.join(allowed_roles)}"})
    return _check_role


# ── Audit Logging ────────────────────────────────────────────────────

import time as _time

async def _log_audit_safe(action: str, clinician_info: dict = None, patient_id: str = None, details: dict = None):
    """Log clinician action to Firebase. Fire-and-forget, never raises."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            return
        if clinician_info is None:
            clinician_info = {}
        audit_entry = {
            "timestamp": utc_iso(),
            "epoch": _time.time(),
            "clinician_uid": clinician_info.get("uid", "api_key_user"),
            "clinician_email": clinician_info.get("email", "api_key"),
            "clinician_role": clinician_info.get("role", "unknown"),
            "action": action,
            "patient_id": patient_id,
            "details": details or {},
            "ip_address": clinician_info.get("ip", "unknown")
        }
        _fb_ref.child("audit_log").push(audit_entry)
    except Exception as e:
        logging.warning(f"Audit log failed: {e}")


def _get_clinician(request: Request) -> dict:
    """Helper: get clinician info from request state."""
    return getattr(request.state, "clinician", {"uid": "api_key_user", "email": "api_key", "role": "unknown"})


# ── Clinician Dashboard Endpoints ────────────────────────────────────

@app.get("/clinician/dashboard", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_dashboard():
    """Get overview of all patients for clinician dashboard.

    Note: both /clinician/dashboard and /clinician/patients resolve here.
    """
    overview = []
    # Firebase is the single source of truth for the patient registry
    all_patients = {}
    try:
        fb_ref = getattr(firebase_db, '_fb_ref', None)
        if fb_ref:
            fb_patients = fb_ref.child("patients").get()
            if fb_patients and isinstance(fb_patients, dict):
                for pid, pdata in fb_patients.items():
                    if isinstance(pdata, dict):
                        all_patients[pid] = pdata
    except Exception as e:
        logger.warning(f"Firebase patient load failed, falling back to in-memory: {e}")
        all_patients = dict(patients_db)
    for pid, patient in all_patients.items():
      try:
        recent_checkins = get_recent_checkins(pid, last_n=3)
        recent_esc = escalations_db.get(pid, [])[-1:] if escalations_db.get(pid) else []

        avg_mood = None
        if recent_checkins:
            avg_mood = round(sum(c.get("mood", 5) for c in recent_checkins) / len(recent_checkins), 1)

        # Determine risk level
        risk = "GREEN"
        if recent_esc and recent_esc[0].get("level") == "RED":
            risk = "RED"
        elif recent_esc and recent_esc[0].get("level") == "AMBER":
            risk = "AMBER"

        # Get signal store data if available
        store = patient_signal_store.get(pid, {})
        latest_ci = recent_checkins[-1] if recent_checkins else None

        patient_name = patient.get("patient_name") or patient.get("name") or "Anonymous"
        stage = patient.get("treatment_stage", "consultation")

        # Load cycle data for spreadsheet view
        cycle_data = None
        try:
            if firebase_db and firebase_db._fb_ref:
                cd = firebase_db._fb_ref.child("patients").child(pid).child("cycle").get()
                if cd and isinstance(cd, dict):
                    cycle_data = {
                        "type": cd.get("type") or cd.get("protocol", ""),
                        "cycle_number": cd.get("cycle_number", 1),
                        "start_date": cd.get("start_date") or (cd.get("key_dates") or {}).get("cycle_start", ""),
                        "key_dates": cd.get("key_dates", {}),
                        "medications": cd.get("medications", {}),
                        "notes": cd.get("notes", ""),
                    }
        except Exception:
            pass

        overview.append({
            "patient_id": pid,
            "patient_name": patient_name,
            "name": patient_name,  # backward compat
            "email": patient.get("email", ""),
            "treatment_stage": STAGE_DISPLAY.get(stage, stage),
            "cycle_number": patient.get("cycle_number", 1),
            "avg_mood_3d": avg_mood,
            "risk_level": risk,
            "escalation_level": risk,
            "last_active": patient.get("last_active", ""),
            "last_updated": patient.get("last_active", ""),
            "last_escalation": recent_esc[0] if recent_esc else None,
            "session_count": store.get("session_count", 0),
            "baseline_established": store.get("baseline_established", False),
            "active_constructs": list((store.get("current_assessment") or {}).get("constructs", {}).keys()),
            "latest_checkin": latest_ci,
            "human_escalation_requested": store.get("human_escalation_requested", False),
            "communication_style": classify_patient_style(pid),
            "summary": (store.get("current_assessment") or {}).get("summary", ""),
            "age": patient.get("age", ""),
            "cycle": cycle_data,
        })
      except Exception as e:
        logger.warning(f"Error building patient overview for {pid}: {e}")
        # Still include the patient with minimal info
        overview.append({
            "patient_id": pid,
            "patient_name": patient.get("patient_name") or patient.get("name") or "Unknown",
            "name": patient.get("name", "Unknown"),
            "email": patient.get("email", ""),
            "treatment_stage": patient.get("treatment_stage", "unknown"),
            "cycle_number": patient.get("cycle_number", 1),
            "risk_level": "GREEN", "escalation_level": "GREEN",
            "last_active": patient.get("last_active", ""),
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


@app.get("/clinician/patients-list", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_patients_v2():
    """Returns all patients — reads from both in-memory and Firebase."""
    all_patients = dict(patients_db)
    try:
        fb_ref = getattr(firebase_db, '_fb_ref', None)
        if fb_ref:
            fb_patients = fb_ref.child("patients").get()
            if fb_patients and isinstance(fb_patients, dict):
                for pid, pdata in fb_patients.items():
                    if pid not in all_patients and isinstance(pdata, dict):
                        all_patients[pid] = pdata
                        patients_db[pid] = pdata
    except Exception:
        pass

    overview = []
    for pid, patient in all_patients.items():
        try:
            patient_name = patient.get("patient_name") or patient.get("name") or "Unknown"
            stage = patient.get("treatment_stage", "consultation")
            recent_ci = get_recent_checkins(pid, last_n=1)
            latest_ci = recent_ci[-1] if recent_ci else None
            recent_esc = escalations_db.get(pid, [])[-1:] if escalations_db.get(pid) else []
            risk = "GREEN"
            if recent_esc and recent_esc[0].get("level") == "RED":
                risk = "RED"
            elif recent_esc and recent_esc[0].get("level") == "AMBER":
                risk = "AMBER"
            overview.append({
                "patient_id": pid,
                "patient_name": patient_name,
                "name": patient_name,
                "email": patient.get("email", ""),
                "treatment_stage": STAGE_DISPLAY.get(stage, stage),
                "cycle_number": patient.get("cycle_number", 1),
                "risk_level": risk,
                "escalation_level": risk,
                "last_active": patient.get("last_active", ""),
                "latest_checkin": latest_ci,
                "communication_style": classify_patient_style(pid),
            })
        except Exception:
            overview.append({
                "patient_id": pid,
                "patient_name": patient.get("name", "Unknown"),
                "treatment_stage": patient.get("treatment_stage", "unknown"),
                "risk_level": "GREEN", "escalation_level": "GREEN",
                "last_active": patient.get("last_active", ""),
            })

    risk_order = {"RED": 0, "AMBER": 1, "GREEN": 2}
    overview.sort(key=lambda x: risk_order.get(x.get("risk_level", "GREEN"), 3))
    return {"patients": overview, "total": len(overview), "timestamp": utc_iso()}


@app.post("/clinician/patient/create", dependencies=[Depends(verify_clinician_api_key)])
async def create_patient_from_dashboard(request: Request):
    """Create a patient directly from the clinician dashboard (no Firebase Auth account)."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        data = await request.json()
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Patient name is required")
        patient_id = f"dash_{int(_time.time() * 1000)}"
        # Calculate age from DOB if provided
        dob = data.get("dob", "")
        age = data.get("age", "")
        if dob and not age:
            try:
                parts = dob.split("-")
                born_y, born_m, born_d = int(parts[0]), int(parts[1]), int(parts[2])
                today = datetime.now()
                age = today.year - born_y - ((today.month, today.day) < (born_m, born_d))
            except Exception:
                pass
        patient_record = {
            "name": name,
            "patient_name": name,
            "email": data.get("email", ""),
            "phone": data.get("phone", ""),
            "dob": dob,
            "age": str(age) if age else "",
            "partner_name": data.get("partner_name", ""),
            "pronunciation": data.get("pronunciation", ""),
            "treatment_stage": data.get("stage", "consultation"),
            "notes": data.get("notes", ""),
            "created_from": "dashboard",
            "created_at": utc_iso(),
            "risk_level": "low"
        }
        _fb_ref.child("patients").child(patient_id).update(patient_record)
        patients_db[patient_id] = patient_record
        # Cycle record
        cycle_type = data.get("cycle_type", "")
        if cycle_type:
            cycle_record = {
                "type": cycle_type,
                "cycle_number": int(data.get("cycle_number", 1)),
                "stage": data.get("stage", "consultation"),
                "start_date": datetime.now().strftime("%Y-%m-%d"),
            }
            _fb_ref.child("patients").child(patient_id).child("cycle").update(cycle_record)
        # Audit
        info = _get_clinician(request)
        asyncio.create_task(_log_audit_safe("create_patient", info, patient_id, {"name": name}))
        return {"patient_id": patient_id, "name": name, "status": "created"}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Create patient failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


@app.get("/clinician/patient/{patient_id}/briefing", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_preconsult_briefing(patient_id: str, role: str = "doctor", force_refresh: str = "false"):
    """Unified one-glance briefing — synthesizes all 6 data streams via Haiku, cached 4h."""
    import json as _json

    # === CHECK CACHE ===
    if force_refresh != "true":
        try:
            if firebase_db and firebase_db._fb_ref:
                cached = firebase_db._fb_ref.child("briefing_cache").child(patient_id).get()
                if cached and isinstance(cached, dict) and cached.get("generated_at"):
                    gen_time = datetime.fromisoformat(cached["generated_at"].replace("Z", "+00:00"))
                    if (utc_now() - gen_time).total_seconds() < 14400:  # 4 hours
                        return cached
        except Exception:
            pass

    # === GATHER DATA (in-memory first, Firebase fallback) ===
    patient = patients_db.get(patient_id, {})
    if not patient:
        try:
            if firebase_db and firebase_db._fb_ref:
                patient = firebase_db._fb_ref.child("patients").child(patient_id).get() or {}
        except Exception:
            patient = {}

    patient_name = patient.get("patient_name") or patient.get("name") or "Unknown"
    patient_age = patient.get("age", "?")
    patient_stage = STAGE_DISPLAY.get(patient.get("treatment_stage", ""), patient.get("treatment_stage", "unknown"))

    # Checkins
    checkins = checkins_db.get(patient_id, [])[-20:]
    mood_summary = "No mood check-ins yet."
    if checkins:
        recent = checkins[-3:]
        moods = [c.get("mood", 5) for c in recent]
        anxieties = [c.get("anxiety", 5) for c in recent]
        hopes = [c.get("hope", 5) for c in recent]
        mood_summary = f"Last {len(recent)} check-ins — Mood: {moods}, Anxiety: {anxieties}, Hope: {hopes}"
        if len(checkins) >= 5:
            avg_old = sum(c.get("mood", 5) for c in checkins[:3]) / 3
            avg_new = sum(c.get("mood", 5) for c in checkins[-3:]) / 3
            if avg_new < avg_old - 1.5:
                mood_summary += f". TREND: Declining (was ~{avg_old:.0f}, now ~{avg_new:.0f})"
            elif avg_new > avg_old + 1.5:
                mood_summary += f". TREND: Improving (was ~{avg_old:.0f}, now ~{avg_new:.0f})"

    # Comfort reports (Firebase)
    comfort_summary = "No post-visit feedback yet."
    latest_comfort = None
    try:
        if firebase_db and firebase_db._fb_ref:
            cr = firebase_db._fb_ref.child("comfort_reports").child(patient_id).order_by_child("timestamp").limit_to_last(1).get()
            if cr:
                latest_comfort = list(cr.values())[0] if isinstance(cr, dict) else None
                if latest_comfort:
                    ratings = latest_comfort.get("ratings", {})
                    proc = latest_comfort.get("procedure", "?")
                    comfort_summary = f"Latest ({proc}): " + ", ".join(f"{k}: {v}/10" for k, v in ratings.items())
                    if latest_comfort.get("note"):
                        comfort_summary += f'. Patient: "{latest_comfort["note"]}"'
                    if latest_comfort.get("want_er"):
                        comfort_summary += " ⚠️ ER CONCERN FLAGGED"
    except Exception:
        pass

    # Chat history
    convs = conversations_db.get(patient_id, [])
    user_msgs = [m.get("content", "") for m in convs[-20:] if m.get("role") == "user"]
    chat_summary = "No chat history."
    if user_msgs:
        distress = [m for m in user_msgs if any(kw in m.lower() for kw in ["terrified", "scared", "stressed", "cannot", "can't", "hopeless", "alone", "crying"])]
        if distress:
            chat_summary = f'Distress: "{distress[-1][:80]}" ({len(distress)} distress msgs). '
        else:
            chat_summary = f"Last {len(user_msgs)} messages — routine tone. "

    # Risk tier
    risk = {}
    try:
        if firebase_db and firebase_db._fb_ref:
            risk = firebase_db._fb_ref.child("patients").child(patient_id).child("risk_tier").get() or {}
    except Exception:
        pass
    risk_tier = risk.get("tier", "GREEN") if isinstance(risk, dict) else "GREEN"
    risk_signals = risk.get("signals", []) if isinstance(risk, dict) else []

    # Unread patient notes
    unread_notes = []
    try:
        if firebase_db and firebase_db._fb_ref:
            notes = firebase_db._fb_ref.child("patient_notes").child(patient_id).get()
            if notes and isinstance(notes, dict):
                for k, v in notes.items():
                    if isinstance(v, dict) and not v.get("read", False):
                        unread_notes.append(v.get("text", ""))
    except Exception:
        pass

    # === ROLE-BASED PROMPT ===
    if role == "nurse":
        role_inst = "Briefing a NURSE: focus on emotional state, distress, medication concerns, callback requests. Under 100 words."
    elif role == "secretary":
        role_inst = "Briefing a SECRETARY: only active/inactive status, appointment concerns, callback requests. Under 50 words."
    else:
        role_inst = "Briefing a DOCTOR before consult: emotional trajectory with numbers, comfort feedback with scores, communication style (analytical/emotional), specific action items. Under 150 words."

    # === HAIKU BRIEFING ===
    briefing_prompt = f"""{role_inst}

PATIENT: {patient_name}, Age {patient_age}, Stage: {patient_stage}
RISK: {risk_tier} — {', '.join(risk_signals[:4]) if risk_signals else 'no signals'}
MOOD: {mood_summary}
COMFORT: {comfort_summary}
CHAT: {chat_summary}
NOTES: {_json.dumps(unread_notes) if unread_notes else 'None'}

Use these EXACT section headers:
WHAT'S HAPPENING:
AFTER LAST VISIT:
WHAT THEY NEED:

Every sentence must reference actual data. No generic advice."""

    try:
        resp = client.messages.create(model=HAIKU_MODEL, max_tokens=400, messages=[{"role": "user", "content": briefing_prompt}])
        briefing_text = resp.content[0].text
    except Exception as e:
        briefing_text = f"Briefing generation failed: {e}"

    result = {
        "patient_id": patient_id,
        "patient_name": patient_name,
        "patient_age": patient_age,
        "stage": patient_stage,
        "risk_tier": risk_tier,
        "risk_score": risk.get("score", 0) if isinstance(risk, dict) else 0,
        "risk_signals": risk_signals,
        "briefing_text": briefing_text,
        "unread_notes": unread_notes,
        "latest_comfort": latest_comfort,
        "mood_trend": mood_summary,
        "role": role,
        "generated_at": utc_iso(),
    }

    # Cache to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("briefing_cache").child(patient_id).set(result)
    except Exception:
        pass

    return result


@app.get("/clinician/patient/{patient_id}/phenotype-history", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_phenotype_history(patient_id: str, days: int = 30):
    """Return the last N days of phenotype snapshots for trend charts."""
    history = firebase_db.load_phenotype_history(patient_id, limit=500)

    # Filter to last N days
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [s for s in history if s.get("timestamp", "") >= cutoff]

    # Load check-ins from Firebase (in-memory may be stale across Cloud Run instances)
    checkins = firebase_db.load_checkins(patient_id) if firebase_db else checkins_db.get(patient_id, [])
    recent_checkins = [c for c in checkins if c.get("date", "") >= cutoff]

    return {
        "patient_id": patient_id,
        "snapshots": recent,
        "checkins": recent_checkins,
        "total_snapshots": len(recent),
        "total_checkins": len(recent_checkins),
        "days": days,
    }


# ── Clinician Action Endpoints ────────────────────────────────────────


@app.post("/clinician/patient/{patient_id}/send-message", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_send_message(patient_id: str, request: Request):
    """Send a message from care team to patient. Saved to conversations and Firebase."""
    body = await request.json()
    msg_text = body.get("message", "").strip()
    from_role = body.get("from_role", "doctor")
    if not msg_text:
        raise HTTPException(status_code=400, detail="Message text required")

    msg = {
        "role": "care_team",
        "content": msg_text,
        "from_role": from_role,
        "sender_name": body.get("clinician_id", "Your care team"),
        "timestamp": utc_iso(),
        "type": "clinician_message",
        "read": False,
    }
    # Save to conversations for this patient
    conversations_db.setdefault(patient_id, []).append(msg)
    # Save to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("clinician_messages").child(patient_id).push(msg)
    except Exception:
        pass
    return {"status": "sent", "timestamp": msg["timestamp"]}


@app.post("/clinician/patient/{patient_id}/flag-topic", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_flag_topic(patient_id: str, request: Request):
    """Flag a topic for the AI to weave into the next conversation."""
    body = await request.json()
    topic = body.get("topic", "").strip()
    instruction = body.get("instruction", "")
    priority = body.get("priority", "when_natural")
    if not topic:
        raise HTTPException(status_code=400, detail="Topic required")

    flag = {
        "topic": topic,
        "instruction": instruction,
        "priority": priority,
        "flagged_at": utc_iso(),
        "resolved": False,
    }
    # Store in patient data
    patient = patients_db.get(patient_id, {})
    patient.setdefault("flagged_topics", []).append(flag)
    # Save to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("flagged_topics").child(patient_id).push(flag)
    except Exception:
        pass
    return {"status": "flagged", "topic": topic}


@app.post("/clinician/patient/{patient_id}/schedule-nudge", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_schedule_nudge(patient_id: str, request: Request):
    """Schedule a check-in nudge for a patient."""
    body = await request.json()
    nudge = {
        "message": body.get("message", "Your care team is thinking of you."),
        "deliver_at": body.get("deliver_at", utc_iso()),
        "from_role": body.get("from", "doctor"),
        "scheduled_at": utc_iso(),
        "delivered": False,
    }
    patient = patients_db.get(patient_id, {})
    patient.setdefault("scheduled_nudges", []).append(nudge)
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("scheduled_nudges").child(patient_id).push(nudge)
    except Exception:
        pass
    return {"status": "scheduled", "deliver_at": nudge["deliver_at"]}


@app.post("/clinician/patient/{patient_id}/resolve-concern", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_resolve_concern(patient_id: str, request: Request):
    """Mark an unresolved question as resolved."""
    body = await request.json()
    topic_key = body.get("topic_key", "")
    resolution_note = body.get("resolution_note", "")
    resolved_by = body.get("resolved_by", "doctor")

    resolution = {
        "topic_key": topic_key,
        "resolution_note": resolution_note,
        "resolved_by": resolved_by,
        "resolved_at": utc_iso(),
    }
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("resolved_concerns").child(patient_id).push(resolution)
    except Exception:
        pass
    return {"status": "resolved", "topic_key": topic_key}


@app.get("/clinician/patient/{patient_id}/conversations", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_conversations(patient_id: str):
    """Return recent conversation sessions for clinician review."""
    convs = conversations_db.get(patient_id, [])
    if not convs:
        return {"sessions": []}

    # Group into sessions (gap > 30 min = new session)
    sessions = []
    current = []
    for i, msg in enumerate(convs):
        if i > 0 and msg.get("timestamp", "") and convs[i-1].get("timestamp", ""):
            try:
                t1 = datetime.fromisoformat(convs[i-1]["timestamp"].replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                if (t2 - t1).total_seconds() > 1800:
                    if current:
                        sessions.append(current)
                    current = []
            except Exception:
                pass
        current.append(msg)
    if current:
        sessions.append(current)

    # Format last 10 sessions
    result = []
    for session in sessions[-10:]:
        user_msgs = [m for m in session if m.get("role") == "user"]
        ai_msgs = [m for m in session if m.get("role") == "assistant"]
        first_ts = session[0].get("timestamp", "")
        emotional_tone = "neutral"
        # Detect tone from triage labels on messages
        for m in session:
            if m.get("is_crisis"):
                emotional_tone = "crisis"
                break
            elif m.get("is_distress"):
                emotional_tone = "distressed"
            elif m.get("triage") == 1 and emotional_tone not in ("distressed", "crisis"):
                emotional_tone = "anxious"
            elif m.get("triage") == 2 and emotional_tone == "neutral":
                emotional_tone = "curious"

        result.append({
            "date": first_ts,
            "message_count": len(session),
            "one_line": user_msgs[0].get("content", "")[:100] if user_msgs else "Check-in conversation",
            "emotional_tone": emotional_tone,
            "full_conversation": [{"role": m.get("role", ""), "content": m.get("content", "")[:500]} for m in session[-20:]],
        })

    return {"sessions": list(reversed(result))}


@app.get("/patient/{patient_id}/clinician-messages")
async def get_clinician_messages(patient_id: str):
    """Get unread clinician messages. Does NOT auto-mark as read — client must POST to mark."""
    messages = []
    # Try Firebase first
    try:
        if firebase_db and firebase_db._fb_ref:
            msgs = firebase_db._fb_ref.child("clinician_messages").child(patient_id).get()
            if msgs and isinstance(msgs, dict):
                for key, val in msgs.items():
                    if not val.get("read", False):
                        val["id"] = key
                        messages.append(val)
                        # Do NOT mark as read here — let client confirm display first
    except Exception as e:
        logger.warning(f"Firebase clinician messages read failed: {e}")
    # Also check in-memory conversations for clinician messages not yet read
    convs = conversations_db.get(patient_id, [])
    for i, msg in enumerate(convs):
        if msg.get("type") == "clinician_message" and not msg.get("read", False):
            messages.append({**msg, "read": False})
            # Do NOT mark as read here
    return {"messages": messages}


@app.post("/patient/{patient_id}/clinician-messages/mark-read")
async def mark_clinician_messages_read(patient_id: str):
    """Mark all clinician messages as read for a patient."""
    # Mark in Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            msgs = firebase_db._fb_ref.child("clinician_messages").child(patient_id).get()
            if msgs and isinstance(msgs, dict):
                for key, val in msgs.items():
                    if not val.get("read", False):
                        firebase_db._fb_ref.child("clinician_messages").child(patient_id).child(key).update({"read": True})
    except Exception:
        pass
    # Mark in memory
    convs = conversations_db.get(patient_id, [])
    for msg in convs:
        if msg.get("type") == "clinician_message" and not msg.get("read", False):
            msg["read"] = True
    return {"status": "ok"}


@app.get("/clinician/patient/{patient_id}/unresolved", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_unresolved(patient_id: str):
    """Return unresolved questions for this patient."""
    patient = patients_db.get(patient_id, {})
    unresolved = patient.get("unresolved_questions", [])
    return {"questions": unresolved, "count": len(unresolved)}


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

    # 1. Stage-specific nudge (always)
    stage_msgs = STAGE_NUDGES.get(stage, STAGE_NUDGES["consultation"])
    import random
    nudges.append(random.choice(stage_msgs))

    # 2. Procedure nudge (only for key procedure days)
    if stage in PROCEDURE_NUDGES:
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
        "extra": nudges[1] if len(nudges) > 1 else None,  # Optional second
        "stage": stage,
        "stage_display": STAGE_DISPLAY.get(stage, stage),
        "days_since_checkin": days_since,
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


# ── Cycle Events ─────────────────────────────────────────────────────

@app.get("/patient/{patient_id}/cycle-events")
async def get_cycle_events(patient_id: str):
    """Get all cycle events for a patient."""
    events = cycle_events_db.get(patient_id, [])
    # Also check for undelivered calendar updates
    updates = calendar_updates_db.get(patient_id, [])
    undelivered = [u for u in updates if not u.get('delivered')]
    return {"events": events, "calendar_updates": undelivered}

@app.post("/patient/{patient_id}/cycle-events")
async def add_cycle_event(patient_id: str, request: Request):
    """Patient adds a cycle event."""
    body = await request.json()
    event = {
        "id": body.get("id", f"evt_{uuid.uuid4().hex[:8]}"),
        "date": body.get("date"),
        "type": body.get("type", "other"),
        "label": body.get("label", ""),
        "notes": body.get("notes", ""),
        "time": body.get("time", ""),
        "source": "patient",
        "created_at": utc_iso(),
    }
    cycle_events_db.setdefault(patient_id, []).append(event)
    # Save to Firebase
    try:
        if firebase_db.ready:
            firebase_db._save_cycle_event(patient_id, event)
    except Exception:
        pass
    return {"status": "ok", "event": event}


# ── Medication Adherence Tracking ────────────────────────────────────

@app.post("/patient/{patient_id}/med-taken")
async def mark_med_taken(patient_id: str, request: Request):
    """Patient marks a medication as taken. Writes to Firebase med_adherence."""
    body = await request.json()
    date_str = body.get("date", "")
    med_name = body.get("med_name", "all")
    timestamp = body.get("timestamp", utc_iso())
    if not date_str:
        raise HTTPException(status_code=400, detail="date required")
    entry = {"taken": True, "timestamp": timestamp, "med_name": med_name}
    # Write to Firebase: melod_ai/patients/{pid}/med_adherence/{date}/{safe_key}
    try:
        if firebase_db and firebase_db._fb_ref:
            safe_key = med_name.replace("/", "_").replace(".", "_").replace(" ", "_")[:40]
            firebase_db._fb_ref.child("patients").child(patient_id).child("med_adherence").child(date_str).child(safe_key).update(entry)
    except Exception as e:
        logger.warning(f"med-taken Firebase write error: {e}")
    return {"status": "ok"}


@app.get("/clinician/patient/{patient_id}/adherence", dependencies=[Depends(verify_clinician_api_key)])
async def get_adherence(patient_id: str, days: int = 14):
    """Return medication adherence data: scheduled vs taken for each day."""
    # 1. Get scheduled medications
    meds_simple = {}
    try:
        if firebase_db and firebase_db._fb_ref:
            cycle = firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").get()
            if cycle and isinstance(cycle, dict):
                meds_simple = cycle.get("medications_simple", {})
    except Exception as e:
        logger.warning(f"adherence: cycle read error: {e}")

    # 2. Get adherence records
    adherence_raw = {}
    try:
        if firebase_db and firebase_db._fb_ref:
            adherence_raw = firebase_db._fb_ref.child("patients").child(patient_id).child("med_adherence").get() or {}
    except Exception as e:
        logger.warning(f"adherence: adherence read error: {e}")

    # 3. Build day-by-day report. Dashboard grid: D1=today, D2=tomorrow, etc.
    from datetime import date as _date
    today = _date.today()
    day_reports = []
    total_scheduled = 0
    total_taken = 0

    for day_offset in range(days):
        d = today + timedelta(days=day_offset)
        date_str = d.isoformat()
        day_key = f"d{day_offset + 1}"
        day_adherence = adherence_raw.get(date_str, {}) if isinstance(adherence_raw, dict) else {}

        day_meds = []
        for med_key, med_data in (meds_simple.items() if isinstance(meds_simple, dict) else []):
            if not isinstance(med_data, dict):
                continue
            name = med_data.get("name", "")
            doses = med_data.get("doses", {})
            dose_val = doses.get(day_key, "")
            if not dose_val:
                continue
            # This med is scheduled on this day
            safe_key = name.replace("/", "_").replace(".", "_").replace(" ", "_")[:40]
            # Also check display-string key (e.g. "Gonal-f_300")
            display_str = f"{name} {dose_val}" if dose_val not in ("1", "\u2713") else name
            safe_display = display_str.replace("/", "_").replace(".", "_").replace(" ", "_")[:40]
            taken_entry = day_adherence.get(safe_key) or day_adherence.get(safe_display) or day_adherence.get(name.replace(" ", "_")[:40])
            is_taken = bool(taken_entry and taken_entry.get("taken"))
            taken_at = taken_entry.get("timestamp", "") if is_taken else ""

            is_procedure = dose_val in ("1", "\u2713")
            day_meds.append({
                "name": display_str if not is_procedure else name,
                "scheduled": True,
                "taken": is_taken,
                "taken_at": taken_at,
                "is_procedure": is_procedure,
            })
            if not is_procedure:
                total_scheduled += 1
                if is_taken:
                    total_taken += 1

        if day_meds:
            day_reports.append({"date": date_str, "day": day_key.upper(), "meds": day_meds})

    adherence_pct = round((total_taken / total_scheduled * 100), 1) if total_scheduled > 0 else 0.0

    return {
        "patient_id": patient_id,
        "days": day_reports,
        "summary": {
            "total_scheduled": total_scheduled,
            "total_taken": total_taken,
            "adherence_pct": adherence_pct,
        },
    }


@app.put("/clinician/patient/{patient_id}/cycle-events/{event_id}", dependencies=[Depends(verify_clinician_api_key)])
async def update_cycle_event(patient_id: str, event_id: str, request: Request):
    """Clinician modifies a cycle event date."""
    body = await request.json()
    events = cycle_events_db.get(patient_id, [])
    for evt in events:
        if evt.get('id') == event_id:
            old_date = evt.get('date')
            evt['date'] = body.get('date', evt['date'])
            evt['label'] = body.get('label', evt.get('label', ''))
            evt['modified_by'] = 'clinician'
            evt['modified_at'] = utc_iso()

            # Record the update for patient notification
            update = {
                "event_id": event_id,
                "old_date": old_date,
                "new_date": evt['date'],
                "reason": body.get('reason', ''),
                "modified_at": utc_iso(),
                "delivered": False,
            }
            calendar_updates_db.setdefault(patient_id, []).append(update)
            return {"status": "ok", "event": evt, "update": update}

    raise HTTPException(status_code=404, detail="Event not found")

@app.post("/patient/{patient_id}/calendar-updates/acknowledge")
async def acknowledge_calendar_updates(patient_id: str):
    """Patient acknowledges seeing calendar updates."""
    updates = calendar_updates_db.get(patient_id, [])
    for u in updates:
        u['delivered'] = True
    return {"status": "ok"}


# ── Anonymous Community ─────────────────────────────────────────────────

CRISIS_KEYWORDS = ['suicide', 'suicidal', 'kill myself', 'end it all', 'want to die', 'self-harm', 'cutting', 'overdose']
IDENTIFYING_KEYWORDS = ['my name is', 'my doctor', 'my clinic', 'dr.', 'dr ', 'melbourne ivf', 'virtus', 'monash ivf', 'genea']


def moderate_community_post(text: str, patient_id: str) -> dict:
    """Check community post for crisis language, identifying info, etc."""
    text_lower = text.lower()
    result = {"approved": True, "flags": [], "crisis": False}

    # Crisis check
    if any(kw in text_lower for kw in CRISIS_KEYWORDS):
        result["crisis"] = True
        result["flags"].append("crisis_language")
        # Trigger RED escalation
        _sync_escalation(patient_id, {
            "level": "RED",
            "reason": "Crisis language in community post",
            "signals": ["community_crisis_content"],
            "timestamp": utc_iso(),
        })

    # Identifying info check
    if any(kw in text_lower for kw in IDENTIFYING_KEYWORDS):
        result["approved"] = False
        result["flags"].append("identifying_info")
        result["message"] = "Please keep posts anonymous — avoid sharing names, doctors, or clinic details."

    return result


def seed_community_posts():
    """Seed the community with realistic anonymous posts if empty."""
    global community_posts_db
    if community_posts_db:
        return  # Already have posts
    # Check Firebase flag
    try:
        if firebase_db and firebase_db._fb_ref:
            flag = firebase_db._fb_ref.child('community_seeded').get()
            if flag:
                # Load existing posts from Firebase
                fb_posts = firebase_db._fb_ref.child('community_posts').get()
                if fb_posts:
                    community_posts_db = list(fb_posts.values()) if isinstance(fb_posts, dict) else fb_posts
                return
    except Exception:
        pass

    import random
    seeds = [
        {"text": "Day 3 of stim and the bloating is unreal. Anyone else feel like a balloon?", "stage": "stimulation", "mood": "anxious", "hours_ago": 2},
        {"text": "Just got told we have 3 good embryos. Crying happy tears.", "stage": "post_retrieval", "mood": "happy", "hours_ago": 5},
        {"text": "TWW day 7. I've googled 'early pregnancy symptoms' approximately 400 times today.", "stage": "early_tww", "mood": "anxious", "hours_ago": 3},
        {"text": "Failed our first cycle. Taking a month off. Trying to remember who I was before all this started.", "stage": "between_cycles", "mood": "sad", "hours_ago": 8},
        {"text": "Trigger shot tonight. Hands shaking but I've got this. Retrieval Thursday.", "stage": "stimulation", "mood": "hopeful", "hours_ago": 12},
        {"text": "14 eggs retrieved! Sore but grateful. Now the waiting begins for the embryo report.", "stage": "post_retrieval", "mood": "hopeful", "hours_ago": 18},
        {"text": "Does anyone else feel completely alone in this? My friends don't get it.", "stage": "stimulation", "mood": "sad", "hours_ago": 6},
        {"text": "Transfer done. One little embryo on board. Talking to it already. Is that weird?", "stage": "early_tww", "mood": "hopeful", "hours_ago": 24},
        {"text": "Second cycle starting. Scared but also weirdly calmer than the first time. You learn what to expect.", "stage": "stimulation", "mood": "content", "hours_ago": 36},
        {"text": "Got my positive today. After 2 years. I can't stop shaking.", "stage": "early_pregnancy", "mood": "happy", "hours_ago": 48},
        {"text": "The injections aren't even the hard part anymore. It's the emotional rollercoaster. Nobody warns you about that.", "stage": "stimulation", "mood": "anxious", "hours_ago": 15},
        {"text": "My partner held the ice pack on my belly while I did the injection tonight. Small moments of being a team.", "stage": "stimulation", "mood": "content", "hours_ago": 30},
    ]
    now = utc_now()
    for i, s in enumerate(seeds):
        post = {
            "id": f"seed_{i+1}_{int(now.timestamp())}",
            "text": s["text"],
            "anonymous": True,
            "display_name": "Anonymous",
            "stage": s["stage"],
            "mood": s["mood"],
            "created_at": (now - timedelta(hours=s["hours_ago"])).isoformat(),
            "patient_id": f"seed_patient_{i+1}",
            "reactions": {
                "support": random.randint(1, 8),
                "same": random.randint(0, 5),
                "strength": random.randint(0, 3),
            },
            "reported": False,
            "moderated": True,
            "visible": True,
        }
        community_posts_db.append(post)
        try:
            if firebase_db and firebase_db._fb_ref:
                firebase_db._fb_ref.child('community_posts').child(post['id']).set(post)
        except Exception:
            pass
    # Set seeded flag
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child('community_seeded').set(True)
    except Exception:
        pass
    logger.info(f"Seeded {len(seeds)} community posts")


@app.post("/community/posts")
async def create_community_post(req: CommunityPostRequest):
    """Create an anonymous community post."""
    # Moderate
    mod = moderate_community_post(req.text, req.patient_id)
    if not mod["approved"]:
        return JSONResponse(status_code=400, content={"error": mod.get("message", "Post not approved"), "flags": mod["flags"]})

    patient = patients_db.get(req.patient_id, {})
    post = {
        "id": f"post_{uuid.uuid4().hex[:10]}",
        "text": req.text[:500],  # 500 char limit
        "anonymous": req.anonymous,
        "display_name": "Anonymous" if req.anonymous else (patient.get("name", "")[:1] + "." if patient.get("name") else "Anonymous"),
        "stage": patient.get("treatment_stage", ""),
        "stage_display": STAGE_DISPLAY.get(patient.get("treatment_stage", ""), ""),
        "mood": "",
        "created_at": utc_iso(),
        "patient_id": req.patient_id,  # stored but NEVER returned to other patients
        "reactions": {"support": 0, "same": 0, "strength": 0},
        "reported_count": 0,
        "reported_by": [],
        "visible": True,
        "moderation_flags": mod["flags"],
        "crisis": mod["crisis"],
    }

    community_posts_db.insert(0, post)

    # Save to Firebase
    try:
        if firebase_db.ready:
            from firebase_db import _fb_ref, _enabled
            if _enabled and _fb_ref:
                _fb_ref.child('community_posts').child(post['id']).set(post)
    except Exception:
        pass

    # Record community post for phenotyping
    store = patient_signal_store.get(req.patient_id)
    if store:
        store.setdefault("community_posts", []).append({
            "text_length": len(req.text),
            "time_of_day": utc_now().hour,
            "stage": patient.get("treatment_stage", ""),
            "created_at": utc_iso(),
        })

    # Return without patient_id
    safe_post = {k: v for k, v in post.items() if k not in ('patient_id', 'reported_by')}
    return {"status": "ok", "post": safe_post}


@app.get("/community/posts")
async def list_community_posts(stage: str = None, limit: int = 20, before: str = None):
    """List visible community posts, optionally filtered by stage."""
    # Seed on first access if empty
    if not community_posts_db:
        seed_community_posts()
    posts = [p for p in community_posts_db if p.get("visible", True)]

    if stage and stage != "all":
        posts = [p for p in posts if p.get("stage") == stage]

    if before:
        posts = [p for p in posts if p.get("created_at", "") < before]

    # NEVER return patient_id or reported_by
    safe_posts = []
    for p in posts[:limit]:
        safe = {k: v for k, v in p.items() if k not in ('patient_id', 'reported_by')}
        safe_posts.append(safe)

    return {"posts": safe_posts, "total": len(posts)}


@app.post("/community/posts/{post_id}/react")
async def react_to_post(post_id: str, req: CommunityReactRequest):
    """React to a community post."""
    if req.reaction not in ("support", "same", "strength"):
        raise HTTPException(status_code=400, detail="Invalid reaction type")

    # Find post
    post = next((p for p in community_posts_db if p["id"] == post_id), None)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # One reaction per patient per post
    key = f"{post_id}"
    prev = community_reactions_db.get(key, {}).get(req.patient_id)
    if prev:
        # Remove previous reaction
        post["reactions"][prev] = max(0, post["reactions"].get(prev, 0) - 1)

    # Add new reaction (or toggle off if same)
    if prev == req.reaction:
        community_reactions_db.setdefault(key, {}).pop(req.patient_id, None)
    else:
        post["reactions"][req.reaction] = post["reactions"].get(req.reaction, 0) + 1
        community_reactions_db.setdefault(key, {})[req.patient_id] = req.reaction

    # Record community reaction for phenotyping
    store = patient_signal_store.get(req.patient_id)
    if store:
        store.setdefault("community_reactions", []).append({
            "type": req.reaction,
            "to_stage": post.get("stage", ""),
            "created_at": utc_iso(),
        })

    # "Me too" notification — notify post author when someone taps "same"
    if req.reaction == "same" and prev != "same":
        author_id = post.get("patient_id", "")
        if author_id and not author_id.startswith("seed_") and author_id != req.patient_id:
            notif = {
                "id": f"notif_{post_id}_{int(utc_now().timestamp())}",
                "type": "same_reaction",
                "post_id": post_id,
                "post_preview": post.get("text", "")[:50],
                "count": post["reactions"].get("same", 0),
                "latest_at": utc_iso(),
                "read": False,
            }
            community_notifications_db.setdefault(author_id, []).append(notif)

    return {"status": "ok", "reactions": post["reactions"]}


@app.post("/community/posts/{post_id}/report")
async def report_post(post_id: str, request: Request):
    """Report a community post."""
    body = await request.json()
    patient_id = body.get("patient_id", "")

    post = next((p for p in community_posts_db if p["id"] == post_id), None)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if patient_id not in post.get("reported_by", []):
        post.setdefault("reported_by", []).append(patient_id)
        post["reported_count"] = len(post["reported_by"])

    # Auto-hide after 2 reports
    if post["reported_count"] >= 2:
        post["visible"] = False

    return {"status": "ok", "reported_count": post["reported_count"], "hidden": not post["visible"]}


@app.get("/community/stages")
async def list_community_stages():
    """List active stages with post counts."""
    stage_counts = {}
    now_iso = utc_iso()
    today = now_iso[:10]

    for p in community_posts_db:
        if not p.get("visible", True):
            continue
        s = p.get("stage", "other")
        if s not in stage_counts:
            stage_counts[s] = {"stage": s, "stage_display": STAGE_DISPLAY.get(s, s), "posts": 0, "active_today": 0}
        stage_counts[s]["posts"] += 1
        if p.get("created_at", "")[:10] == today:
            stage_counts[s]["active_today"] += 1

    stages = sorted(stage_counts.values(), key=lambda x: x["posts"], reverse=True)
    return {"stages": stages}


# ── "Patients Like You" — Aggregated Stage Insights ────────────────────

@app.get("/community/insights/stage/{stage}", dependencies=[Depends(verify_clinician_api_key)])
async def community_stage_insights(stage: str):
    """Aggregated, anonymized insights for a treatment stage."""
    # Aggregate check-in scores by stage
    stage_checkins = []
    for pid, checkins in checkins_db.items():
        patient = patients_db.get(pid, {})
        if patient.get("treatment_stage") == stage:
            stage_checkins.extend(checkins[-10:])  # Last 10 per patient

    # Count active patients at this stage
    active_patients = sum(1 for p in patients_db.values() if p.get("treatment_stage") == stage)

    # Calculate emotional summary
    emotional_summary = {"avg_mood": 5.0, "avg_anxiety": 5.0, "most_common_concerns": [], "mood_trend": "stable"}
    if stage_checkins:
        moods = [c.get("mood", 5) for c in stage_checkins]
        anxieties = [c.get("anxiety", 5) for c in stage_checkins]
        emotional_summary["avg_mood"] = round(sum(moods) / len(moods), 1)
        emotional_summary["avg_anxiety"] = round(sum(anxieties) / len(anxieties), 1)

    # Aggregate community themes by stage
    stage_posts = [p for p in community_posts_db if p.get("stage") == stage and p.get("visible", True)]
    community_themes = []
    if stage_posts:
        # Simple keyword extraction for themes
        all_text = " ".join(p.get("text", "") for p in stage_posts[-50:]).lower()
        theme_keywords = {
            "Difficulty waiting": ["wait", "waiting", "patience"],
            "Symptom checking": ["symptom", "symptoms", "cramping", "bleeding", "spotting"],
            "Fear of results": ["scared", "afraid", "worried", "fear", "terrified"],
            "Medication concerns": ["medication", "injection", "side effects", "dose"],
            "Emotional exhaustion": ["exhausted", "tired", "drained", "overwhelmed"],
            "Isolation feelings": ["alone", "lonely", "no one understands", "isolated"],
        }
        for theme, keywords in theme_keywords.items():
            if any(kw in all_text for kw in keywords):
                community_themes.append(theme)

    # Common questions from conversations
    common_questions = []
    stage_conversations = []
    for pid, convs in conversations_db.items():
        patient = patients_db.get(pid, {})
        if patient.get("treatment_stage") == stage:
            user_msgs = [m for m in convs[-20:] if m.get("role") == "user" and "?" in m.get("content", "")]
            stage_conversations.extend(user_msgs)

    # Simple frequency analysis of question topics
    question_topics = {
        "Is cramping normal?": ["cramp", "cramping"],
        "When should I test?": ["test", "testing", "pregnancy test"],
        "Does progesterone cause symptoms?": ["progesterone", "pessary"],
        "What are my chances?": ["chance", "success rate", "odds"],
        "Is bleeding normal?": ["bleed", "bleeding", "spotting"],
    }
    for q, keywords in question_topics.items():
        count = sum(1 for m in stage_conversations if any(kw in m.get("content", "").lower() for kw in keywords))
        if count > 0:
            common_questions.append({"question": q, "frequency": count})
    common_questions.sort(key=lambda x: x["frequency"], reverse=True)

    return {
        "stage": stage,
        "stage_display": STAGE_DISPLAY.get(stage, stage),
        "active_patients": active_patients,
        "period": "last_30_days",
        "emotional_summary": emotional_summary,
        "common_questions": common_questions[:5],
        "community_themes": community_themes[:5],
        "support_that_helped": [
            "Grounding exercises before procedures",
            "Distraction activities during TWW",
            "Talking to someone who's been through it"
        ]
    }


# ── Community Replies (max 1 level, max 3 per post) ──────────────────

community_replies_db: dict = {}  # {post_id: [replies]}
community_notifications_db: dict = {}  # {patient_id: [notifs]}


@app.post("/community/posts/{post_id}/reply")
async def reply_to_post(post_id: str, request: Request):
    """Add a reply to a community post (max 3 per post, max 200 chars)."""
    body = await request.json()
    patient_id = body.get("patient_id", "")
    text = body.get("text", "").strip()

    if not text or not patient_id:
        raise HTTPException(status_code=400, detail="Missing text or patient_id")
    if len(text) > 200:
        text = text[:200]

    post = next((p for p in community_posts_db if p["id"] == post_id), None)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    replies = community_replies_db.get(post_id, [])
    if len(replies) >= 3:
        raise HTTPException(status_code=400, detail="Maximum 3 replies per post")

    # Moderate
    mod = moderate_community_post(text, patient_id)
    if not mod["approved"]:
        return {"status": "rejected", "message": mod.get("message", "Post rejected")}

    # Get replier mood
    latest_checkins = checkins_db.get(patient_id, [])
    mood = latest_checkins[-1].get("mood", 5) if latest_checkins else 5

    reply = {
        "id": f"reply_{post_id}_{len(replies)}_{int(utc_now().timestamp())}",
        "post_id": post_id,
        "text": text,
        "anonymous": True,
        "display_name": "Anonymous",
        "mood": "hopeful" if mood >= 6 else "anxious" if mood >= 4 else "sad",
        "created_at": utc_iso(),
        "patient_id": patient_id,
        "reported": False,
        "visible": True,
    }

    community_replies_db.setdefault(post_id, []).append(reply)

    # Save to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("community_replies").child(post_id).child(reply["id"]).set(reply)
    except Exception:
        pass

    # Return without patient_id
    safe = {k: v for k, v in reply.items() if k != "patient_id"}
    return {"status": "ok", "reply": safe}


@app.get("/community/posts/{post_id}/replies")
async def list_replies(post_id: str):
    """List replies for a post."""
    replies = community_replies_db.get(post_id, [])
    safe = [{k: v for k, v in r.items() if k != "patient_id"} for r in replies if r.get("visible", True)]
    return {"replies": safe, "count": len(safe)}


@app.get("/community/active-count")
async def community_active_count(stage: str = None):
    """Count active patients at a stage (activity in last 7 days)."""
    now = utc_now()
    seven_days_ago = (now - timedelta(days=7)).isoformat()
    count = 0
    for pid, patient in patients_db.items():
        if stage and patient.get("treatment_stage") != stage:
            continue
        # Check recent activity
        checkins = checkins_db.get(pid, [])
        convs = conversations_db.get(pid, [])
        has_recent = False
        if checkins and checkins[-1].get("date", "") >= seven_days_ago:
            has_recent = True
        if convs and convs[-1].get("timestamp", "") >= seven_days_ago:
            has_recent = True
        if has_recent:
            count += 1
    return {"count": max(count, 1), "stage": stage or "all"}


@app.get("/community/notifications/{patient_id}")
async def get_notifications(patient_id: str):
    """Get unread community notifications for a patient."""
    notifs = community_notifications_db.get(patient_id, [])
    unread = [n for n in notifs if not n.get("read", False)]
    return {"notifications": unread, "count": len(unread)}


@app.post("/community/notifications/{patient_id}/read")
async def mark_notifications_read(patient_id: str):
    """Mark all community notifications as read."""
    notifs = community_notifications_db.get(patient_id, [])
    for n in notifs:
        n["read"] = True
    return {"status": "ok"}


# ── Patient Notes (sent via egg agent to care team) ──────────────────

@app.post("/patient/{patient_id}/send-note")
async def patient_send_note(patient_id: str, request: Request):
    """Patient sends a note to their care team via the egg companion."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty note")

    note = {
        "text": text,
        "timestamp": utc_iso(),
        "read": False,
        "source": body.get("source", "egg_agent"),
        "patient_id": patient_id,
    }

    # Save to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patient_notes").child(patient_id).push(note)
    except Exception:
        pass

    # Also create a clinician alert
    patient = patients_db.get(patient_id, {})
    clinician_alerts.append({
        "type": "patient_note",
        "patient_id": patient_id,
        "patient_name": patient.get("patient_name") or patient.get("name") or patient_id,
        "message": f"Patient sent a note: \"{text[:100]}\"",
        "timestamp": utc_iso(),
        "acknowledged": False,
    })

    return {"status": "sent", "timestamp": note["timestamp"]}


# ── Clinician → Patient Messaging ────────────────────────────────────

@app.post("/clinician/patient/{patient_id}/send-message", dependencies=[Depends(verify_clinician_api_key)])
async def clinician_send_message(patient_id: str, request: Request):
    """Send a message from clinician to patient."""
    body = await request.json()
    msg = {
        "text": body.get("message", ""),
        "content": body.get("message", ""),
        "from_role": body.get("from_role", "doctor"),
        "sender_name": body.get("sender_name", "Your care team"),
        "timestamp": utc_iso(),
        "type": "clinician_message",
        "read": False,
        "role": "care_team",
    }
    # Save to in-memory
    conversations_db.setdefault(patient_id, []).append(msg)
    # Save to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("clinician_messages").child(patient_id).push(msg)
    except Exception:
        pass
    return {"status": "sent", "timestamp": msg["timestamp"]}


@app.get("/patient/{patient_id}/clinician-messages")
async def get_clinician_messages(patient_id: str):
    """Get unread clinician messages. Does NOT mark as read."""
    messages = []
    try:
        if firebase_db and firebase_db._fb_ref:
            msgs = firebase_db._fb_ref.child("clinician_messages").child(patient_id).get()
            if msgs and isinstance(msgs, dict):
                for key, val in msgs.items():
                    if isinstance(val, dict) and not val.get("read", False):
                        val["id"] = key
                        messages.append(val)
    except Exception as e:
        logger.warning(f"Firebase clinician messages read failed: {e}")
    # Also check in-memory
    convs = conversations_db.get(patient_id, [])
    for msg in convs:
        if msg.get("type") == "clinician_message" and not msg.get("read", False):
            messages.append({**msg, "read": False})
    return {"messages": messages}


@app.post("/patient/{patient_id}/clinician-messages/mark-read")
async def mark_clinician_messages_read(patient_id: str):
    """Mark all clinician messages as read for a patient."""
    try:
        if firebase_db and firebase_db._fb_ref:
            msgs = firebase_db._fb_ref.child("clinician_messages").child(patient_id).get()
            if msgs and isinstance(msgs, dict):
                for key, val in msgs.items():
                    if isinstance(val, dict) and not val.get("read", False):
                        firebase_db._fb_ref.child("clinician_messages").child(patient_id).child(key).update({"read": True})
    except Exception:
        pass
    convs = conversations_db.get(patient_id, [])
    for msg in convs:
        if msg.get("type") == "clinician_message":
            msg["read"] = True
    return {"status": "ok"}


# ── Patient Notes (sent via egg agent to care team) ──────────────────

@app.post("/patient/{patient_id}/send-note")
async def send_patient_note(patient_id: str, request: Request):
    """Patient sends a note to their care team via the egg agent."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note text required")

    note = {
        "text": text,
        "timestamp": utc_iso(),
        "read": False,
        "source": body.get("source", "egg_agent"),
    }

    # Save to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patient_notes").child(patient_id).push(note)
    except Exception:
        pass

    # Also create a clinician alert
    _sync_escalation(patient_id, {
        "level": "AMBER",
        "reason": f"Patient sent a note: {text[:80]}",
        "signals": ["patient_note_via_egg"],
        "timestamp": utc_iso(),
    })

    return {"status": "sent", "timestamp": note["timestamp"]}


# ── Comfort Check-in (procedure-day ratings) ────────────────────────

@app.post("/patient/{patient_id}/comfort-report")
async def submit_comfort_report(patient_id: str, request: Request):
    """Save a comfort check-in report from the egg agent."""
    body = await request.json()
    report = {
        "patient_id": patient_id,
        "procedure": body.get("procedure", "unknown"),
        "ratings": body.get("ratings", {}),
        "note": body.get("note", ""),
        "want_er": body.get("want_er", False),
        "escalation_level": body.get("escalation_level", "GREEN"),
        "timestamp": body.get("timestamp", utc_iso()),
        "source": "egg_comfort_checkin",
    }

    # Save to Firebase
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("comfort_reports").child(patient_id).push(report)
    except Exception:
        pass

    # If AMBER/RED, create escalation alert
    if report["escalation_level"] in ("RED", "AMBER"):
        _sync_escalation(patient_id, {
            "level": report["escalation_level"],
            "reason": f"Comfort check-in: {report['procedure']} — {report['escalation_level']}",
            "signals": ["comfort_report", "want_er" if report["want_er"] else "high_discomfort"],
            "timestamp": report["timestamp"],
        })

    # If ER flag, also save as urgent note
    if report.get("want_er"):
        try:
            if firebase_db and firebase_db._fb_ref:
                firebase_db._fb_ref.child("patient_notes").child(patient_id).push({
                    "text": f"URGENT: Patient considering ER after {report['procedure']}. Scores: {report['ratings']}",
                    "timestamp": report["timestamp"],
                    "read": False,
                    "source": "egg_comfort_er_flag",
                    "urgent": True,
                })
        except Exception:
            pass

    return {"status": "saved", "escalation_level": report["escalation_level"]}


@app.get("/clinician/patient/{patient_id}/comfort-reports", dependencies=[Depends(verify_clinician_api_key)])
async def get_comfort_reports(patient_id: str):
    """Get comfort check-in reports for a patient."""
    reports = []
    try:
        if firebase_db and firebase_db._fb_ref:
            data = firebase_db._fb_ref.child("comfort_reports").child(patient_id).get()
            if data and isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, dict):
                        val["id"] = key
                        reports.append(val)
                reports.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    except Exception:
        pass
    return {"reports": reports}




# ── Cycle Management (medications, dates, protocol) ──────────────────

@app.get("/clinician/patient/{patient_id}/cycle", dependencies=[Depends(verify_clinician_api_key)])
async def get_patient_cycle(patient_id: str):
    """Get patient's current cycle data."""
    try:
        if firebase_db and firebase_db._fb_ref:
            cycle = firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").get()
            # Log what Firebase actually returned — especially dose data
            ms = (cycle or {}).get('medications_simple', {})
            if ms and isinstance(ms, dict):
                dose_detail = {k: {'name': v.get('name','?'), 'has_doses': bool(v.get('doses')), 'dose_keys': list(v.get('doses', {}).keys())} for k,v in ms.items() if isinstance(v, dict)}
                logger.info(f"Cycle GET for {patient_id}: meds={dose_detail}")
            return cycle or {}
    except Exception as e:
        return {"error": str(e)}
    return {}

@app.post("/clinician/patient/{patient_id}/cycle", dependencies=[Depends(verify_clinician_api_key)])
async def update_patient_cycle(patient_id: str, request: Request):
    """Update patient cycle data.

    Uses .update() instead of .set() to avoid wiping sibling data if
    Firebase read returns stale/empty data (multi-instance Cloud Run).
    """
    # Reject bogus patient IDs created by the container- prefix bug
    if patient_id.startswith("container-"):
        logger.warning(f"Rejected container-prefixed patient_id: {patient_id}")
        return {"status": "rejected", "reason": "invalid patient_id"}
    data = await request.json()
    # Log incoming payload with dose details for debugging
    ms_incoming = data.get('medications_simple', {})
    dose_summary = {k: {
        'name': v.get('name', '?'),
        'dose_keys': list(v.get('doses', {}).keys()),
        'dose_vals': list(v.get('doses', {}).values())
    } for k, v in ms_incoming.items()} if isinstance(ms_incoming, dict) else 'not-dict'
    logger.info(f"Cycle update request for {patient_id}: incoming_keys={list(data.keys())}, meds_detail={dose_summary}")
    try:
        if firebase_db and firebase_db._fb_ref:
            ref = firebase_db._fb_ref.child("patients").child(patient_id).child("cycle")

            # Build the update payload — only keys the caller sent
            update_payload = {}
            for key in data:
                if key == 'key_dates' and isinstance(data[key], dict):
                    # key_dates needs merge with existing — read just this child
                    try:
                        kd = ref.child('key_dates').get() or {}
                    except Exception:
                        kd = {}
                    kd.update(data[key])
                    update_payload['key_dates'] = kd
                else:
                    update_payload[key] = data[key]
            update_payload['updated_at'] = utc_iso()

            # Only clear medications_simple if caller explicitly sent it as empty {}
            # Never clear if it contains any entries (even without doses yet)
            ms = update_payload.get('medications_simple', None)
            if ms is not None and ms == {}:
                # Empty dict sent — don't write it, don't delete existing
                update_payload.pop('medications_simple', None)
                logger.info(f"Skipped empty medications_simple for {patient_id}")
            elif ms is not None and isinstance(ms, dict) and len(ms) > 0:
                # Check if incoming meds have ALL empty doses — likely a focusout race
                # If existing data has doses and incoming doesn't, merge to preserve doses
                try:
                    existing = ref.child('medications_simple').get() or {}
                    if existing and isinstance(existing, dict):
                        incoming_has_any_doses = any(
                            bool(m.get('doses', {})) for m in ms.values() if isinstance(m, dict)
                        )
                        existing_has_doses = any(
                            bool(m.get('doses', {})) for m in existing.values() if isinstance(m, dict)
                        )
                        if not incoming_has_any_doses and existing_has_doses:
                            logger.warning(f"Blocked dose-stripping save for {patient_id}: incoming has 0 doses, existing has doses")
                            update_payload.pop('medications_simple', None)
                except Exception as me:
                    logger.warning(f"medications_simple merge check failed: {me}")

            # Auto-correct start_date if it was saved with UTC (off-by-one in AEST)
            try:
                sd = update_payload.get('start_date', '')
                ua = update_payload.get('updated_at', '')
                if sd and ua:
                    aest = timezone(timedelta(hours=10))
                    ua_dt = datetime.fromisoformat(ua.replace('Z', '+00:00'))
                    aest_date = ua_dt.astimezone(aest).strftime('%Y-%m-%d')
                    if sd != aest_date:
                        sd_date = datetime.strptime(sd, '%Y-%m-%d').date()
                        aest_d = ua_dt.astimezone(aest).date()
                        if abs((aest_d - sd_date).days) == 1:
                            update_payload['start_date'] = aest_date
                            logger.info(f"Auto-corrected start_date {sd} -> {aest_date} for {patient_id}")
            except Exception as ce:
                logger.warning(f"start_date correction skipped: {ce}")

            # .update() merges top-level keys without wiping siblings
            med_count = len(update_payload.get('medications_simple', {})) if 'medications_simple' in update_payload else 'n/a'
            ms_writing = update_payload.get('medications_simple', None)
            if ms_writing and isinstance(ms_writing, dict):
                dose_detail = {k: {'name': v.get('name','?'), 'doses': v.get('doses',{})} for k,v in ms_writing.items() if isinstance(v, dict)}
                logger.info(f"Cycle WRITING for {patient_id}: med_count={med_count}, dose_detail={dose_detail}")
            else:
                logger.info(f"Cycle WRITING for {patient_id}: keys={list(update_payload.keys())}, medications_simple={'SKIPPED' if ms_writing is None else 'present'}")
            ref.update(update_payload)
            # Read-back verification
            try:
                readback = ref.child('medications_simple').get()
                rb_doses = {k: list(v.get('doses', {}).keys()) for k,v in (readback or {}).items() if isinstance(v, dict)}
                logger.info(f"Cycle READBACK for {patient_id}: {rb_doses}")
            except Exception as rb_err:
                logger.warning(f"Cycle readback failed for {patient_id}: {rb_err}")
            return {"status": "updated"}
    except Exception as e:
        logger.error(f"Cycle update error for {patient_id}: {e}")
        return {"error": str(e)}
    return {"status": "no_firebase"}

@app.post("/clinician/notify-opu/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def notify_patient_opu(patient_id: str, request: Request):
    """Notify patient about confirmed OPU: updates Firebase, sends message."""
    try:
        data = await request.json()
        trigger_drug = data.get("trigger_drug", "Ovidrel")
        trigger_time = data.get("trigger_time", "20:30")
        opu_date = data.get("opu_date", "")
        opu_time = data.get("opu_time_confirmed") or data.get("opu_time_calculated", "")
        doctor = data.get("doctor", "your doctor")

        # Update opu_schedule in Firebase with notification status
        if firebase_db and firebase_db._fb_ref:
            ref = firebase_db._fb_ref.child("patients").child(patient_id).child("cycle")
            ref.child("opu_schedule").update({
                "patient_notified": True,
                "notification_sent_at": utc_iso(),
            })
            # Also add OPU as a procedure on the patient's calendar via medications_simple
            existing_meds = ref.child("medications_simple").get() or {}
            # Find next med index
            max_idx = -1
            for k in existing_meds:
                try:
                    idx = int(k.replace("med_", ""))
                    if idx > max_idx:
                        max_idx = idx
                except (ValueError, AttributeError):
                    pass
            # Add trigger and OPU entries if not already present
            has_opu = any(
                isinstance(m, dict) and "OPU" in m.get("name", "")
                for m in existing_meds.values()
            )
            if not has_opu:
                ref.child("medications_simple").child(f"med_{max_idx + 1}").update({
                    "name": "OPU (egg retrieval)",
                    "doses": {}  # Will be populated by dashboard grid
                })

        # Send message to patient via clinician message system
        msg = (
            f"Your egg collection is confirmed for {opu_date} at {opu_time}. "
            f"Trigger injection: {trigger_drug} tonight at {trigger_time}. "
            f"Fasting from midnight — no food or water. "
            f"Your doctor: {doctor}. You've got this."
        )
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("clinician_messages").child(patient_id).push({
                "from": "clinic",
                "message": msg,
                "timestamp": utc_iso(),
                "read": False,
                "type": "opu_confirmation",
            })

        return {"success": True, "notification_sent": True, "message": msg}
    except Exception as e:
        logger.error(f"notify-opu error for {patient_id}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/clinician/patient/{patient_id}/post-opu/ohss/{date_str}", dependencies=[Depends(verify_clinician_api_key)])
async def save_ohss_daily(patient_id: str, date_str: str, request: Request):
    """Save OHSS daily monitoring data."""
    try:
        data = await request.json()
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").child("ohss_daily").child(date_str).update(data)
        return {"status": "saved"}
    except Exception as e:
        logger.error(f"OHSS save error for {patient_id}/{date_str}: {e}")
        return {"error": str(e)}


@app.post("/clinician/patient/{patient_id}/post-opu/complications", dependencies=[Depends(verify_clinician_api_key)])
async def save_complications(patient_id: str, request: Request):
    """Save post-OPU complications."""
    try:
        data = await request.json()
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").child("complications").update(data)
        return {"status": "saved"}
    except Exception as e:
        logger.error(f"Complications save error for {patient_id}: {e}")
        return {"error": str(e)}


@app.post("/clinician/patient/{patient_id}/escalate", dependencies=[Depends(verify_clinician_api_key)])
async def escalate_patient(patient_id: str, request: Request):
    """Manually escalate a patient's risk level."""
    try:
        data = await request.json()
        level = data.get("level", "AMBER")
        reason = data.get("reason", "Manual escalation")
        source = data.get("source", "clinician")
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).update({
                "escalation_level": level,
                "escalation_reason": reason,
                "escalation_source": source,
                "escalated_at": utc_iso(),
            })
        # Push to alert queue
        alert_queue.insert(0, {
            "type": "manual_escalation",
            "patient_id": patient_id,
            "level": level,
            "summary": reason,
            "source": source,
            "timestamp": utc_iso(),
            "acknowledged": False,
        })
        alert_queue[:] = alert_queue[:100]
        return {"status": "escalated", "level": level}
    except Exception as e:
        logger.error(f"Escalation error for {patient_id}: {e}")
        return {"error": str(e)}


@app.post("/patient/{patient_id}/post-opu-symptoms")
async def submit_post_opu_symptoms(patient_id: str, request: Request):
    """Patient self-reports post-OPU symptoms."""
    try:
        data = await request.json()
        data["submitted_at"] = utc_iso()
        # Calculate OHSS grade
        pain = int(data.get("pain", 0))
        nausea = int(data.get("nausea", 0))
        mobility = data.get("mobility", "normal")
        grade = "none"
        if pain >= 7 or nausea >= 3 or mobility == "cannot move":
            grade = "severe"
        elif pain >= 4 or nausea >= 2 or mobility == "difficult":
            grade = "moderate"
        elif pain >= 1 or nausea >= 1:
            grade = "mild"
        data["calculated_grade"] = grade

        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").child("patient_reports").push(data)
            # Auto-escalate if moderate+
            if grade in ("severe", "critical"):
                firebase_db._fb_ref.child("patients").child(patient_id).update({
                    "escalation_level": "RED",
                    "escalation_reason": f"Patient-reported OHSS symptoms: pain {pain}/10, nausea {nausea}/3, mobility: {mobility}",
                    "escalated_at": utc_iso(),
                })
                alert_queue.insert(0, {
                    "type": "patient_ohss_alert", "patient_id": patient_id, "level": "RED",
                    "summary": f"Patient reports severe post-OPU symptoms (pain {pain}/10)",
                    "timestamp": utc_iso(), "acknowledged": False,
                })
            elif grade == "moderate":
                firebase_db._fb_ref.child("patients").child(patient_id).update({
                    "escalation_level": "AMBER",
                    "escalation_reason": f"Patient-reported moderate symptoms: pain {pain}/10",
                    "escalated_at": utc_iso(),
                })
        return {"status": "saved", "grade": grade}
    except Exception as e:
        logger.error(f"Post-OPU symptoms error for {patient_id}: {e}")
        return {"error": str(e)}


@app.post("/clinician/patient/{patient_id}/cycle/medication", dependencies=[Depends(verify_clinician_api_key)])
async def add_cycle_medication(patient_id: str, request: Request):
    """Add a medication to the patient's cycle."""
    data = await request.json()
    try:
        med_id = (data.get('name', 'med').lower().replace(' ', '_').replace('-', '_')
                  + '_' + str(int(utc_now().timestamp()) % 100000))
        med = {
            'name': data.get('name', ''),
            'category': data.get('category', 'other'),
            'dose': data.get('dose', 0),
            'unit': data.get('unit', ''),
            'start_date': data.get('start_date', utc_now().strftime('%Y-%m-%d')),
            'end_date': data.get('end_date'),
        }
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").child("medications").child(med_id).set(med)
        return {"status": "added", "id": med_id}
    except Exception as e:
        return {"error": str(e)}

@app.delete("/clinician/patient/{patient_id}/cycle/medication/{med_id}", dependencies=[Depends(verify_clinician_api_key)])
async def delete_cycle_medication(patient_id: str, med_id: str):
    """Remove a medication."""
    try:
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").child("medications").child(med_id).delete()
        return {"status": "deleted"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/clinician/patient/{patient_id}/cycle/medication/{med_id}/dose", dependencies=[Depends(verify_clinician_api_key)])
async def update_med_dose(patient_id: str, med_id: str, request: Request):
    """Update a specific date's dose."""
    data = await request.json()
    try:
        date_str = data.get('date')
        dose = data.get('dose')
        if firebase_db and firebase_db._fb_ref:
            firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").child("medications").child(med_id).child("doses").child(date_str).set(dose)
        return {"status": "updated"}
    except Exception as e:
        return {"error": str(e)}


# ── Static File Serving ────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse("index.html")


@app.get("/clinician-dashboard.html")
async def serve_dashboard():
    return FileResponse("clinician-dashboard.html")


@app.get("/firebase-messaging-sw.js")
async def serve_fcm_sw():
    """Serve FCM service worker from root path (required by browser SW scope rules)."""
    return FileResponse("firebase-messaging-sw.js", media_type="application/javascript")


@app.post("/api/send-med-reminders")
async def send_med_reminders():
    """Send push notifications for today's unfinished medications."""
    try:
        import firebase_admin
        from firebase_admin import messaging as fcm_messaging
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        melb = ZoneInfo("Australia/Melbourne")
        now = datetime.now(melb)
        today_str = now.strftime("%Y-%m-%d")
        sent = 0
        errors = 0

        if not firebase_db or not firebase_db._fb_ref:
            return {"sent": 0, "errors": 0, "date": today_str, "note": "Firebase not available"}

        patients_ref = firebase_db._fb_ref.child("patients")
        patients = patients_ref.get() or {}

        for pid, pdata in patients.items():
            if not isinstance(pdata, dict):
                continue
            token = pdata.get("fcm_token")
            if not token:
                continue

            # Check cycle medications for today
            cycle_meds = pdata.get("cycle", {}).get("medications_simple", {})
            med_names = []
            for med_key, med_data in cycle_meds.items():
                if not isinstance(med_data, dict):
                    continue
                med_name = med_data.get("name", med_key)
                # Check if any day column maps to today
                start_date = pdata.get("cycle", {}).get("start_date")
                if not start_date:
                    continue
                for dk, dv in med_data.items():
                    if not dk.startswith("d"):
                        continue
                    try:
                        day_num = int(dk[1:])
                    except (ValueError, IndexError):
                        continue
                    sp = start_date.split("-")
                    d = datetime.now()
                    try:
                        d = datetime(int(sp[0]), int(sp[1]) - 1 + 1, int(sp[2]))
                        from datetime import timedelta as _td
                        d = d + _td(days=day_num - 1)
                        ds = d.strftime("%Y-%m-%d")
                    except Exception:
                        continue
                    if ds == today_str and dv:
                        med_names.append(med_name)
                        break

            if not med_names:
                continue

            try:
                body = ", ".join(med_names[:3])
                if len(med_names) > 3:
                    body += f" +{len(med_names) - 3} more"
                msg = fcm_messaging.Message(
                    notification=fcm_messaging.Notification(
                        title="\U0001f48a Medication Reminder",
                        body=f"Time to take {body}",
                    ),
                    token=token,
                )
                fcm_messaging.send(msg)
                sent += 1
            except Exception as e:
                logger.warning(f"FCM send error for {pid}: {e}")
                errors += 1

        return {"sent": sent, "errors": errors, "date": today_str}
    except Exception as e:
        logger.error(f"send-med-reminders error: {e}")
        return {"sent": 0, "errors": 1, "error": str(e)}


@app.post("/api/send-companion-nudge")
async def send_companion_nudge():
    """Send a gentle check-in nudge to all patients with FCM tokens."""
    try:
        import firebase_admin
        from firebase_admin import messaging as fcm_messaging

        if not firebase_db or not firebase_db._fb_ref:
            return {"sent": 0, "note": "Firebase not available"}

        patients_ref = firebase_db._fb_ref.child("patients")
        patients = patients_ref.get() or {}
        sent = 0

        for pid, pdata in patients.items():
            if not isinstance(pdata, dict):
                continue
            token = pdata.get("fcm_token")
            if not token:
                continue
            try:
                msg = fcm_messaging.Message(
                    notification=fcm_messaging.Notification(
                        title="\U0001f95a Hey there!",
                        body="Just checking in \u2014 how are you feeling today?",
                    ),
                    token=token,
                )
                fcm_messaging.send(msg)
                sent += 1
            except Exception:
                pass

        return {"sent": sent}
    except Exception as e:
        logger.error(f"send-companion-nudge error: {e}")
        return {"sent": 0, "error": str(e)}


# ── Run ───────────────────────────────────────────────────────────────



# (Duplicate cycle endpoints removed — used firebase_db.reference() which would
#  double-prefix paths. Canonical endpoints use firebase_db._fb_ref.child().)


# ── Admin diagnostic endpoints (TEMPORARY — remove after debugging) ────

@app.get("/admin/firebase-dump/{patient_id}")
async def firebase_dump(patient_id: str):
    """Dump ALL possible Firebase locations where cycle data might exist."""
    import firebase_admin.db as fb_db_mod
    results = {}
    paths = {
        "melod_ai/patients/{pid}/cycle": None,
        "melod_ai/melod_ai/patients/{pid}/cycle": None,
        "patients/{pid}/cycle": None,
        "melod_ai/cycle_events/{pid}": None,
        "cycle_events/{pid}": None,
        "melod_ai/patients/{pid}": "KEYS_ONLY",
    }
    for path_template, mode in paths.items():
        path = path_template.replace("{pid}", patient_id)
        try:
            ref = fb_db_mod.reference(path)
            data = ref.get()
            if mode == "KEYS_ONLY" and isinstance(data, dict):
                results[path] = {"exists": True, "keys": list(data.keys()), "has_cycle": "cycle" in data}
            else:
                results[path] = {"exists": data is not None, "data": data}
        except Exception as e:
            results[path] = {"error": str(e)}
    # Also check in-memory caches
    results["in_memory"] = {
        "cycle_events_db": cycle_events_db.get(patient_id, "NOT_FOUND"),
    }
    results["note"] = "Dashboard also caches in browser localStorage key 'medgrid_{pid}'. Clear manually in browser DevTools."
    return results

@app.post("/admin/nuke-cycle/{patient_id}")
async def nuke_cycle(patient_id: str):
    """Nuclear cleanup: delete cycle data from ALL possible Firebase paths."""
    import firebase_admin.db as fb_db_mod
    deleted = []
    paths_to_nuke = [
        f"melod_ai/patients/{patient_id}/cycle",
        f"melod_ai/melod_ai/patients/{patient_id}/cycle",
        f"patients/{patient_id}/cycle",
        f"melod_ai/cycle_events/{patient_id}",
        f"cycle_events/{patient_id}",
    ]
    for path in paths_to_nuke:
        try:
            ref = fb_db_mod.reference(path)
            data = ref.get()
            if data is not None:
                ref.delete()
                deleted.append({"path": path, "had_data": True})
            else:
                deleted.append({"path": path, "had_data": False})
        except Exception as e:
            deleted.append({"path": path, "error": str(e)})
    # Clear in-memory caches
    if patient_id in cycle_events_db:
        del cycle_events_db[patient_id]
        deleted.append({"path": "in_memory:cycle_events_db", "had_data": True})
    return {"deleted": deleted, "note": "Also clear browser localStorage key 'medgrid_{pid}' in the clinician's browser DevTools."}

@app.post("/admin/nuke-all-cycles")
async def nuke_all_cycles():
    """Nuclear cleanup: delete ALL cycle data for ALL patients from ALL paths."""
    import firebase_admin.db as fb_db_mod
    results = []
    patient_ids = set()
    try:
        patients = fb_db_mod.reference("melod_ai/patients").get()
        if patients and isinstance(patients, dict):
            patient_ids.update(patients.keys())
    except Exception:
        pass
    try:
        patients2 = fb_db_mod.reference("patients").get()
        if patients2 and isinstance(patients2, dict):
            patient_ids.update(patients2.keys())
    except Exception:
        pass
    for pid in patient_ids:
        for path in [f"melod_ai/patients/{pid}/cycle", f"melod_ai/melod_ai/patients/{pid}/cycle", f"patients/{pid}/cycle"]:
            try:
                ref = fb_db_mod.reference(path)
                data = ref.get()
                if data is not None:
                    ref.delete()
                    results.append({"patient": pid, "path": path, "deleted": True})
            except Exception as e:
                results.append({"patient": pid, "path": path, "error": str(e)})
    for evt_path in ["melod_ai/cycle_events", "cycle_events"]:
        try:
            ref = fb_db_mod.reference(evt_path)
            if ref.get():
                ref.delete()
                results.append({"path": evt_path, "deleted": True})
        except Exception:
            pass
    cycle_events_db.clear()
    results.append({"path": "in_memory:cycle_events_db", "cleared": True})
    return {"results": results, "patients_found": list(patient_ids), "note": "Clear browser localStorage on all clinician browsers too."}

@app.post("/admin/wipe-all")
async def wipe_all():
    """Nuclear: delete EVERY cycle-related path for ALL patients across all possible locations."""
    import firebase_admin.db as fb_db_mod
    wiped = []
    # Wipe all known stale paths
    for root_path in [
        "melod_ai/melod_ai",  # Double-prefix bug artifacts
        "cycle_events",       # Root-level cycle events
    ]:
        try:
            ref = fb_db_mod.reference(root_path)
            if ref.get() is not None:
                ref.delete()
                wiped.append(root_path)
        except Exception:
            pass
    # Wipe root-level patients (non-prefixed duplicate)
    try:
        ref = fb_db_mod.reference("patients")
        if ref.get() is not None:
            ref.delete()
            wiped.append("patients")
    except Exception:
        pass
    # Wipe cycle data from canonical path for each patient
    try:
        patients = fb_db_mod.reference("melod_ai/patients").get()
        if patients and isinstance(patients, dict):
            for pid in patients:
                try:
                    ref = fb_db_mod.reference(f"melod_ai/patients/{pid}/cycle")
                    if ref.get() is not None:
                        ref.delete()
                        wiped.append(f"melod_ai/patients/{pid}/cycle")
                except Exception:
                    pass
    except Exception:
        pass
    # Clear in-memory
    cycle_events_db.clear()
    return {"wiped": True, "paths_deleted": wiped}


@app.get("/patient/{patient_id}/cycle-meds")
async def get_patient_cycle_meds(patient_id: str):
    """Public endpoint for patient app to read their own cycle data."""
    try:
        if firebase_db and firebase_db._fb_ref:
            cycle = firebase_db._fb_ref.child("patients").child(patient_id).child("cycle").get()
            return cycle or {}
        return {}
    except Exception as e:
        return {}




@app.get("/debug/firebase-check/{patient_id}")
async def debug_firebase(patient_id: str):
    """Temporary debug — check Firebase connectivity."""
    result = {"checks": []}
    try:
        from firebase_db import _fb_ref, _enabled
        result["enabled"] = _enabled
        result["fb_ref_exists"] = _fb_ref is not None
        if _fb_ref:
            result["fb_ref_path"] = str(_fb_ref.path)
            test = _fb_ref.child("patients").child(patient_id).child("cycle").get()
            result["cycle_data"] = test
            result["checks"].append("direct_read_ok")
    except Exception as e:
        result["error"] = str(e)
    return result


@app.get("/debug/baselines/{patient_id}")
async def debug_baselines(patient_id: str):
    """
    Debug: compare in-memory signal baseline vs Firebase for a patient.
    Confirms persistence is working after a flush cycle.
    """
    result = {}
    try:
        from signal_integration import patient_signal_store
        mem = patient_signal_store.get(patient_id)
        if mem:
            result["in_memory"] = {
                "session_count": mem.get("session_count"),
                "baseline_established": mem.get("baseline_established"),
                "escalation_level": mem.get("escalation_level"),
                "signal_history_len": len(mem.get("signal_history", [])),
                "check_in_history_len": len(mem.get("check_in_history", [])),
                "last_updated": mem.get("last_updated").isoformat() if hasattr(mem.get("last_updated"), "isoformat") else str(mem.get("last_updated")),
                "hydrated_from_firebase": mem.get("_hydrated_from_firebase", False),
            }
        else:
            result["in_memory"] = None

        fb_data = firebase_db.load_signal_baseline(patient_id)
        if fb_data:
            result["firebase"] = {
                "session_count": fb_data.get("session_count"),
                "baseline_established": fb_data.get("baseline_established"),
                "escalation_level": fb_data.get("escalation_level"),
                "signal_history_len": len(fb_data.get("signal_history", [])),
                "check_in_history_len": len(fb_data.get("check_in_history", [])),
                "last_updated": fb_data.get("last_updated"),
            }
        else:
            result["firebase"] = None

        if result.get("in_memory") and result.get("firebase"):
            result["match"] = (
                result["in_memory"]["session_count"] == result["firebase"]["session_count"]
                and result["in_memory"]["baseline_established"] == result["firebase"]["baseline_established"]
            )
    except Exception as e:
        result["error"] = str(e)
    return result




# ── Phenotype score endpoints ────────────────────────────────────────────────

@app.get("/api/phenotype/all", dependencies=[Depends(verify_clinician_api_key)])
async def get_all_phenotype_scores():
    """
    Return latest phenotype score card for ALL patients, sorted by dropout_risk desc.
    Primary feed for the clinician dashboard patient list.
    """
    try:
        # First try in-memory (fast, most current)
        from signal_integration import patient_signal_store, compute_phenotype_score
        scores = []
        for pid in list(patient_signal_store.keys()):
            try:
                score = compute_phenotype_score(pid)
                # Enrich with patient name from patients_db
                pdata = patients_db.get(pid, {})
                score["patient_name"] = pdata.get("name")
                score["treatment_stage"] = pdata.get("treatment_stage")
                scores.append(score)
            except Exception as e:
                logger.warning(f"compute_phenotype_score error for {pid}: {e}")

        # Also pull from Firebase for any patients not in this instance's memory
        fb_scores = firebase_db.load_all_phenotype_scores()
        in_memory_pids = {s["patient_id"] for s in scores}
        for pid, fb_score in fb_scores.items():
            if pid not in in_memory_pids and fb_score:
                pdata = patients_db.get(pid, {})
                fb_score["patient_name"] = pdata.get("name")
                fb_score["treatment_stage"] = pdata.get("treatment_stage")
                scores.append(fb_score)

        scores.sort(key=lambda s: s.get("dropout_risk", 0.0), reverse=True)
        return {"scores": scores, "total": len(scores)}
    except Exception as e:
        logger.error(f"get_all_phenotype_scores error: {e}")
        return {"scores": [], "total": 0, "error": str(e)}


@app.get("/api/phenotype/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def get_phenotype_score(patient_id: str):
    """
    Return the latest phenotype score card for one patient.
    Tries in-memory first (fresh compute), falls back to Firebase.
    """
    try:
        from signal_integration import patient_signal_store, compute_phenotype_score
        if patient_id in patient_signal_store:
            score = compute_phenotype_score(patient_id)
            score["source"] = "live"
            return score
        # Patient not in this instance — try Firebase
        fb_score = firebase_db.load_phenotype_score(patient_id)
        if fb_score:
            fb_score["source"] = "firebase_cache"
            return fb_score
        return {"error": "No phenotype data for this patient yet", "patient_id": patient_id}
    except Exception as e:
        logger.error(f"get_phenotype_score error for {patient_id}: {e}")
        return {"error": str(e), "patient_id": patient_id}


@app.get("/api/phenotype/{patient_id}/history", dependencies=[Depends(verify_clinician_api_key)])
async def get_phenotype_history(patient_id: str, limit: int = 30):
    """
    Return the last N phenotype score cards for a patient (for trend charts).
    """
    try:
        history = firebase_db.load_phenotype_history(patient_id, limit=limit)
        return {"patient_id": patient_id, "history": history, "count": len(history)}
    except Exception as e:
        logger.error(f"get_phenotype_history error for {patient_id}: {e}")
        return {"patient_id": patient_id, "history": [], "count": 0, "error": str(e)}


# ── Agent Endpoints ───────────────────────────────────────────────────

def _get_all_phenotype_scores_dict() -> dict:
    """Return phenotype scores keyed by patient_id for agent consumption."""
    from signal_integration import compute_phenotype_score, patient_signal_store
    scores = {}
    for pid in list(patient_signal_store.keys()):
        try:
            score = compute_phenotype_score(pid)
            if score:
                scores[pid] = score
        except Exception:
            pass
    # Also load from Firebase
    try:
        fb_scores = firebase_db.load_all_phenotype_scores()
        for pid, sc in fb_scores.items():
            if pid not in scores and sc:
                scores[pid] = sc
    except Exception:
        pass
    return scores

def _get_agents():
    """Create agent instances with current dependencies."""
    from agents import ClinicianAgent, PatientAgent
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    scores_fn = _get_all_phenotype_scores_dict
    checkins_fn = get_recent_checkins
    return ClinicianAgent(fb_ref, scores_fn, checkins_fn), PatientAgent(fb_ref, scores_fn, checkins_fn)


@app.post("/agent/clinician/run", dependencies=[Depends(verify_clinician_api_key)])
async def agent_clinician_run():
    """Trigger clinician agent: generate briefings, assess escalation, build digest."""
    clinician, _ = _get_agents()
    results = clinician.run_all()
    return results

@app.post("/agent/patient/run/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def agent_patient_run(patient_id: str):
    """Trigger patient agent: compute egg state, check reach-out, generate greeting."""
    _, patient = _get_agents()
    results = patient.run(patient_id)
    return results

@app.get("/agent/briefing/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def agent_get_briefing(patient_id: str):
    """Get latest clinician briefing for a patient."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        data = fb_ref.child(f"briefings/{patient_id}/latest").get()
        if data:
            return data
    return {"text": "No briefing available yet.", "generated_at": None}

@app.get("/agent/digest", dependencies=[Depends(verify_clinician_api_key)])
async def agent_get_digest():
    """Get latest daily digest."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        data = fb_ref.child("daily_digest/latest").get()
        if data:
            return data
    return {"text": "No digest available yet.", "generated_at": None}

@app.get("/agent/egg-state/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def agent_get_egg_state(patient_id: str):
    """Get current egg companion state for a patient."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        data = fb_ref.child(f"egg_state/{patient_id}").get()
        if data:
            return data
    return {"mood": "warm", "energy": 0.6, "warmth": 0.7, "proactive": False, "suggested_activity": None, "greeting_tone": "cheerful"}

@app.get("/agent/pending-actions", dependencies=[Depends(verify_clinician_api_key)])
async def agent_get_pending_actions():
    """Get all pending approval actions."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        data = fb_ref.child("pending_actions").get()
        if data and isinstance(data, dict):
            actions = [{"id": k, **v} for k, v in data.items() if isinstance(v, dict) and v.get("status") == "pending_approval"]
            return {"actions": actions, "count": len(actions)}
    return {"actions": [], "count": 0}

@app.post("/agent/approve-action/{action_id}", dependencies=[Depends(verify_clinician_api_key)])
async def agent_approve_action(action_id: str):
    """Approve a pending action."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        ref = fb_ref.child(f"pending_actions/{action_id}")
        action = ref.get()
        if action:
            ref.update({"status": "approved", "approved_at": utc_iso()})
            return {"status": "approved", "action": action}
    return {"error": "Action not found"}

@app.post("/agent/reject-action/{action_id}", dependencies=[Depends(verify_clinician_api_key)])
async def agent_reject_action(action_id: str):
    """Reject a pending action."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        ref = fb_ref.child(f"pending_actions/{action_id}")
        action = ref.get()
        if action:
            ref.update({"status": "rejected", "rejected_at": utc_iso()})
            return {"status": "rejected"}
    return {"error": "Action not found"}


@app.delete("/debug/cleanup-patients/{keep_id}")
async def cleanup_patients(keep_id: str):
    """Temporary — delete all patients except the specified ID."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            return {"error": "no firebase"}
        all_patients = _fb_ref.child("patients").get() or {}
        deleted = []
        for pid in list(all_patients.keys()):
            if pid != keep_id:
                _fb_ref.child("patients").child(pid).delete()
                deleted.append(pid)
        return {"kept": keep_id, "deleted_count": len(deleted), "deleted": deleted}
    except Exception as e:
        return {"error": str(e)}




# ── Lab Parser + Pre-IVF Clearance ───────────────────────────────────

class LabParseRequest(BaseModel):
    raw_text: str

class ClearanceOverrideRequest(BaseModel):
    status: str  # "cleared", "abnormal", "missing"
    value: Optional[str] = None
    notes: Optional[str] = None


def _build_enhanced_lab_prompt():
    """Build the enhanced Sonnet prompt for lab/document parsing."""
    all_items = PRE_IVF_CHECKLIST["mandatory"] + PRE_IVF_CHECKLIST["often_required"]
    item_list = json.dumps([item["id"] + ": " + item["label"] for item in all_items])
    return (
        "You are an IVF clinical document analyzer. Given a document (referral letter, lab report, or patient summary), extract TWO things:\n\n"
        "PART 1 - REFERRAL SUMMARY:\n"
        "Extract patient demographics and referral information:\n"
        "- patient_name (if found)\n- female_age\n- male_age\n- referring_doctor (name and practice if found)\n"
        "- reason_for_referral\n- relevant_history (list of key points)\n- previous_treatments (list if any)\n"
        "- medications (current medications if listed)\n- allergies (if listed)\n\n"
        "PART 2 - LAB RESULTS:\n"
        "Map all test results to these checklist items:\n" + item_list + "\n\n"
        "For each lab found, assess status:\n"
        '- "normal" = within reference range / acceptable for IVF\n'
        '- "abnormal" = outside range OR clinically significant for IVF planning\n'
        '- "borderline" = at edge of range, needs clinical review\n\n'
        "CLINICAL FLAGS — mark as abnormal:\n"
        '- Rubella IgG: "Non-immune" or "Negative" = ABNORMAL (needs vaccination before cycle)\n'
        '- Varicella IgG: "Non-immune" or "Negative" = ABNORMAL\n'
        "- TSH: >4.0 mIU/L = ABNORMAL for IVF (target <2.5)\n"
        "- AMH: <5.4 pmol/L = flag as LOW (consider dosing implications)\n"
        "- FSH: >12 IU/L = flag as ELEVATED\n"
        "- Semen: concentration <15M/mL OR motility <40% OR morphology <4% = ABNORMAL\n"
        "- Any positive infectious serology = ABNORMAL\n\n"
        "Also extract any ADDITIONAL FINDINGS not in the checklist:\n"
        "- Ultrasound findings (cysts, fibroids, polyps, hydrosalpinx)\n"
        "- Any abnormal results not in the standard checklist\n\n"
        "Return ONLY valid JSON, no markdown, no backticks:\n"
        "{\n"
        '  "referral_summary": {\n'
        '    "patient_name": "...", "female_age": null, "male_age": null,\n'
        '    "referring_doctor": "...", "reason_for_referral": "...",\n'
        '    "relevant_history": ["..."], "previous_treatments": ["..."],\n'
        '    "medications": ["..."], "allergies": ["..."]\n'
        "  },\n"
        '  "lab_results": [\n'
        '    {"id": "checklist_item_id", "value": "result value", "unit": "unit", "status": "normal|abnormal|borderline", "clinical_note": "why flagged if abnormal"}\n'
        "  ],\n"
        '  "additional_findings": [\n'
        '    {"finding": "description", "clinical_significance": "why it matters for IVF", "action_required": "what to do"}\n'
        "  ],\n"
        '  "missing_mandatory": ["list of checklist item IDs not found in document"],\n'
        '  "overall_concerns": ["top 3-5 clinical concerns in priority order"]\n'
        "}"
    )


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from AI response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _save_enhanced_parse_results(patient_id: str, parsed: dict, source: str, _fb_ref):
    """Save enhanced parse results (lab results + referral summary + findings) to Firebase."""
    # Save lab results to clearance
    results = parsed.get("lab_results", [])
    for r in results:
        item_id = r.get("id")
        if not item_id:
            continue
        clearance_data = {
            "value": r.get("value", ""),
            "unit": r.get("unit", ""),
            "status": "cleared" if r.get("status") == "normal" else r.get("status", "cleared"),
            "clinical_note": r.get("clinical_note", ""),
            "raw_match": r.get("raw_match", ""),
            "parsed_at": utc_iso(),
            "source": source
        }
        _fb_ref.child("patients").child(patient_id).child("clearance").child(item_id).update(clearance_data)
    # Save referral summary
    ref_summary = parsed.get("referral_summary")
    if ref_summary and any(v for v in ref_summary.values() if v):
        ref_summary["parsed_at"] = utc_iso()
        ref_summary["source"] = source
        _fb_ref.child("patients").child(patient_id).child("referral_summary").update(ref_summary)
    # Save additional findings
    add_findings = parsed.get("additional_findings", [])
    if add_findings:
        _fb_ref.child("patients").child(patient_id).child("additional_findings").set(
            {"items": add_findings, "updated_at": utc_iso(), "source": source}
        )
    # Save overall concerns
    concerns = parsed.get("overall_concerns", [])
    if concerns:
        _fb_ref.child("patients").child(patient_id).child("clinical_concerns").set(
            {"items": concerns, "updated_at": utc_iso(), "source": source}
        )
    return results


@app.post("/clinician/patient/{patient_id}/parse-labs", dependencies=[Depends(verify_clinician_api_key)])
async def parse_lab_results(patient_id: str, req: LabParseRequest):
    """Parse pasted lab report text and map to pre-IVF clearance checklist (enhanced with referral summary)."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        system_prompt = _build_enhanced_lab_prompt()
        sonnet_resp = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": req.raw_text}]
        )
        resp_text = _strip_markdown_fences(sonnet_resp.content[0].text)
        parsed = json.loads(resp_text)
        results = _save_enhanced_parse_results(patient_id, parsed, "lab_paste", _fb_ref)
        return {
            "parsed_count": len(results),
            "lab_results": results,
            "referral_summary": parsed.get("referral_summary"),
            "additional_findings": parsed.get("additional_findings", []),
            "overall_concerns": parsed.get("overall_concerns", []),
            "missing_mandatory": parsed.get("missing_mandatory", []),
            "unmatched": parsed.get("unmatched_items", [])
        }
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse AI response: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Lab parse failed for {patient_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clinician/patient/{patient_id}/parse-document", dependencies=[Depends(verify_clinician_api_key)])
async def parse_document_upload(patient_id: str, file: UploadFile = File(...)):
    """Parse an uploaded PDF document (referral letter, lab report) and extract clearance data + referral summary."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        contents = await file.read()
        text = ""
        try:
            with pdfplumber.open(io.BytesIO(contents)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        except Exception as pdf_err:
            raise HTTPException(status_code=400, detail=f"Could not read PDF: {pdf_err}")
        text = text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Could not extract text from PDF")
        system_prompt = _build_enhanced_lab_prompt()
        sonnet_resp = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": text}]
        )
        resp_text = _strip_markdown_fences(sonnet_resp.content[0].text)
        parsed = json.loads(resp_text)
        results = _save_enhanced_parse_results(patient_id, parsed, "pdf_upload", _fb_ref)
        return {
            "parsed_count": len(results),
            "lab_results": results,
            "referral_summary": parsed.get("referral_summary"),
            "additional_findings": parsed.get("additional_findings", []),
            "overall_concerns": parsed.get("overall_concerns", []),
            "missing_mandatory": parsed.get("missing_mandatory", []),
            "extracted_text_length": len(text)
        }
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse AI response: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Document parse failed for {patient_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clinician/patient/{patient_id}/clearance", dependencies=[Depends(verify_clinician_api_key)])
async def get_clearance_status(patient_id: str):
    """Get full pre-IVF clearance status for a patient."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            return {"readiness": "pending", "mandatory": [], "often_required": [], "missing_count": 0, "abnormal_count": 0}
        clearance_data = _fb_ref.child("patients").child(patient_id).child("clearance").get() or {}
        def build_items(checklist_items):
            items = []
            for item in checklist_items:
                stored = clearance_data.get(item["id"], {})
                status = stored.get("status", "missing") if stored else "missing"
                items.append({
                    "id": item["id"],
                    "label": item["label"],
                    "category": item["category"],
                    "partner": item["partner"],
                    "status": status,
                    "value": stored.get("value", ""),
                    "unit": stored.get("unit", ""),
                    "clinical_note": stored.get("clinical_note", ""),
                    "parsed_at": stored.get("parsed_at", ""),
                    "notes": stored.get("notes", ""),
                    "source": stored.get("source", "")
                })
            return items
        mandatory = build_items(PRE_IVF_CHECKLIST["mandatory"])
        often_required = build_items(PRE_IVF_CHECKLIST["often_required"])
        missing_count = sum(1 for i in mandatory if i["status"] == "missing")
        abnormal_count = sum(1 for i in mandatory if i["status"] == "abnormal")
        if abnormal_count > 0:
            readiness = "blocked"
        elif missing_count > 0:
            readiness = "pending"
        else:
            readiness = "ready"
        # Fetch enhanced data (referral summary, findings, concerns)
        referral_summary = _fb_ref.child("patients").child(patient_id).child("referral_summary").get()
        additional_findings = _fb_ref.child("patients").child(patient_id).child("additional_findings").get()
        clinical_concerns = _fb_ref.child("patients").child(patient_id).child("clinical_concerns").get()
        return {
            "readiness": readiness,
            "mandatory": mandatory,
            "often_required": often_required,
            "missing_count": missing_count,
            "abnormal_count": abnormal_count,
            "total_mandatory": len(mandatory),
            "cleared_count": sum(1 for i in mandatory if i["status"] in ("cleared", "normal")),
            "referral_summary": referral_summary,
            "additional_findings": (additional_findings or {}).get("items", []),
            "clinical_concerns": (clinical_concerns or {}).get("items", [])
        }
    except Exception as e:
        logging.error(f"Clearance status failed for {patient_id}: {e}")
        return {"readiness": "pending", "mandatory": [], "often_required": [], "missing_count": 0, "abnormal_count": 0}


@app.patch("/clinician/patient/{patient_id}/clearance/{item_id}", dependencies=[Depends(verify_clinician_api_key)])
async def update_clearance_item(patient_id: str, item_id: str, req: ClearanceOverrideRequest):
    """Manually override a clearance item status."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        update_data = {"status": req.status, "parsed_at": utc_iso(), "source": "manual_override"}
        if req.value is not None:
            update_data["value"] = req.value
        if req.notes is not None:
            update_data["notes"] = req.notes
        _fb_ref.child("patients").child(patient_id).child("clearance").child(item_id).update(update_data)
        return {"status": "updated", "item_id": item_id, **update_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Outcome Notification Queue ───────────────────────────────────────

@app.post("/clinician/patient/{patient_id}/outcome", dependencies=[Depends(verify_clinician_api_key)])
async def create_outcome(patient_id: str, req: OutcomeRequest):
    """Add an outcome result to the notification queue."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        outcome = {
            "outcome_type": req.outcome_type,
            "outcome_value": req.outcome_value,
            "notes": req.notes or "",
            "created_at": utc_iso(),
            "status": "pending_call",
            "informed_at": None,
            "call_attempts": 0,
            "patient_id": patient_id
        }
        ref = _fb_ref.child("outcomes").child(patient_id).push(outcome)
        outcome["outcome_id"] = ref.key
        return outcome
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clinician/outcomes/pending", dependencies=[Depends(verify_clinician_api_key)])
async def get_pending_outcomes():
    """Get all outcomes pending doctor call, sorted by urgency."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            return {"outcomes": []}
        all_outcomes = _fb_ref.child("outcomes").get() or {}
        pending = []
        now = datetime.now(timezone.utc)
        for pid, outcomes in all_outcomes.items():
            if not isinstance(outcomes, dict):
                continue
            # Fetch patient info once per patient
            patient_data = _fb_ref.child("patients").child(pid).get() or {}
            patient_name = patient_data.get("patient_name") or patient_data.get("name") or pid
            patient_phone = patient_data.get("phone") or ""
            pron = patient_data.get("pronunciation") or {}
            for oid, o in outcomes.items():
                if not isinstance(o, dict):
                    continue
                if o.get("status") not in ("pending_call", "voicemail_left"):
                    continue
                created = o.get("created_at", "")
                age_hours = 0
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_hours = round((now - created_dt).total_seconds() / 3600, 1)
                except Exception:
                    pass
                pending.append({
                    "patient_id": pid,
                    "outcome_id": oid,
                    "patient_name": patient_name,
                    "patient_phone": patient_phone,
                    "pronunciation": pron,
                    "outcome_type": o.get("outcome_type", ""),
                    "outcome_value": o.get("outcome_value", ""),
                    "notes": o.get("notes", ""),
                    "status": o.get("status", ""),
                    "created_at": created,
                    "age_hours": age_hours,
                    "call_attempts": o.get("call_attempts", 0)
                })
        pending.sort(key=lambda x: x.get("created_at", ""))
        return {"outcomes": pending}
    except Exception as e:
        logging.error(f"Error fetching pending outcomes: {e}")
        return {"outcomes": []}


@app.patch("/clinician/patient/{patient_id}/outcome/{outcome_id}", dependencies=[Depends(verify_clinician_api_key)])
async def update_outcome(patient_id: str, outcome_id: str, req: OutcomeUpdateRequest):
    """Update outcome status (mark as informed, voicemail, unreachable)."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        update_data = {"status": req.status, "informed_at": utc_iso()}
        if req.call_notes:
            update_data["call_notes"] = req.call_notes
        # Increment call attempts
        existing = _fb_ref.child("outcomes").child(patient_id).child(outcome_id).get()
        if existing:
            update_data["call_attempts"] = (existing.get("call_attempts", 0) or 0) + 1
        _fb_ref.child("outcomes").child(patient_id).child(outcome_id).update(update_data)
        return {"status": "updated", "outcome_id": outcome_id, **update_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Name Pronunciation Helper ────────────────────────────────────────

@app.post("/clinician/patient/{patient_id}/pronunciation", dependencies=[Depends(verify_clinician_api_key)])
async def generate_pronunciation(patient_id: str):
    """Generate pronunciation guide for a patient's name using Haiku."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        patient_data = _fb_ref.child("patients").child(patient_id).get()
        if not patient_data:
            raise HTTPException(status_code=404, detail="Patient not found")
        display_name = patient_data.get("patient_name") or patient_data.get("name") or patient_id
        haiku_resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": f"You are a pronunciation guide for an Australian English speaker. Given the name below, provide:\n1. Phonetic pronunciation with syllable breaks and stress markers (CAPS for stressed syllable)\n2. A 'sounds like' approximation using common English words\nName: {display_name}\nRespond in this exact format:\nPhonetic: [phonetic]\nSounds like: [approximation]"}]
        )
        resp_text = haiku_resp.content[0].text
        phonetic = ""
        sounds_like = ""
        for line in resp_text.strip().split("\n"):
            if line.lower().startswith("phonetic:"):
                phonetic = line.split(":", 1)[1].strip()
            elif line.lower().startswith("sounds like:"):
                sounds_like = line.split(":", 1)[1].strip()
        pronunciation_data = {
            "phonetic": phonetic,
            "sounds_like": sounds_like,
            "source": "haiku_auto",
            "generated_at": utc_iso()
        }
        _fb_ref.child("patients").child(patient_id).child("pronunciation").update(pronunciation_data)
        return pronunciation_data
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Pronunciation generation failed for {patient_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clinician/patient/{patient_id}/pronunciation", dependencies=[Depends(verify_clinician_api_key)])
async def get_pronunciation(patient_id: str):
    """Get stored pronunciation for a patient."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            raise HTTPException(status_code=503, detail="Firebase not available")
        data = _fb_ref.child("patients").child(patient_id).child("pronunciation").get()
        if not data:
            raise HTTPException(status_code=404, detail="No pronunciation data")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/clinician/patient/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def delete_patient(patient_id: str):
    """Permanently delete ALL patient data from every source."""
    deleted_from = []
    try:
        from firebase_db import _fb_ref
        if _fb_ref:
            paths = ["patients", "conversations", "checkins", "phenotype_history",
                     "alerts", "briefing_cache", "comfort_reports", "community_posts",
                     "screenings", "reflections"]
            for path in paths:
                try:
                    _fb_ref.child(path).child(patient_id).delete()
                    deleted_from.append("db/" + path)
                except Exception:
                    pass
        # Delete from Firebase Auth (kills their login permanently)
        try:
            import firebase_admin.auth as fb_auth
            fb_auth.delete_user(patient_id)
            deleted_from.append("firebase_auth")
        except Exception:
            pass
        # Delete from in-memory storage
        if "patients" in dir() or True:
            try:
                from app import patients as mem_patients
                if patient_id in mem_patients:
                    del mem_patients[patient_id]
                    deleted_from.append("in_memory")
            except Exception:
                pass
        return {"status": "permanently_deleted", "patient_id": patient_id, "deleted_from": deleted_from}
    except Exception as e:
        return {"error": str(e)}


# ── Admin: Clinician Account Management ──────────────────────────────

class CreateClinicianRequest(BaseModel):
    email: str
    password: str
    display_name: str
    role: str  # "doctor", "nurse", "pa"


@app.post("/admin/clinician", dependencies=[Depends(verify_clinician_api_key)])
async def create_clinician_account(req: CreateClinicianRequest, request: Request):
    """Create a new clinician account with Firebase Auth + custom claims."""
    try:
        import firebase_admin.auth as fb_auth
        from firebase_db import _fb_ref
        if req.role not in ("doctor", "nurse", "pa"):
            raise HTTPException(status_code=400, detail="Role must be doctor, nurse, or pa")
        # Create Firebase Auth user
        user = fb_auth.create_user(
            email=req.email,
            password=req.password,
            display_name=req.display_name
        )
        # Set custom claims
        fb_auth.set_custom_user_claims(user.uid, {
            "clinician": True,
            "role": req.role,
            "clinician_role": req.role,
            "clinic": "melod-ai"
        })
        # Save to RTDB
        if _fb_ref:
            _fb_ref.child("clinicians").child(user.uid).update({
                "email": req.email,
                "display_name": req.display_name,
                "role": req.role,
                "created_at": utc_iso(),
                "active": True
            })
        # Audit log
        info = _get_clinician(request)
        asyncio.create_task(_log_audit_safe("create_clinician", info, details={
            "new_uid": user.uid, "email": req.email, "role": req.role
        }))
        return {"uid": user.uid, "email": req.email, "role": req.role, "display_name": req.display_name}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Failed to create clinician: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/create-test-accounts", dependencies=[Depends(verify_clinician_api_key)])
async def create_test_accounts():
    """Create test clinician accounts for each role — idempotent."""
    try:
        import firebase_admin.auth as fb_auth
        from firebase_db import _fb_ref
        test_accounts = [
            {"email": "doctor@melodai.test", "password": "MelodTest2026!", "role": "doctor", "name": "Dr. Test"},
            {"email": "nurse@melodai.test", "password": "MelodTest2026!", "role": "nurse", "name": "Nurse Test"},
            {"email": "pa@melodai.test", "password": "MelodTest2026!", "role": "pa", "name": "PA Test"},
            {"email": "secretary@melodai.test", "password": "MelodTest2026!", "role": "secretary", "name": "Secretary Test"},
        ]
        results = []
        for acct in test_accounts:
            try:
                user = fb_auth.create_user(email=acct["email"], password=acct["password"], display_name=acct["name"])
                fb_auth.set_custom_user_claims(user.uid, {"clinician": True, "role": acct["role"], "clinician_role": acct["role"], "clinic": "melod-ai"})
                if _fb_ref:
                    _fb_ref.child("clinicians").child(user.uid).update({"email": acct["email"], "role": acct["role"], "display_name": acct["name"], "created_at": utc_iso(), "active": True})
                results.append({"email": acct["email"], "status": "created", "uid": user.uid})
            except Exception as e:
                if "ALREADY_EXISTS" in str(e) or "already exists" in str(e).lower():
                    results.append({"email": acct["email"], "status": "already_exists"})
                else:
                    results.append({"email": acct["email"], "status": f"error: {e}"})
        return {"accounts": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Audit Log Viewer ─────────────────────────────────────────────────

@app.get("/clinician/audit-log", dependencies=[Depends(verify_clinician_api_key)])
async def get_audit_log(request: Request, days: int = 7, clinician_uid: str = None, patient_id: str = None):
    """View audit log entries. Doctor-only in practice (enforced by frontend)."""
    try:
        from firebase_db import _fb_ref
        if not _fb_ref:
            return {"entries": []}
        cutoff_epoch = _time.time() - (days * 86400)
        all_entries = _fb_ref.child("audit_log").order_by_child("epoch").start_at(cutoff_epoch).limit_to_last(500).get()
        if not all_entries:
            return {"entries": []}
        entries = []
        for key, val in all_entries.items():
            if not isinstance(val, dict):
                continue
            if clinician_uid and val.get("clinician_uid") != clinician_uid:
                continue
            if patient_id and val.get("patient_id") != patient_id:
                continue
            val["id"] = key
            entries.append(val)
        # Sort by epoch descending
        entries.sort(key=lambda x: x.get("epoch", 0), reverse=True)
        return {"entries": entries[:500]}
    except Exception as e:
        logging.error(f"Audit log read failed: {e}")
        return {"entries": []}


# ── Engagement Score Endpoints ───────────────────────────────────────

def _compute_engagement(patient_id: str) -> dict:
    """Compute 7-day composite engagement score for a patient.

    Components (weighted):
      - check_ins:  30%  (7/week = 100%)
      - messages:   40%  (18/week = 100%, capped)
      - phq4:       15%  (1/week = 100%)
      - active_days: 15%  (5/week = 100%)
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    melb = ZoneInfo("Australia/Melbourne")
    now = datetime.now(melb)
    cutoff = now - timedelta(days=7)
    cutoff_iso = cutoff.isoformat()

    fb_ref = getattr(firebase_db, '_fb_ref', None)
    checkin_count = 0
    message_count = 0
    phq4_count = 0
    active_dates = set()

    if fb_ref:
        # Count check-ins in last 7 days
        try:
            checkins_raw = fb_ref.child("checkins").child(patient_id).get()
            if checkins_raw and isinstance(checkins_raw, dict):
                for _k, v in checkins_raw.items():
                    if isinstance(v, dict):
                        ts = v.get("timestamp") or v.get("date") or ""
                        if ts >= cutoff_iso[:10]:
                            checkin_count += 1
                            active_dates.add(ts[:10])
        except Exception:
            pass

        # Count messages in last 7 days
        try:
            convos_raw = fb_ref.child("conversations").child(patient_id).get()
            if convos_raw and isinstance(convos_raw, dict):
                for _k, v in convos_raw.items():
                    if isinstance(v, dict) and v.get("role") == "user":
                        ts = v.get("timestamp") or ""
                        if ts >= cutoff_iso[:10]:
                            message_count += 1
                            active_dates.add(ts[:10])
        except Exception:
            pass

        # Count PHQ-4 completions in last 7 days
        try:
            phq4_raw = fb_ref.child("patients").child(patient_id).child("phq4_scores").get()
            if phq4_raw and isinstance(phq4_raw, dict):
                for date_key, v in phq4_raw.items():
                    if date_key >= cutoff_iso[:10]:
                        phq4_count += 1
                        active_dates.add(date_key[:10])
        except Exception:
            pass

    active_day_count = len(active_dates)

    # Weighted score (0-100)
    checkin_pct = min(checkin_count / 7.0, 1.0) * 100
    message_pct = min(message_count / 18.0, 1.0) * 100
    phq4_pct = min(phq4_count / 1.0, 1.0) * 100
    active_pct = min(active_day_count / 5.0, 1.0) * 100

    score = round(checkin_pct * 0.30 + message_pct * 0.40 + phq4_pct * 0.15 + active_pct * 0.15)
    score = max(0, min(100, score))

    # Alert status
    alert_status = None
    if score < 15:
        alert_status = "critical"
    elif score < 30:
        alert_status = "warning"

    date_str = now.strftime("%Y-%m-%d")

    return {
        "patient_id": patient_id,
        "current_score": score,
        "components": {
            "check_ins": checkin_count,
            "messages": message_count,
            "phq4_completed": phq4_count,
            "active_days": active_day_count,
        },
        "alert_status": alert_status,
        "date": date_str,
        "timestamp": now.isoformat(),
    }


@app.get("/clinician/engagement/all", dependencies=[Depends(verify_clinician_api_key)])
async def get_all_engagement_scores():
    """Get engagement scores for all patients (batch endpoint for dashboard)."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    patient_ids = []

    if fb_ref:
        try:
            fb_patients = fb_ref.child("patients").get()
            if fb_patients and isinstance(fb_patients, dict):
                patient_ids = list(fb_patients.keys())
        except Exception as e:
            logger.warning(f"Engagement batch: failed to list patients: {e}")

    # Fall back to in-memory
    if not patient_ids:
        patient_ids = list(patients_db.keys())

    results = []
    for pid in patient_ids:
        try:
            results.append(_compute_engagement(pid))
        except Exception as e:
            logger.warning(f"Engagement compute error for {pid}: {e}")

    return {"patients": results}


@app.get("/clinician/engagement/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def get_engagement_score(patient_id: str):
    """Get 7-day composite engagement score for a patient."""
    result = _compute_engagement(patient_id)

    # Store score to Firebase
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        try:
            fb_ref.child("engagement_scores").child(patient_id).child(result["date"]).update({
                "score": result["current_score"],
                "components": result["components"],
                "alert_status": result["alert_status"],
                "timestamp": result["timestamp"],
            })
        except Exception as e:
            logger.warning(f"Engagement score write error for {patient_id}: {e}")

        # Push alert if disengaged
        if result["alert_status"]:
            try:
                fb_ref.child("alerts").push({
                    "patient_id": patient_id,
                    "type": "disengagement",
                    "level": "RED" if result["alert_status"] == "critical" else "AMBER",
                    "message": f"Engagement score {result['current_score']}/100 ({result['alert_status']})",
                    "score": result["current_score"],
                    "alert_status": result["alert_status"],
                    "timestamp": result["timestamp"],
                    "acknowledged": False,
                })
            except Exception as e:
                logger.warning(f"Engagement alert write error for {patient_id}: {e}")

    return result


# ── Therapeutic Alliance Micro-Survey Endpoints ──────────────────────


class AllianceItem(BaseModel):
    q_id: str
    score: int  # 1-5


class AllianceSurveyRequest(BaseModel):
    items: list[AllianceItem]
    cycle_phase: str = "general"


# Standard TAI-SF inspired items for AI companion
ALLIANCE_QUESTIONS = {
    "q1": {"text": "My companion understands what I'm going through", "subscale": "warmth"},
    "q2": {"text": "I feel comfortable sharing my feelings here", "subscale": "warmth"},
    "q3": {"text": "My companion gives me useful suggestions", "subscale": "competence"},
    "q4": {"text": "The support I receive feels relevant to my situation", "subscale": "competence"},
    "q5": {"text": "I feel cared for when I use this app", "subscale": "warmth"},
    "q6": {"text": "I trust the information I receive here", "subscale": "competence"},
}


@app.post("/alliance-survey/{patient_id}")
async def submit_alliance_survey(patient_id: str, req: AllianceSurveyRequest):
    """Submit a therapeutic alliance micro-survey (patient-facing, no auth)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    melb = ZoneInfo("Australia/Melbourne")
    now = datetime.now(melb)
    ts_str = now.strftime("%Y%m%d_%H%M%S")

    # Parse scores by subscale
    warmth_scores = []
    competence_scores = []
    all_scores = []

    for item in req.items:
        score = max(1, min(5, item.score))
        all_scores.append(score)
        q_info = ALLIANCE_QUESTIONS.get(item.q_id)
        if q_info:
            if q_info["subscale"] == "warmth":
                warmth_scores.append(score)
            else:
                competence_scores.append(score)

    warmth_mean = round(sum(warmth_scores) / len(warmth_scores), 2) if warmth_scores else 0
    competence_mean = round(sum(competence_scores) / len(competence_scores), 2) if competence_scores else 0
    overall_mean = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0

    survey_data = {
        "patient_id": patient_id,
        "cycle_phase": req.cycle_phase,
        "items": {item.q_id: item.score for item in req.items},
        "warmth_mean": warmth_mean,
        "competence_mean": competence_mean,
        "overall_mean": overall_mean,
        "timestamp": now.isoformat(),
    }

    # Store to Firebase
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        try:
            fb_ref.child("patients").child(patient_id).child("alliance_surveys").child(ts_str).update(survey_data)
        except Exception as e:
            logger.warning(f"Alliance survey write error for {patient_id}: {e}")

        # Clear pending flag
        try:
            fb_ref.child("patients").child(patient_id).update({"pending_alliance_survey": False})
        except Exception:
            pass

        # Alert if low alliance
        if overall_mean < 2.5:
            try:
                fb_ref.child("alerts").push({
                    "patient_id": patient_id,
                    "type": "low_alliance",
                    "level": "AMBER",
                    "message": f"Low therapeutic alliance: overall={overall_mean}, warmth={warmth_mean}, competence={competence_mean}",
                    "timestamp": now.isoformat(),
                    "acknowledged": False,
                })
            except Exception as e:
                logger.warning(f"Alliance alert write error for {patient_id}: {e}")

    return {"warmth_mean": warmth_mean, "competence_mean": competence_mean, "overall_mean": overall_mean}


@app.get("/alliance-survey/{patient_id}/pending")
async def check_pending_alliance(patient_id: str):
    """Check if a patient has a pending alliance survey."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    if fb_ref:
        try:
            val = fb_ref.child("patients").child(patient_id).child("pending_alliance_survey").get()
            if val:
                cycle_phase = None
                if isinstance(val, dict):
                    cycle_phase = val.get("cycle_phase")
                return {"pending": True, "cycle_phase": cycle_phase}
        except Exception:
            pass
    return {"pending": False, "cycle_phase": None}


@app.get("/clinician/alliance/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def get_alliance_history(patient_id: str):
    """Get all alliance surveys and trend for a patient (clinician-facing)."""
    fb_ref = getattr(firebase_db, '_fb_ref', None)
    surveys = []

    if fb_ref:
        try:
            raw = fb_ref.child("patients").child(patient_id).child("alliance_surveys").get()
            if raw and isinstance(raw, dict):
                for ts_key, sdata in sorted(raw.items()):
                    if isinstance(sdata, dict):
                        surveys.append(sdata)
        except Exception as e:
            logger.warning(f"Alliance history read error for {patient_id}: {e}")

    # Compute trend from last 2 surveys
    trend = "stable"
    current_warmth = None
    current_competence = None

    if len(surveys) >= 1:
        latest = surveys[-1]
        current_warmth = latest.get("warmth_mean")
        current_competence = latest.get("competence_mean")

    if len(surveys) >= 2:
        prev_overall = surveys[-2].get("overall_mean", 0)
        curr_overall = surveys[-1].get("overall_mean", 0)
        diff = curr_overall - prev_overall
        if diff > 0.3:
            trend = "improving"
        elif diff < -0.3:
            trend = "declining"

    return {
        "surveys": surveys,
        "trend": trend,
        "current_warmth": current_warmth,
        "current_competence": current_competence,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
# deploy trigger 1774411583


# --- PHQ-4 Screening Endpoints ---

class PHQ4Request(BaseModel):
    q1: int
    q2: int
    q3: int
    q4: int
    triggered_by: str = "daily"

@app.post("/phq4/{patient_id}")
async def submit_phq4(patient_id: str, req: PHQ4Request):
    from zoneinfo import ZoneInfo
    import firebase_admin.db as fb_db_mod
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    date_str = now.strftime("%Y-%m-%d")
    anxiety_sub = req.q1 + req.q2
    depression_sub = req.q3 + req.q4
    total = anxiety_sub + depression_sub
    if total <= 2:
        severity = "normal"
    elif total <= 5:
        severity = "mild"
    elif total <= 8:
        severity = "moderate"
    else:
        severity = "severe"
    phq4_data = {"date": date_str, "anxiety_sub": anxiety_sub, "depression_sub": depression_sub, "total": total, "severity": severity, "triggered_by": req.triggered_by, "timestamp": now.isoformat()}
    fb_db_mod.reference(f"melod_ai/patients/{patient_id}/phq4_scores/{date_str}").update(phq4_data)
    if total >= 6 or anxiety_sub >= 4 or depression_sub >= 4:
        fb_db_mod.reference("melod_ai").child("alerts").push({"type": "phq4_elevated", "patient_id": patient_id, "total": total, "anxiety_sub": anxiety_sub, "depression_sub": depression_sub, "severity": severity, "timestamp": now.isoformat()})
    return {"total": total, "anxiety_sub": anxiety_sub, "depression_sub": depression_sub, "severity": severity}

@app.get("/phq4/{patient_id}/pending")
async def check_pending_phq4(patient_id: str):
    import firebase_admin.db as fb_db_mod
    ref = fb_db_mod.reference(f"melod_ai/patients/{patient_id}/pending_phq4")
    val = ref.get()
    if val:
        return {"pending": True, "cycle_phase": val.get("cycle_phase") if isinstance(val, dict) else None}
    return {"pending": False, "cycle_phase": None}


# --- Engagement Composite Score Endpoints ---

def _compute_engagement(patient_id):
    import firebase_admin.db as fb_db_mod
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    week_ago = now - timedelta(days=7)
    week_ago_str = week_ago.strftime("%Y-%m-%d")
    checkins = 0
    messages = 0
    phq4_count = 0
    active_dates = set()
    try:
        ci = fb_db_mod.reference(f"melod_ai/checkins/{patient_id}").get() or {}
        for k, v in ci.items():
            d = v.get("date", k) if isinstance(v, dict) else k
            if d >= week_ago_str:
                checkins += 1
                active_dates.add(d)
    except Exception:
        pass
    try:
        convs = fb_db_mod.reference(f"melod_ai/conversations/{patient_id}").get() or {}
        for k, v in convs.items():
            if isinstance(v, dict):
                d = v.get("date", "")
                if d >= week_ago_str and v.get("role") == "user":
                    messages += 1
                    active_dates.add(d)
    except Exception:
        pass
    try:
        phq = fb_db_mod.reference(f"melod_ai/patients/{patient_id}/phq4_scores").get() or {}
        for k, v in phq.items():
            if k >= week_ago_str:
                phq4_count += 1
                active_dates.add(k)
    except Exception:
        pass
    active_days = len(active_dates)
    ci_pct = min(checkins / 7.0, 1.0) * 100
    msg_pct = min(messages / 18.0, 1.0) * 100
    phq_pct = min(phq4_count / 1.0, 1.0) * 100
    day_pct = min(active_days / 5.0, 1.0) * 100
    score = int(ci_pct * 0.30 + msg_pct * 0.40 + phq_pct * 0.15 + day_pct * 0.15)
    alert_status = None
    if score < 15:
        alert_status = "critical"
    elif score < 30:
        alert_status = "warning"
    return {"current_score": score, "components": {"check_ins": checkins, "messages": messages, "phq4": phq4_count, "active_days": active_days}, "alert_status": alert_status}


@app.get("/clinician/engagement/all", dependencies=[Depends(verify_clinician_api_key)])
async def get_all_engagement():
    import firebase_admin.db as fb_db_mod
    patients = fb_db_mod.reference("melod_ai/patients").get() or {}
    results = []
    for pid in patients.keys():
        eng = _compute_engagement(pid)
        eng["patient_id"] = pid
        results.append(eng)
    return {"patients": results}


@app.get("/clinician/engagement/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def get_patient_engagement(patient_id: str):
    import firebase_admin.db as fb_db_mod
    from zoneinfo import ZoneInfo
    eng = _compute_engagement(patient_id)
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    date_str = now.strftime("%Y-%m-%d")
    fb_db_mod.reference(f"melod_ai/engagement_scores/{patient_id}/{date_str}").update({"score": eng["current_score"], "components": eng["components"], "timestamp": now.isoformat()})
    if eng["alert_status"]:
        fb_db_mod.reference("melod_ai").child("alerts").push({"type": "disengagement", "patient_id": patient_id, "score": eng["current_score"], "alert_status": eng["alert_status"], "timestamp": now.isoformat()})
    return eng


# --- Proactive Escalation Nudge Endpoints ---

def _evaluate_escalation(patient_id):
    import firebase_admin.db as fb_db_mod
    triggers = []
    # 1. Check latest PHQ-4
    try:
        phq_scores = fb_db_mod.reference(f"melod_ai/patients/{patient_id}/phq4_scores").get() or {}
        if phq_scores:
            sorted_dates = sorted(phq_scores.keys(), reverse=True)
            latest = phq_scores[sorted_dates[0]]
            if isinstance(latest, dict):
                total = latest.get("total", 0)
                dep_sub = latest.get("depression_sub", 0)
                if total >= 9:
                    triggers.append({"type": "phq4_severe", "severity": "strong_recommendation", "detail": f"PHQ-4 total={total}"})
                if dep_sub >= 5 and len(sorted_dates) >= 3:
                    scores_3 = [phq_scores[d].get("depression_sub", 0) for d in sorted_dates[:3] if isinstance(phq_scores[d], dict)]
                    if len(scores_3) == 3 and scores_3[0] > scores_3[1] > scores_3[2]:
                        triggers.append({"type": "depression_trending", "severity": "gentle_nudge", "detail": f"Depression trending up: {scores_3[::-1]}"})
    except Exception:
        pass
    # 2. Check engagement
    try:
        eng = _compute_engagement(patient_id)
        if eng["current_score"] < 15:
            triggers.append({"type": "severe_disengagement", "severity": "clinician_alert_only", "detail": f"Engagement score={eng['current_score']}"})
    except Exception:
        pass
    # 3. Check alliance
    try:
        surveys = fb_db_mod.reference(f"melod_ai/patients/{patient_id}/alliance_surveys").get() or {}
        if surveys:
            sorted_keys = sorted(surveys.keys(), reverse=True)
            latest_survey = surveys[sorted_keys[0]]
            if isinstance(latest_survey, dict) and latest_survey.get("overall_mean", 5) < 2.0:
                triggers.append({"type": "low_alliance", "severity": "gentle_nudge", "detail": f"Alliance overall={latest_survey.get('overall_mean')}"})
    except Exception:
        pass
    # 4. Check phenotype
    try:
        pheno = fb_db_mod.reference(f"melod_ai/phenotype_scores/{patient_id}/latest").get() or {}
        if isinstance(pheno, dict):
            for construct, score in pheno.items():
                if isinstance(score, (int, float)) and score > 0.8:
                    triggers.append({"type": "phenotype_elevated", "severity": "strong_recommendation", "detail": f"{construct}={score}"})
                    break
    except Exception:
        pass
    if not triggers:
        return {"needs_escalation": False, "trigger_type": None, "severity": None, "message": None}
    worst = sorted(triggers, key=lambda t: {"strong_recommendation": 0, "gentle_nudge": 1, "clinician_alert_only": 2}.get(t["severity"], 3))[0]
    messages = {
        "gentle_nudge": "It sounds like things have been really tough lately. Your care team is here for you \u2014 would you like me to let them know you\u2019d appreciate a check-in?",
        "strong_recommendation": "I want to make sure you\u2019re getting the best support possible. I\u2019m going to let your nurse know to reach out \u2014 they care about how you\u2019re doing.",
        "clinician_alert_only": None
    }
    return {"needs_escalation": True, "trigger_type": worst["type"], "severity": worst["severity"], "message": messages.get(worst["severity"]), "detail": worst["detail"], "all_triggers": triggers}


@app.get("/escalation/{patient_id}/check")
async def check_escalation(patient_id: str):
    return _evaluate_escalation(patient_id)


@app.post("/escalation/{patient_id}/respond")
async def respond_to_escalation(patient_id: str, response: dict):
    import firebase_admin.db as fb_db_mod
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    ts = now.strftime("%Y%m%d_%H%M%S")
    patient_response = response.get("response", "unknown")
    trigger_type = response.get("trigger_type", "unknown")
    severity = response.get("severity", "unknown")
    record = {"patient_id": patient_id, "response": patient_response, "trigger_type": trigger_type, "severity": severity, "timestamp": now.isoformat(), "resolved": False}
    fb_db_mod.reference(f"melod_ai/patients/{patient_id}/escalation_responses/{ts}").update(record)
    if patient_response == "yes_please" or severity == "strong_recommendation":
        fb_db_mod.reference("melod_ai").child("alerts").push({"type": "patient_requested_contact" if patient_response == "yes_please" else "escalation_auto", "patient_id": patient_id, "trigger_type": trigger_type, "severity": severity, "patient_response": patient_response, "timestamp": now.isoformat()})
    followups = {"yes_please": "Thank you. Your care team will reach out to you soon.", "not_now": "No worries at all. I\u2019m here whenever you need me.", "im_okay": "Glad to hear it. Remember, your care team is always just a message away."}
    return {"acknowledged": True, "message": followups.get(patient_response, "Thank you for letting me know.")}


@app.get("/clinician/escalations/{patient_id}", dependencies=[Depends(verify_clinician_api_key)])
async def get_patient_escalations(patient_id: str):
    import firebase_admin.db as fb_db_mod
    responses = fb_db_mod.reference(f"melod_ai/patients/{patient_id}/escalation_responses").get() or {}
    entries = [{"id": k, **v} for k, v in responses.items() if isinstance(v, dict)]
    entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"escalations": entries}


@app.post("/clinician/escalations/{patient_id}/resolve", dependencies=[Depends(verify_clinician_api_key)])
async def resolve_escalation(patient_id: str, body: dict):
    import firebase_admin.db as fb_db_mod
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    esc_id = body.get("escalation_id", "")
    action = body.get("action", "dismissed")
    note = body.get("note", "")
    if esc_id:
        fb_db_mod.reference(f"melod_ai/patients/{patient_id}/escalation_responses/{esc_id}").update({"resolved": True, "resolved_action": action, "resolved_note": note, "resolved_at": now.isoformat()})
    return {"resolved": True}


@app.get("/anti-dependency/{patient_id}/check")
async def check_anti_dependency(patient_id: str):
    import firebase_admin.db as fb_db_mod
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    try:
        msgs = fb_db_mod.reference(f"melod_ai/patients/{patient_id}/anti_dependency_msgs").get() or {}
        if msgs:
            last_date = max(msgs.keys())
            days_since = (now.date() - datetime.strptime(last_date, "%Y-%m-%d").date()).days
            if days_since < 14:
                return {"show_message": False}
    except Exception:
        pass
    try:
        eng = _compute_engagement(patient_id)
        if eng["current_score"] > 80:
            date_str = now.strftime("%Y-%m-%d")
            fb_db_mod.reference(f"melod_ai/patients/{patient_id}/anti_dependency_msgs/{date_str}").update({"shown": True, "timestamp": now.isoformat()})
            return {"show_message": True, "message": "Remember, your care team is always available for anything you need. I\u2019m here to support you between appointments."}
    except Exception:
        pass
    return {"show_message": False}
