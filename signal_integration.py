"""
signal_integration.py — Melod-AI Signal Integration Layer
Drop-in module that:
  1. Receives passive phenotyping data from the frontend
  2. Runs signal_analysis.py detectors
  3. Stores signal state per-patient (in-memory for now)
  4. Feeds signal summary into the chat system prompt
  5. Exposes /alerts endpoint for clinician dashboard
  6. Handles human-escalation requests from patient app

INTEGRATION: Add these lines to app.py:
  
  from signal_integration import (
      signal_router, 
      get_signal_context_for_patient,
      patient_signal_store
  )
  app.include_router(signal_router)
  
  Then in your /chat endpoint, before calling Claude, add:
  
  signal_ctx = get_signal_context_for_patient(patient_id)
  # Append to system prompt:
  system_prompt += signal_ctx
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
import json
import logging
import time

logger = logging.getLogger("melod-ai.signals")

signal_router = APIRouter(tags=["signals"])

# ── Firebase baseline helpers (lazy import to avoid circular deps) ────────────

def _firebase_db():
    """Lazy-import firebase_db to avoid circular import at module load time."""
    try:
        import firebase_db as _fb
        return _fb.db
    except Exception:
        return None


def _load_baseline_from_firebase(pid: str):
    """
    Lazy-load a patient's signal baseline from Firebase into the in-memory store.
    Called once per patient per Cloud Run instance lifetime (cold-start recovery).
    Silently no-ops if Firebase is unavailable.
    """
    db = _firebase_db()
    if not db:
        return
    try:
        data = db.load_signal_baseline(pid)
        if not data:
            return
        # Hydrate the in-memory store from Firebase
        patient_signal_store[pid] = {
            "signal_history": data.get("signal_history", []),
            "check_in_history": data.get("check_in_history", []),
            "session_count": data.get("session_count", 0),
            "baseline_established": data.get("baseline_established", False),
            "escalation_level": data.get("escalation_level", "GREEN"),
            "human_escalation_requested": data.get("human_escalation_requested", False),
            "human_escalation_at": data.get("human_escalation_at"),
            "current_assessment": data.get("current_assessment"),
            "last_passive_data": {},
            "last_updated": datetime.utcnow(),
            "_hydrated_from_firebase": True,  # Debug marker
        }
        logger.info(f"[baselines] Hydrated {pid} from Firebase — sessions={data.get('session_count',0)}, baseline_established={data.get('baseline_established',False)}")
    except Exception as e:
        logger.warning(f"[baselines] load_baseline_from_firebase error for {pid}: {e}")


def _save_baseline_to_firebase(pid: str):
    """
    Persist current in-memory baseline for a patient to Firebase.
    Fire-and-forget — failure is logged but does not raise.
    """
    db = _firebase_db()
    if not db:
        return
    store = patient_signal_store.get(pid)
    if not store:
        return
    try:
        db.save_signal_baseline(pid, store)
    except Exception as e:
        logger.warning(f"[baselines] save_baseline_to_firebase error for {pid}: {e}")


def _save_phenotype_score_to_firebase(pid: str, score: Dict):
    """
    Persist a phenotype score card to Firebase.
    Writes to both /latest (update) and /history/{ts} (set).
    Fire-and-forget.
    """
    db = _firebase_db()
    if not db:
        return
    try:
        db.save_phenotype_score(pid, score)
    except Exception as e:
        logger.warning(f"[phenotype] save_phenotype_score error for {pid}: {e}")


# ── In-memory stores (replace with PostgreSQL later) ────────────────────────

patient_signal_store: Dict[str, Dict] = {}
# Structure per patient_id:
# {
#   "last_passive_data": {...},          # Raw passive data from last flush
#   "signal_history": [...],             # Rolling window of signal snapshots
#   "current_assessment": {...},         # Latest signal_analysis output
#   "escalation_level": "GREEN",         # GREEN / AMBER / RED
#   "human_escalation_requested": False, # Patient pressed "talk to human"
#   "human_escalation_at": None,         # Timestamp
#   "check_in_history": [...],           # Last N check-in submissions
#   "baseline_established": False,       # 7+ sessions?
#   "session_count": 0,
#   "last_updated": datetime
# }

alert_queue: List[Dict] = []  # Clinician-facing alerts (most recent first)


# ── Pydantic models ────────────────────────────────────────────────────────

class PassiveSignalPayload(BaseModel):
    patient_id: str
    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    typing: Optional[Dict] = None        # inter_key_intervals, deletion_ratio, composition_time_ms
    touch: Optional[Dict] = None         # velocity, pressure, tap_intervals, long_presses
    scroll: Optional[Dict] = None        # velocity_peaks, direction_changes, idle_gaps_ms
    motion: Optional[Dict] = None        # accelerometer variance
    content: Optional[Dict] = None       # word_count, negative_words, uncertainty_words
    engagement: Optional[Dict] = None    # check_in_time_ms, sliders_adjusted, screens_visited
    navigation: Optional[Dict] = None    # entropy, screen_dwell_times
    circadian: Optional[Dict] = None     # hour, is_late_night
    battery: Optional[Dict] = None       # level, charging
    network: Optional[Dict] = None       # type, downlink

class CheckInPayload(BaseModel):
    patient_id: str
    mood: int           # 1-10
    anxiety: int        # 1-10
    loneliness: int     # 1-10
    uncertainty: int    # 1-10
    hope: int           # 1-10
    cycle: Optional[int] = 1
    stage: Optional[str] = None
    timestamp: Optional[str] = None

class HumanEscalationRequest(BaseModel):
    patient_id: str
    patient_name: Optional[str] = None
    reason: Optional[str] = None
    current_scores: Optional[Dict] = None
    urgency: Optional[str] = "high"  # high / critical


# ── Signal Analysis (inline, mirrors signal_analysis.py constructs) ─────────

CONSTRUCT_THRESHOLDS = {
    "psychomotor_retardation": {
        "typing_iki_z": 1.5,        # Slowed typing (high inter-key interval)
        "touch_velocity_z": -1.5,   # Slow touch movements
        "scroll_idle_z": 1.5,       # Long idle gaps
    },
    "psychomotor_agitation": {
        "typing_deletion_z": 1.5,   # Excessive deletion/correction
        "touch_pressure_z": 1.5,    # Hard tapping
        "scroll_direction_z": 1.5,  # Erratic scrolling
    },
    "sleep_disturbance": {
        "late_night_sessions": 2,   # Count of sessions between 00:00-05:00
    },
    "social_withdrawal": {
        "session_gap_days": 3,      # Days since last session
        "engagement_decline_z": -1.5,
    },
    "rumination": {
        "typing_composition_z": 1.5,   # Very long message composition
        "content_repetition_z": 1.5,   # Repeated themes
    },
    "anxiety_escalation": {
        "check_in_anxiety_trend": 2,   # Rising anxiety over 3+ check-ins
        "content_uncertainty_z": 1.5,
    },
    "hopelessness": {
        "check_in_hope_trend": -2,     # Falling hope over 3+ check-ins
        "content_negative_z": 1.5,
    },
}

def analyze_passive_signals(patient_id: str, passive_data: Dict, store: Dict) -> Dict:
    """
    Analyze passive signals against personal baseline.
    Returns assessment dict with construct scores and escalation level.
    """
    assessment = {
        "timestamp": datetime.utcnow().isoformat(),
        "constructs": {},
        "escalation_level": "GREEN",
        "flags": [],
        "summary": "",
    }
    
    session_count = store.get("session_count", 0)
    history = store.get("signal_history", [])
    
    # ── Compute baselines from history ──
    if session_count >= 7 and len(history) >= 5:
        store["baseline_established"] = True
        baseline = _compute_baseline(history)
    else:
        # Not enough data for personal baseline — use population norms.
        # Fix D: log a warning so we can detect missing baselines from Cloud
        # Run logs. Baselines should accumulate as session_count grows; if a
        # patient with many check-ins keeps logging this, their passive
        # signal history isn't persisting across Cloud Run restarts.
        baseline = _population_baseline()
        assessment["flags"].append("baseline_building")
        logger.warning(
            f"[phenotype] No personal baseline for {patient_id} — using population norms "
            f"(session_count={session_count}, history_len={len(history)})"
        )
    
    # ── Run each construct detector ──
    active_constructs = []
    
    # Psychomotor retardation
    if passive_data.get("typing"):
        iki = passive_data["typing"].get("mean_iki_ms", 0)
        if baseline.get("typing_iki_mean"):
            z = (iki - baseline["typing_iki_mean"]) / max(baseline.get("typing_iki_std", 50), 1)
            if z > CONSTRUCT_THRESHOLDS["psychomotor_retardation"]["typing_iki_z"]:
                active_constructs.append(("psychomotor_retardation", z, "slow_typing"))
                assessment["constructs"]["psychomotor_retardation"] = {
                    "active": True, "z_score": round(z, 2), "signal": "typing_slowed"
                }
    
    # Psychomotor agitation 
    if passive_data.get("typing"):
        del_ratio = passive_data["typing"].get("deletion_ratio", 0)
        if baseline.get("deletion_ratio_mean"):
            z = (del_ratio - baseline["deletion_ratio_mean"]) / max(baseline.get("deletion_ratio_std", 0.1), 0.01)
            if z > CONSTRUCT_THRESHOLDS["psychomotor_agitation"]["typing_deletion_z"]:
                active_constructs.append(("psychomotor_agitation", z, "excessive_deletion"))
                assessment["constructs"]["psychomotor_agitation"] = {
                    "active": True, "z_score": round(z, 2), "signal": "excessive_correction"
                }
    
    # Sleep disturbance
    if passive_data.get("circadian"):
        hour = passive_data["circadian"].get("hour", 12)
        if 0 <= hour <= 5:
            late_count = sum(1 for h in history[-7:] if h.get("circadian", {}).get("hour", 12) in range(0, 6))
            if late_count >= CONSTRUCT_THRESHOLDS["sleep_disturbance"]["late_night_sessions"]:
                active_constructs.append(("sleep_disturbance", late_count, "late_night"))
                assessment["constructs"]["sleep_disturbance"] = {
                    "active": True, "count_7d": late_count, "signal": "repeated_late_night_use"
                }
    
    # Anxiety escalation (from check-in trend)
    check_ins = store.get("check_in_history", [])
    if len(check_ins) >= 3:
        recent_anxiety = [c["anxiety"] for c in check_ins[-3:]]
        trend = recent_anxiety[-1] - recent_anxiety[0]
        if trend >= CONSTRUCT_THRESHOLDS["anxiety_escalation"]["check_in_anxiety_trend"]:
            active_constructs.append(("anxiety_escalation", trend, "rising_anxiety"))
            assessment["constructs"]["anxiety_escalation"] = {
                "active": True, "trend": trend, "signal": "anxiety_rising_over_3_checkins"
            }
    
    # Hopelessness (from check-in trend)
    if len(check_ins) >= 3:
        recent_hope = [c["hope"] for c in check_ins[-3:]]
        trend = recent_hope[-1] - recent_hope[0]
        if trend <= CONSTRUCT_THRESHOLDS["hopelessness"]["check_in_hope_trend"]:
            active_constructs.append(("hopelessness", abs(trend), "falling_hope"))
            assessment["constructs"]["hopelessness"] = {
                "active": True, "trend": trend, "signal": "hope_declining_over_3_checkins"
            }
    
    # Content analysis
    if passive_data.get("content"):
        neg_ratio = passive_data["content"].get("negative_word_ratio", 0)
        unc_ratio = passive_data["content"].get("uncertainty_word_ratio", 0)
        if neg_ratio > 0.15:
            active_constructs.append(("hopelessness", neg_ratio * 10, "negative_language"))
        if unc_ratio > 0.12:
            active_constructs.append(("anxiety_escalation", unc_ratio * 10, "uncertain_language"))
    
    # ── Determine escalation level ──
    n_active = len(set(c[0] for c in active_constructs))
    max_z = max((c[1] for c in active_constructs), default=0)
    
    if n_active >= 3 or max_z >= 2.5:
        assessment["escalation_level"] = "RED"
        assessment["flags"].append("multi_construct_alert")
    elif n_active >= 2 or max_z >= 2.0:
        assessment["escalation_level"] = "AMBER"
        assessment["flags"].append("elevated_concern")
    else:
        assessment["escalation_level"] = "GREEN"
    
    # Also check latest check-in absolute values
    if check_ins:
        latest = check_ins[-1]
        if latest.get("mood", 5) <= 2 and latest.get("hope", 5) <= 2:
            assessment["escalation_level"] = "RED"
            assessment["flags"].append("acute_low_mood_hope")
        elif latest.get("anxiety", 5) >= 9:
            if assessment["escalation_level"] != "RED":
                assessment["escalation_level"] = "AMBER"
            assessment["flags"].append("high_anxiety")
    
    # ── Build summary ──
    if assessment["escalation_level"] == "RED":
        constructs_str = ", ".join(set(c[0] for c in active_constructs))
        assessment["summary"] = f"ELEVATED CONCERN: Active constructs: {constructs_str}. Consider offering human support."
    elif assessment["escalation_level"] == "AMBER":
        assessment["summary"] = f"Moderate signals detected in {n_active} construct(s). Monitor closely."
    else:
        assessment["summary"] = "Within normal range." if store.get("baseline_established") else "Building personal baseline."
    
    # ── Community behavior analysis ──
    community_signals = passive_data.get("community_activity", {})
    if community_signals:
        community_flags = analyze_community_behavior(community_signals, store.get("signal_history", []))
        for cf in community_flags:
            assessment["flags"].append(cf["flag"])
            assessment["constructs"][cf["flag"]] = {
                "active": True,
                "severity": cf["severity"],
                "signal": cf["evidence"],
                "recommendation": cf.get("recommendation", "")
            }

    return assessment


def analyze_community_behavior(community_signals: dict, weekly_behavior: list = None) -> list:
    """Analyze community engagement patterns for phenotyping.

    Detects: SEEKING_CONNECTION, LATE_NIGHT_COMMUNITY, ANTICIPATORY_BROWSING
    """
    flags = []

    if not community_signals:
        return flags

    # SEEKING_CONNECTION: Extended community browsing + multiple reactions
    time_on_circle = community_signals.get('time_on_circle_tab_ms', 0)
    reactions_given = community_signals.get('reactions_given', 0)

    if time_on_circle > 120000 and reactions_given >= 3:  # >2 min + 3+ reactions
        flags.append({
            "flag": "SEEKING_CONNECTION",
            "severity": "low",
            "evidence": f"Extended community browsing ({round(time_on_circle/1000)}s) + {reactions_given} reactions",
            "recommendation": "Patient may benefit from peer support group referral"
        })
    elif time_on_circle > 180000:  # >3 min even without reactions
        flags.append({
            "flag": "SEEKING_CONNECTION",
            "severity": "low",
            "evidence": f"Extended community browsing ({round(time_on_circle/1000)}s)",
            "recommendation": "Patient spending significant time in community — may be seeking connection"
        })

    # LATE_NIGHT_COMMUNITY: Posting after 11pm
    post_hours = community_signals.get('post_hours', [])
    late_posts = [h for h in post_hours if h >= 23 or h <= 4]
    if len(late_posts) >= 2:
        flags.append({
            "flag": "LATE_NIGHT_COMMUNITY",
            "severity": "moderate",
            "evidence": f"{len(late_posts)} community posts between 11pm-4am this week",
            "recommendation": "Possible insomnia + isolation. Consider sleep support."
        })

    # ANTICIPATORY_BROWSING: Reading stages they haven't reached yet
    stage_filters_used = community_signals.get('stage_filters_used', [])
    current_stage = community_signals.get('current_stage', '')

    if stage_filters_used:
        other_stages = [s for s in stage_filters_used if s != current_stage]
        if len(other_stages) >= 3:
            flags.append({
                "flag": "ANTICIPATORY_BROWSING",
                "severity": "low",
                "evidence": f"Browsing {', '.join(other_stages)} while in {current_stage}",
                "recommendation": "May be anxious about upcoming stages"
            })

    return flags


def _compute_baseline(history: List[Dict]) -> Dict:
    """Compute personal baseline from signal history."""
    baseline = {}
    ikis = [h.get("typing", {}).get("mean_iki_ms", 0) for h in history if h.get("typing")]
    if ikis:
        baseline["typing_iki_mean"] = sum(ikis) / len(ikis)
        baseline["typing_iki_std"] = (sum((x - baseline["typing_iki_mean"])**2 for x in ikis) / len(ikis)) ** 0.5
    
    del_ratios = [h.get("typing", {}).get("deletion_ratio", 0) for h in history if h.get("typing")]
    if del_ratios:
        baseline["deletion_ratio_mean"] = sum(del_ratios) / len(del_ratios)
        baseline["deletion_ratio_std"] = (sum((x - baseline["deletion_ratio_mean"])**2 for x in del_ratios) / len(del_ratios)) ** 0.5
    
    return baseline


def _population_baseline() -> Dict:
    """Population-level baseline norms (used before personal baseline is established)."""
    return {
        "typing_iki_mean": 180,     # ms between keystrokes
        "typing_iki_std": 60,
        "deletion_ratio_mean": 0.12,
        "deletion_ratio_std": 0.08,
        "touch_velocity_mean": 400,
        "touch_velocity_std": 150,
    }


# ── Phenotype scoring layer ─────────────────────────────────────────────────

# Dropout risk weights — starting hypothesis, easy to tune.
# Must sum to 1.0.
DROPOUT_WEIGHTS = {
    "social_withdrawal":       0.25,
    "hopelessness":            0.20,
    "engagement_decline":      0.20,
    "anxiety_escalation":      0.15,
    "psychomotor_retardation": 0.10,
    "psychomotor_agitation":   0.05,
    "sleep_disturbance":       0.03,
    "rumination":              0.02,
}

# Severity → 0-1 mapping for constructs detected via level string
_LEVEL_TO_SCORE = {"GREEN": 0.1, "low": 0.2, "moderate": 0.5, "AMBER": 0.5, "high": 0.8, "RED": 0.9}


def _z_to_score(z: float, cap: float = 4.0) -> float:
    """Map a positive z-score to 0.0-1.0 (clamped)."""
    return min(max(z / cap, 0.0), 1.0)


def compute_phenotype_score(patient_id: str) -> Dict:
    """
    Synthesize all 7 construct detectors + engagement metrics into a single
    structured score card per patient.

    Returns the score card dict and also persists it to Firebase
    at melod_ai/phenotype_scores/{patient_id}/latest and /history/{ts}.
    """
    store = patient_signal_store.get(patient_id)
    now_iso = datetime.utcnow().isoformat()

    # ── Empty-store guard ────────────────────────────────────────────────────
    if not store:
        score = {
            "patient_id": patient_id,
            "computed_at": now_iso,
            "overall_risk": "GREEN",
            "dropout_risk": 0.0,
            "constructs": {k: 0.0 for k in DROPOUT_WEIGHTS if k != "engagement_decline"},
            "engagement": {
                "trend": "stable",
                "sessions_last_7d": 0,
                "avg_session_depth": 0.0,
                "circadian_regularity": 0.0,
            },
            "deltas": {"dropout_risk_change": 0.0, "biggest_mover": None, "biggest_mover_delta": 0.0},
            "flags": ["no_data"],
        }
        return score

    assessment = store.get("current_assessment") or {}
    history = store.get("signal_history", [])
    check_ins = store.get("check_in_history", [])
    session_count = store.get("session_count", 0)

    # ── 1. Per-construct scores (0.0-1.0) ────────────────────────────────────
    constructs_raw = assessment.get("constructs", {})
    c_scores: Dict[str, float] = {}

    def _score_construct(name: str) -> float:
        entry = constructs_raw.get(name)
        if not entry or not entry.get("active"):
            return 0.0
        # z-score path
        z = entry.get("z_score")
        if z is not None:
            return _z_to_score(float(z))
        # trend magnitude path (anxiety/hope)
        t = entry.get("trend")
        if t is not None:
            return _z_to_score(abs(float(t)), cap=10.0)
        # late_night count path
        cnt = entry.get("count_7d")
        if cnt is not None:
            return min(cnt / 5.0, 1.0)
        # severity string path (community flags)
        sev = entry.get("severity", "")
        return _LEVEL_TO_SCORE.get(sev, 0.5)

    for name in ["psychomotor_retardation", "psychomotor_agitation", "sleep_disturbance",
                 "social_withdrawal", "rumination", "anxiety_escalation", "hopelessness"]:
        c_scores[name] = _score_construct(name)

    # ── 2. Engagement metrics ────────────────────────────────────────────────
    now = datetime.utcnow()

    # sessions_last_7d: count signal_history entries with circadian data in last 7 days
    recent_sessions = [
        h for h in history
        if h.get("circadian") and
        abs((now - datetime.fromisoformat(h["circadian"].get("timestamp", now_iso))).days) <= 7
    ] if history else []
    sessions_last_7d = len(recent_sessions)

    # sessions_prev_7d: same for days 8-14
    prev_sessions = [
        h for h in history
        if h.get("circadian") and
        7 < abs((now - datetime.fromisoformat(h["circadian"].get("timestamp", now_iso))).days) <= 14
    ] if history else []
    sessions_prev_7d = len(prev_sessions)

    # Fallback: approximate from session_count when timestamp data is thin
    if sessions_last_7d == 0 and session_count > 0:
        sessions_last_7d = min(session_count, 7)

    # Engagement trend
    if sessions_prev_7d > 0:
        delta_pct = (sessions_last_7d - sessions_prev_7d) / sessions_prev_7d
        eng_trend = "rising" if delta_pct > 0.20 else ("declining" if delta_pct < -0.20 else "stable")
        eng_decline_score = max(-delta_pct, 0.0)  # 0 if rising/stable, >0 if declining
    else:
        eng_trend = "stable"
        eng_decline_score = 0.0

    # avg_session_depth: proxy — ratio of sessions with typing data to total
    sessions_with_typing = sum(1 for h in history if h.get("typing")) if history else 0
    avg_session_depth = sessions_with_typing / max(len(history), 1)

    # circadian_regularity: std-dev of session hours (lower std = more regular)
    session_hours = [h["circadian"]["hour"] for h in history if h.get("circadian", {}).get("hour") is not None]
    if len(session_hours) >= 3:
        mean_h = sum(session_hours) / len(session_hours)
        std_h = (sum((x - mean_h) ** 2 for x in session_hours) / len(session_hours)) ** 0.5
        circ_regularity = max(0.0, 1.0 - std_h / 12.0)  # 12h std = 0.0 regularity
    else:
        circ_regularity = 0.5  # unknown → neutral

    engagement = {
        "trend": eng_trend,
        "sessions_last_7d": sessions_last_7d,
        "avg_session_depth": round(avg_session_depth, 2),
        "circadian_regularity": round(circ_regularity, 2),
    }

    # ── 3. Composite dropout risk ────────────────────────────────────────────
    weighted_sum = 0.0
    for construct, weight in DROPOUT_WEIGHTS.items():
        if construct == "engagement_decline":
            weighted_sum += weight * min(eng_decline_score, 1.0)
        else:
            weighted_sum += weight * c_scores.get(construct, 0.0)
    dropout_risk = round(min(weighted_sum, 1.0), 3)

    # overall_risk mirrors existing escalation or is derived from dropout_risk
    existing_level = store.get("escalation_level", "GREEN")
    if existing_level == "RED" or dropout_risk >= 0.7:
        overall_risk = "RED"
    elif existing_level == "AMBER" or dropout_risk >= 0.35:
        overall_risk = "AMBER"
    else:
        overall_risk = "GREEN"

    # ── 4. Deltas vs previous score card ────────────────────────────────────
    prev_score = store.get("_last_phenotype_score")
    if prev_score:
        prev_dropout = prev_score.get("dropout_risk", 0.0)
        prev_constructs = prev_score.get("constructs", {})
        dropout_risk_change = round(dropout_risk - prev_dropout, 3)
        # biggest mover
        moves = {
            name: abs(c_scores.get(name, 0.0) - prev_constructs.get(name, 0.0))
            for name in c_scores
        }
        biggest_mover = max(moves, key=moves.get) if moves else None
        biggest_mover_delta = round(moves.get(biggest_mover, 0.0), 3) if biggest_mover else 0.0
    else:
        dropout_risk_change = 0.0
        biggest_mover = None
        biggest_mover_delta = 0.0

    deltas = {
        "dropout_risk_change": dropout_risk_change,
        "biggest_mover": biggest_mover,
        "biggest_mover_delta": biggest_mover_delta,
    }

    # ── 5. Clinical flags (max 5, prioritized) ───────────────────────────────
    flags = []

    # Engagement silence
    last_ci = check_ins[-1] if check_ins else None
    if last_ci:
        try:
            days_since = (now - datetime.fromisoformat(last_ci.get("submitted_at", now_iso))).days
            if days_since >= 3:
                flags.append(f"No check-in for {days_since}d (was active)")
        except Exception:
            pass

    if eng_trend == "declining":
        flags.append(f"Engagement declining — {sessions_last_7d} sessions this week vs {sessions_prev_7d} last week")

    # Construct threshold crossings
    construct_flag_order = [
        ("hopelessness", 0.6, "Hopelessness signal elevated"),
        ("anxiety_escalation", 0.6, "Anxiety escalation detected"),
        ("social_withdrawal", 0.6, "Social withdrawal pattern"),
        ("psychomotor_retardation", 0.5, "Typing speed slowed significantly"),
        ("psychomotor_agitation", 0.5, "Increased correction/deletion behaviour"),
        ("sleep_disturbance", 0.5, "Repeated late-night sessions"),
        ("rumination", 0.5, "Extended message composition pattern"),
    ]
    for name, threshold, msg in construct_flag_order:
        if c_scores.get(name, 0.0) >= threshold:
            entry = constructs_raw.get(name, {})
            detail = entry.get("signal", "")
            flags.append(f"{msg}" + (f" ({detail})" if detail else ""))

    # Absolute check-in floor
    if last_ci:
        if last_ci.get("mood", 5) <= 2 and last_ci.get("hope", 5) <= 2:
            flags.insert(0, "Acute low mood+hope on last check-in — review urgently")
        elif last_ci.get("anxiety", 5) >= 9:
            flags.insert(0, "Extreme anxiety on last check-in")

    flags = flags[:5]  # Cap

    # ── 6. Assemble score card ───────────────────────────────────────────────
    score = {
        "patient_id": patient_id,
        "computed_at": now_iso,
        "overall_risk": overall_risk,
        "dropout_risk": dropout_risk,
        "constructs": {k: round(v, 3) for k, v in c_scores.items()},
        "engagement": engagement,
        "deltas": deltas,
        "flags": flags,
    }

    # Cache on store for next delta computation
    store["_last_phenotype_score"] = score

    # Persist to Firebase (fire-and-forget)
    _save_phenotype_score_to_firebase(patient_id, score)

    # ── PHQ-4 ad-hoc trigger ────────────────────────────────────────────────
    # If any construct score crosses 0.5 (≈2 SD from baseline), flag for PHQ-4
    any_elevated = any(v >= 0.5 for v in c_scores.values())
    if any_elevated and store.get("baseline_established"):
        try:
            db = _firebase_db()
            if db and db._fb_ref:
                db._fb_ref.child("patients").child(patient_id).update({"pending_phq4": True})
                logger.info(f"[phenotype] PHQ-4 ad-hoc trigger set for {patient_id} — elevated constructs detected")
        except Exception as e:
            logger.warning(f"[phenotype] pending_phq4 flag write error for {patient_id}: {e}")

    return score


# ── Chat context injection ──────────────────────────────────────────────────

def get_signal_context_for_patient(patient_id: str) -> str:
    """
    Returns a string to append to the system prompt before calling Claude.
    This is how passive assessment FACTORS INTO the chat response.
    """
    store = patient_signal_store.get(patient_id)
    if not store:
        return ""
    
    assessment = store.get("current_assessment")
    if not assessment:
        return ""
    
    check_ins = store.get("check_in_history", [])
    latest_checkin = check_ins[-1] if check_ins else None
    
    parts = ["\n\n--- PASSIVE SIGNAL ASSESSMENT (do NOT share raw scores with patient) ---"]
    parts.append(f"Escalation level: {assessment.get('escalation_level', 'GREEN')}")
    parts.append(f"Summary: {assessment.get('summary', 'No data')}")
    
    if assessment.get("constructs"):
        active = [k for k, v in assessment["constructs"].items() if v.get("active")]
        if active:
            parts.append(f"Active clinical constructs: {', '.join(active)}")
    
    if latest_checkin:
        parts.append(f"Latest check-in: mood={latest_checkin.get('mood')}, anxiety={latest_checkin.get('anxiety')}, "
                     f"hope={latest_checkin.get('hope')}, loneliness={latest_checkin.get('loneliness')}")
    
    if assessment.get("escalation_level") == "RED":
        parts.append("INSTRUCTION: The patient may be in distress. Be warm, validating, and gently "
                     "check in on their wellbeing. If they express hopelessness or thoughts of self-harm, "
                     "guide them toward professional support. Do NOT be alarmist.")
    elif assessment.get("escalation_level") == "AMBER":
        parts.append("INSTRUCTION: Some signals suggest the patient may be struggling more than usual. "
                     "Be attentive and empathetic. Gently explore how they are feeling.")
    
    parts.append("--- END SIGNAL ASSESSMENT ---\n")
    return "\n".join(parts)


# ── API Endpoints ───────────────────────────────────────────────────────────

@signal_router.post("/signals")
async def receive_passive_signals(payload: PassiveSignalPayload):
    """Receive passive phenotyping data from frontend (60s flush cycle)."""
    t0 = time.time()
    pid = payload.patient_id
    
    # Initialize store if new patient on this instance (cold start or new patient)
    if pid not in patient_signal_store:
        # Try to restore from Firebase first (cold-start recovery)
        _load_baseline_from_firebase(pid)

    # If still not in store after Firebase attempt, create fresh
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
            "last_updated": datetime.utcnow(),
        }

    store = patient_signal_store[pid]
    passive_dict = payload.model_dump(exclude={"patient_id", "session_id", "timestamp"}, exclude_none=True)

    # Store raw data
    store["last_passive_data"] = passive_dict
    store["signal_history"].append(passive_dict)
    store["signal_history"] = store["signal_history"][-50:]  # Keep last 50
    store["session_count"] += 1
    store["last_updated"] = datetime.utcnow()

    # Run analysis
    assessment = analyze_passive_signals(pid, passive_dict, store)
    store["current_assessment"] = assessment
    store["escalation_level"] = assessment["escalation_level"]

    # Persist baseline + compute phenotype score on every flush cycle
    _save_baseline_to_firebase(pid)
    try:
        compute_phenotype_score(pid)
    except Exception as _e:
        logger.warning(f"[phenotype] compute error for {pid}: {_e}")

    # If RED, push to alert queue
    if assessment["escalation_level"] == "RED":
        alert_queue.insert(0, {
            "type": "signal_alert",
            "patient_id": pid,
            "level": "RED",
            "summary": assessment["summary"],
            "constructs": list(assessment.get("constructs", {}).keys()),
            "timestamp": datetime.utcnow().isoformat(),
            "acknowledged": False,
        })
        alert_queue[:] = alert_queue[:100]  # Cap at 100 alerts
    
    latency = round((time.time() - t0) * 1000)
    return {
        "status": "ok",
        "escalation_level": assessment["escalation_level"],
        "latency_ms": latency,
    }


# NOTE: The @signal_router.post("/checkin") handler used to live here.
# It was shadowing @app.post("/checkin") in app.py (daily_checkin) because
# app.include_router(signal_router) is registered before daily_checkin, and
# FastAPI matches routes in registration order. Every POST /checkin was
# silently handled by this stub (no Firebase checkin persistence, no AI
# response, no PHQ-9/GAD-7 triggers, no clinical triggers, no low_mood
# alert), which caused the March–April regression in dashboard phenotype
# scores and clinician alerts. The three useful side effects
# (_load_baseline_from_firebase on cold-start, _save_baseline_to_firebase
# after recompute, and the RED alert_queue push) are now ported into
# daily_checkin in app.py. Do not re-add a /checkin route here.


@signal_router.post("/escalate/human")
async def request_human_escalation(payload: HumanEscalationRequest):
    """Patient has pressed 'Talk to someone' — flag immediately for clinician."""
    pid = payload.patient_id
    
    if pid in patient_signal_store:
        patient_signal_store[pid]["human_escalation_requested"] = True
        patient_signal_store[pid]["human_escalation_at"] = datetime.utcnow().isoformat()
    
    alert_queue.insert(0, {
        "type": "human_escalation",
        "patient_id": pid,
        "patient_name": payload.patient_name,
        "reason": payload.reason,
        "urgency": payload.urgency,
        "current_scores": payload.current_scores,
        "timestamp": datetime.utcnow().isoformat(),
        "acknowledged": False,
    })
    
    logger.warning(f"HUMAN ESCALATION REQUESTED by patient {pid}: {payload.reason}")
    
    return {
        "status": "ok",
        "message": "Your request has been flagged. A member of the care team will reach out to you.",
    }


# ── Clinician-facing endpoints ──────────────────────────────────────────────

@signal_router.get("/clinician/patients")
async def get_all_patients():
    """Clinician dashboard: get overview of all patients."""
    # Import patients_db to look up patient names
    from app import patients_db

    patients = []
    for pid, store in patient_signal_store.items():
        assessment = store.get("current_assessment", {})
        check_ins = store.get("check_in_history", [])
        latest_checkin = check_ins[-1] if check_ins else None

        # Resolve patient name from patients_db (set during onboarding)
        patient_record = patients_db.get(pid, {})
        patient_name = patient_record.get("name") or None

        # Normalise last_updated to an ISO string (always use datetime.now
        # for consistent local-time comparison with the frontend)
        last_upd = store.get("last_updated")
        if isinstance(last_upd, datetime):
            last_updated_iso = last_upd.isoformat()
        elif isinstance(last_upd, str) and last_upd:
            last_updated_iso = last_upd
        else:
            last_updated_iso = datetime.now().isoformat()

        patients.append({
            "patient_id": pid,
            "patient_name": patient_name,
            "escalation_level": store.get("escalation_level", "GREEN"),
            "human_escalation_requested": store.get("human_escalation_requested", False),
            "human_escalation_at": store.get("human_escalation_at"),
            "active_constructs": list(assessment.get("constructs", {}).keys()) if assessment else [],
            "summary": assessment.get("summary", "No data") if assessment else "No data",
            "latest_checkin": latest_checkin,
            "session_count": store.get("session_count", 0),
            "baseline_established": store.get("baseline_established", False),
            "last_updated": last_updated_iso,
            "treatment_stage": patient_record.get("treatment_stage", "unknown"),
            "cycle_number": patient_record.get("cycle_number", 1),
            "communication_style": store.get("communication_style"),
        })
    
    # Sort: human escalation first, then RED, then AMBER, then GREEN
    level_order = {"RED": 0, "AMBER": 1, "GREEN": 2}
    patients.sort(key=lambda p: (
        0 if p["human_escalation_requested"] else 1,
        level_order.get(p["escalation_level"], 3),
    ))
    
    return {"patients": patients, "total": len(patients)}


@signal_router.get("/clinician/alerts")
async def get_alerts(limit: int = 20, unacknowledged_only: bool = False):
    """Clinician dashboard: get alert feed."""
    alerts = alert_queue[:limit]
    if unacknowledged_only:
        alerts = [a for a in alerts if not a.get("acknowledged")][:limit]
    return {"alerts": alerts, "total_unacknowledged": sum(1 for a in alert_queue if not a.get("acknowledged"))}


@signal_router.post("/clinician/alerts/{index}/acknowledge")
async def acknowledge_alert(index: int):
    """Clinician acknowledges an alert."""
    if 0 <= index < len(alert_queue):
        alert_queue[index]["acknowledged"] = True
        return {"status": "ok"}
    return JSONResponse(status_code=404, content={"error": "Alert not found"})


@signal_router.get("/clinician/patient/{patient_id}")
async def get_patient_detail(patient_id: str):
    """Clinician dashboard: get detailed view of one patient."""
    store = patient_signal_store.get(patient_id)
    if not store:
        return JSONResponse(status_code=404, content={"error": "Patient not found"})
    
    return {
        "patient_id": patient_id,
        "current_assessment": store.get("current_assessment"),
        "escalation_level": store.get("escalation_level"),
        "human_escalation_requested": store.get("human_escalation_requested"),
        "human_escalation_at": store.get("human_escalation_at"),
        "check_in_history": store.get("check_in_history", [])[-10:],
        "signal_history_count": len(store.get("signal_history", [])),
        "session_count": store.get("session_count"),
        "baseline_established": store.get("baseline_established"),
    }
