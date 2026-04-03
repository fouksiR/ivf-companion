"""
Dual Agent Layer — Clinician Agent + Patient Agent

Clinician Agent: generates briefings, assesses escalation, creates daily digest
Patient Agent: computes egg state, determines proactive reach-out, personalizes greetings

Both agents consume phenotype scores from signal_integration.py and use Claude API.
"""

import os
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import anthropic

logger = logging.getLogger("agents")

# ── Models ────────────────────────────────────────────────────────────
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-20250514"

client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var

# ── System Prompts ────────────────────────────────────────────────────

BRIEFING_SYSTEM = """You are a clinical briefing assistant for IVF/fertility clinicians.
Generate a concise 3-5 sentence pre-consultation briefing based on the patient's
phenotype data and check-in history. Be specific about scores and trends.
Use clinical but warm language. Focus on actionable insights.
Do NOT diagnose or prescribe — summarize signals for the clinician's judgment."""

DIGEST_SYSTEM = """You are a clinical digest assistant summarizing an IVF clinic's patient cohort.
Generate a structured morning briefing for the care team. Include:
1. Top patients by risk (one-liner each)
2. New escalations since yesterday
3. Cohort engagement trends
4. Flags needing attention
Be concise and actionable. Use clinical language."""

REACH_OUT_SYSTEM = """You are the voice of an empathetic egg companion (Melod·AI) for IVF patients.
Draft a brief, warm check-in message. Be genuine, not clinical.
Reference the patient's situation without being specific about scores.
Use a conversational tone with occasional emoji. Keep it under 2 sentences.
Never mention data, algorithms, or monitoring."""

GREETING_SYSTEM = """You are a warm egg companion greeting an IVF patient.
Generate a single personalized greeting based on the context provided.
Be brief (one sentence), warm, and encouraging. Match the requested tone.
Never be clinical or mention monitoring/data."""


# ── Helpers ───────────────────────────────────────────────────────────

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _aest_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=10)))

def _time_of_day() -> str:
    h = _aest_now().hour
    if h < 12: return "morning"
    if h < 17: return "afternoon"
    return "evening"

def _call_claude(model: str, system: str, user_msg: str, max_tokens: int = 300) -> str:
    """Call Claude API with error handling."""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return ""

def _fb_write(path: str, data, fb_ref):
    """Write to Firebase with error handling."""
    try:
        fb_ref.child(path).set(data)
    except Exception as e:
        logger.warning(f"Firebase write failed at {path}: {e}")

def _fb_read(path: str, fb_ref):
    """Read from Firebase with error handling."""
    try:
        return fb_ref.child(path).get()
    except Exception as e:
        logger.warning(f"Firebase read failed at {path}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# CLINICIAN AGENT
# ══════════════════════════════════════════════════════════════════════

class ClinicianAgent:
    def __init__(self, fb_ref, phenotype_scores_fn, get_checkins_fn):
        """
        fb_ref: firebase_db._fb_ref (melod_ai/ root)
        phenotype_scores_fn: callable that returns {pid: score_card, ...}
        get_checkins_fn: callable(pid, n) that returns recent check-ins
        """
        self.fb = fb_ref
        self.get_scores = phenotype_scores_fn
        self.get_checkins = get_checkins_fn

    def generate_briefing(self, patient_id: str) -> str:
        """Generate pre-consultation briefing for one patient."""
        scores = self.get_scores()
        card = scores.get(patient_id)
        if not card:
            return "No phenotype data available for this patient."

        checkins = self.get_checkins(patient_id, 5)
        checkin_summary = ""
        if checkins:
            moods = [c.get("mood", 5) for c in checkins]
            checkin_summary = f"Recent mood scores: {moods}. "

        constructs = card.get("constructs", {})
        engagement = card.get("engagement", {})
        deltas = card.get("deltas", {})
        flags = card.get("flags", [])

        prompt = f"""Patient phenotype data:
- Overall risk: {card.get('overall_risk', 'GREEN')}
- Dropout risk: {card.get('dropout_risk', 0):.0%}
- Constructs: {', '.join(f'{k}: {v:.2f}' for k, v in constructs.items() if v > 0.1)}
- Engagement: {engagement.get('trend', 'stable')}, {engagement.get('sessions_last_7d', 0)} sessions/week
- Flags: {'; '.join(flags) if flags else 'None'}
- Biggest change: {deltas.get('biggest_mover', 'none')} ({deltas.get('biggest_mover_delta', 0):+.2f})
- {checkin_summary}

Generate a 3-5 sentence pre-consultation briefing."""

        briefing = _call_claude(HAIKU, BRIEFING_SYSTEM, prompt)
        if not briefing:
            briefing = f"Risk level: {card.get('overall_risk', 'GREEN')}. Dropout risk: {card.get('dropout_risk', 0):.0%}."

        data = {
            "text": briefing,
            "generated_at": _utc_iso(),
            "risk": card.get("overall_risk", "GREEN"),
            "dropout_risk": card.get("dropout_risk", 0),
        }
        if self.fb:
            _fb_write(f"briefings/{patient_id}/latest", data, self.fb)

        return briefing

    def assess_escalation(self, patient_id: str) -> Dict:
        """Assess escalation level and create actions if needed."""
        scores = self.get_scores()
        card = scores.get(patient_id)
        if not card:
            return {"level": "LOW", "action": None}

        dropout_risk = card.get("dropout_risk", 0)
        constructs = card.get("constructs", {})
        flags = card.get("flags", [])

        if dropout_risk > 0.7:
            # HIGH — draft urgent outreach, needs clinician approval
            reason = f"Dropout risk {dropout_risk:.0%}. " + ("; ".join(flags[:3]) if flags else "Multiple elevated constructs.")
            draft = _call_claude(HAIKU, REACH_OUT_SYSTEM,
                f"Patient has high dropout risk ({dropout_risk:.0%}). Key concerns: {reason}. Draft a caring check-in message.")

            action_id = str(uuid.uuid4())[:8]
            action = {
                "patient_id": patient_id,
                "action_type": "urgent_outreach",
                "draft_message": draft or "We've been thinking of you. How are you doing?",
                "status": "pending_approval",
                "created_at": _utc_iso(),
                "escalation_reason": reason,
                "dropout_risk": dropout_risk,
            }
            if self.fb:
                _fb_write(f"pending_actions/{action_id}", action, self.fb)
            return {"level": "HIGH", "action": action, "action_id": action_id}

        elif dropout_risk > 0.4:
            # MEDIUM — auto-generate check-in suggestion
            suggestion = _call_claude(HAIKU, REACH_OUT_SYSTEM,
                f"Patient has moderate dropout risk ({dropout_risk:.0%}). Suggest a gentle check-in.")
            if self.fb:
                _fb_write(f"suggested_actions/{patient_id}", {
                    "suggestion": suggestion or "Consider a gentle check-in.",
                    "created_at": _utc_iso(),
                    "dropout_risk": dropout_risk,
                }, self.fb)
            return {"level": "MEDIUM", "action": "suggested_checkin"}

        return {"level": "LOW", "action": None}

    def generate_daily_digest(self) -> str:
        """Generate morning digest summarizing all patients."""
        scores = self.get_scores()
        if not scores:
            return "No patient data available for digest."

        # Build summary for Sonnet
        patient_summaries = []
        for pid, card in sorted(scores.items(), key=lambda x: x.get("dropout_risk", 0) if isinstance(x, dict) else 0, reverse=True):
            if not isinstance(card, dict):
                continue
            name = _fb_read(f"patients/{pid}/name", self.fb) or pid[:8]
            risk = card.get("overall_risk", "GREEN")
            dr = card.get("dropout_risk", 0)
            trend = (card.get("engagement") or {}).get("trend", "stable")
            flags = card.get("flags", [])
            patient_summaries.append(
                f"- {name}: {risk} risk, dropout {dr:.0%}, engagement {trend}" +
                (f", flags: {'; '.join(flags[:2])}" if flags else "")
            )

        prompt = f"""Patient cohort ({len(patient_summaries)} patients):
{chr(10).join(patient_summaries[:20])}

Generate a structured morning digest for the care team."""

        digest = _call_claude(SONNET, DIGEST_SYSTEM, prompt, max_tokens=500)
        if not digest:
            digest = f"Cohort: {len(scores)} patients. Check dashboard for details."

        data = {
            "text": digest,
            "generated_at": _utc_iso(),
            "patient_count": len(scores),
            "red_count": sum(1 for c in scores.values() if isinstance(c, dict) and c.get("overall_risk") == "RED"),
            "amber_count": sum(1 for c in scores.values() if isinstance(c, dict) and c.get("overall_risk") == "AMBER"),
        }
        if self.fb:
            _fb_write("daily_digest/latest", data, self.fb)
            date_key = _aest_now().strftime("%Y-%m-%d")
            _fb_write(f"daily_digest/history/{date_key}", data, self.fb)

        return digest

    def run_all(self) -> Dict:
        """Orchestrate all clinician agent tasks."""
        start = datetime.now(timezone.utc)
        results = {"briefings": 0, "escalations": {}, "errors": []}

        scores = self.get_scores()
        for pid in scores:
            try:
                self.generate_briefing(pid)
                results["briefings"] += 1
                esc = self.assess_escalation(pid)
                results["escalations"][pid] = esc["level"]
            except Exception as e:
                results["errors"].append(f"{pid}: {e}")
                logger.error(f"Clinician agent error for {pid}: {e}")

        try:
            self.generate_daily_digest()
        except Exception as e:
            results["errors"].append(f"digest: {e}")

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        results["elapsed_seconds"] = round(elapsed, 1)

        if self.fb:
            _fb_write(f"agent_logs/{_utc_iso().replace(':', '-')}", {
                "agent": "clinician", "results": results
            }, self.fb)

        return results


# ══════════════════════════════════════════════════════════════════════
# PATIENT AGENT
# ══════════════════════════════════════════════════════════════════════

class PatientAgent:
    def __init__(self, fb_ref, phenotype_scores_fn, get_checkins_fn):
        self.fb = fb_ref
        self.get_scores = phenotype_scores_fn
        self.get_checkins = get_checkins_fn

    def compute_egg_state(self, patient_id: str) -> Dict:
        """Compute egg companion state from phenotype data."""
        scores = self.get_scores()
        card = scores.get(patient_id)

        # Default state
        state = {
            "mood": "warm",
            "energy": 0.6,
            "warmth": 0.7,
            "proactive": False,
            "suggested_activity": None,
            "greeting_tone": "cheerful",
            "updated_at": _utc_iso(),
        }

        if not card:
            if self.fb:
                _fb_write(f"egg_state/{patient_id}", state, self.fb)
            return state

        constructs = card.get("constructs", {})
        engagement = card.get("engagement", {})
        anxiety = constructs.get("anxiety_escalation", 0)
        hopelessness = constructs.get("hopelessness", 0)
        social = constructs.get("social_withdrawal", 0)
        trend = engagement.get("trend", "stable")

        # Determine mood and suggestions
        if anxiety > 0.6:
            state["mood"] = "concerned"
            state["warmth"] = 0.85
            state["energy"] = 0.4
            state["suggested_activity"] = "breathing"
            state["greeting_tone"] = "calm"
        elif hopelessness > 0.5:
            state["mood"] = "gentle"
            state["warmth"] = 0.9
            state["energy"] = 0.3
            state["suggested_activity"] = "chat"
            state["greeting_tone"] = "empathetic"
        elif trend == "declining" or social > 0.6:
            state["mood"] = "warm"
            state["warmth"] = 0.8
            state["proactive"] = True
            state["suggested_activity"] = "journaling"
            state["greeting_tone"] = "encouraging"
        elif all(v < 0.2 for v in constructs.values()) and engagement.get("sessions_last_7d", 0) >= 4:
            state["mood"] = "celebratory"
            state["energy"] = 0.9
            state["warmth"] = 0.8
            state["greeting_tone"] = "cheerful"

        if self.fb:
            _fb_write(f"egg_state/{patient_id}", state, self.fb)

        return state

    def should_reach_out(self, patient_id: str) -> Dict:
        """Determine if egg should proactively message the patient."""
        scores = self.get_scores()
        card = scores.get(patient_id)

        result = {"should_reach_out": False, "reason": None, "message": None, "approval_required": False}

        if not card:
            return result

        constructs = card.get("constructs", {})
        engagement = card.get("engagement", {})
        anxiety = constructs.get("anxiety_escalation", 0)
        hopelessness = constructs.get("hopelessness", 0)
        sessions = engagement.get("sessions_last_7d", 0)
        trend = engagement.get("trend", "stable")

        # Check engagement drop
        if sessions == 0 or (trend == "declining" and sessions <= 1):
            result["should_reach_out"] = True
            result["reason"] = "engagement_drop"
            result["approval_required"] = False

        # Check anxiety/hopelessness threshold
        if anxiety > 0.7 or hopelessness > 0.6:
            result["should_reach_out"] = True
            result["reason"] = "clinical_concern"
            result["approval_required"] = True  # Needs clinician approval

        if not result["should_reach_out"]:
            return result

        # Draft message
        egg_state = self.compute_egg_state(patient_id)
        tone = egg_state.get("greeting_tone", "warm")

        prompt = f"Tone: {tone}. Reason: {result['reason']}. "
        if result["reason"] == "engagement_drop":
            prompt += "Patient hasn't engaged recently. Draft a gentle 'thinking of you' message."
        else:
            prompt += "Patient may be struggling. Draft an empathetic check-in without being clinical."

        message = _call_claude(HAIKU, REACH_OUT_SYSTEM, prompt, max_tokens=100)
        result["message"] = message or "Just thinking of you. How are you going? 💛"

        # Store action
        action_id = str(uuid.uuid4())[:8]
        status = "pending_approval" if result["approval_required"] else "auto_approved"
        action_path = "pending_actions" if result["approval_required"] else "auto_actions"

        if self.fb:
            _fb_write(f"{action_path}/{action_id}", {
                "patient_id": patient_id,
                "action_type": "egg_reach_out",
                "message": result["message"],
                "reason": result["reason"],
                "status": status,
                "created_at": _utc_iso(),
            }, self.fb)

        result["action_id"] = action_id
        result["status"] = status
        return result

    def personalize_greeting(self, patient_id: str) -> str:
        """Generate personalized greeting for the egg companion."""
        egg_state = _fb_read(f"egg_state/{patient_id}", self.fb) if self.fb else None
        if not egg_state:
            egg_state = self.compute_egg_state(patient_id)

        tone = egg_state.get("greeting_tone", "cheerful")
        tod = _time_of_day()

        # Get patient name
        name = None
        if self.fb:
            name = _fb_read(f"patients/{patient_id}/name", self.fb)
        name = name or "there"

        prompt = f"Tone: {tone}. Time of day: {tod}. Patient name: {name}. Generate one greeting sentence."

        greeting = _call_claude(HAIKU, GREETING_SYSTEM, prompt, max_tokens=60)
        if not greeting:
            greetings = {
                "morning": f"Good morning, {name}! Hope you slept well 🌅",
                "afternoon": f"Hey {name}, hope your day is going okay 💛",
                "evening": f"Hi {name}, winding down for the evening? 🌙",
            }
            greeting = greetings.get(tod, f"Hey {name} 💛")

        if self.fb:
            _fb_write(f"egg_state/{patient_id}/greeting", greeting, self.fb)

        return greeting

    def run(self, patient_id: str) -> Dict:
        """Orchestrate all patient agent tasks."""
        start = datetime.now(timezone.utc)
        results = {"patient_id": patient_id, "errors": []}

        try:
            results["egg_state"] = self.compute_egg_state(patient_id)
        except Exception as e:
            results["errors"].append(f"egg_state: {e}")

        try:
            results["reach_out"] = self.should_reach_out(patient_id)
        except Exception as e:
            results["errors"].append(f"reach_out: {e}")

        try:
            results["greeting"] = self.personalize_greeting(patient_id)
        except Exception as e:
            results["errors"].append(f"greeting: {e}")

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        results["elapsed_seconds"] = round(elapsed, 1)

        if self.fb:
            _fb_write(f"agent_logs/{_utc_iso().replace(':', '-')}", {
                "agent": "patient", "patient_id": patient_id, "results": {
                    "egg_mood": results.get("egg_state", {}).get("mood"),
                    "reach_out": results.get("reach_out", {}).get("should_reach_out"),
                    "elapsed": results["elapsed_seconds"],
                }
            }, self.fb)

        return results
