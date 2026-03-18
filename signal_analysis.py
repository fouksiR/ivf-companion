"""
════════════════════════════════════════════════════════════════════
MELOD-AI — PASSIVE SIGNAL ANALYSIS ENGINE
════════════════════════════════════════════════════════════════════

Processes digital phenotyping signals from the PassiveCollector frontend.
Detects behavioural patterns that map to clinical distress constructs.
Feeds into the existing GREEN/AMBER/RED escalation system.

DESIGN PRINCIPLES:
  1. No single signal triggers escalation alone — patterns require convergence
  2. Baselines are personal — first 7 days establish the patient's normal
  3. Signals are weighted by clinical evidence strength
  4. Late-night signals carry higher weight (circadian disruption literature)
  5. Trend matters more than absolutes (declining engagement > low engagement)

Dr Yuval Fouks — March 2026
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("melod-signal-analysis")


# ── Signal Taxonomy: Clinical Construct Mapping ───────────────────────

CONSTRUCT_SIGNALS = {
    "psychomotor_retardation": {
        "description": "Slowed motor/cognitive processing",
        "indicators": [
            "typing_speed_mean_ms > baseline + 1.5 SD",
            "touch_velocity_mean < baseline - 1 SD",
            "composition_time_mean_ms > baseline + 2 SD",
            "motion_magnitude_mean < baseline - 1 SD (stillness)",
        ],
        "weight": 0.8,
        "clinical_note": "Associated with depression severity (PHQ-9 items 7-8)",
    },
    "psychomotor_agitation": {
        "description": "Restless, erratic motor behaviour",
        "indicators": [
            "scroll_direction_changes > baseline + 2 SD",
            "motion_magnitude_std > baseline + 2 SD",
            "touch_velocity_std > baseline + 1.5 SD",
            "tap_interval_std_ms > baseline + 2 SD (irregular tapping)",
        ],
        "weight": 0.7,
        "clinical_note": "Associated with anxiety (GAD-7) and mixed depression",
    },
    "sleep_disturbance": {
        "description": "Circadian disruption pattern",
        "indicators": [
            "is_late_night == True (session between 00:00–05:00)",
            "session_hour variance > baseline",
            "low_battery_late_night events",
        ],
        "weight": 0.9,
        "clinical_note": "Strong predictor of depression onset (PHQ-9 item 3). "
                         "IVF patients: insomnia during TWW is near-universal but "
                         "chronic late-night usage across stages is concerning.",
    },
    "social_withdrawal": {
        "description": "Declining engagement and communication",
        "indicators": [
            "message_length_trend < -2 (messages getting shorter)",
            "message_count declining across sessions",
            "inter-session gap > patient's baseline + 2 days",
            "checkin_abandoned == True",
            "education_taps_count == 0 for 3+ sessions",
        ],
        "weight": 0.85,
        "clinical_note": "Core depressive feature. In IVF context, withdrawal "
                         "from the app may signal emotional shutdown or treatment dropout risk.",
    },
    "rumination": {
        "description": "Repetitive, stuck thought patterns",
        "indicators": [
            "deletion_ratio > 0.3 (heavy editing/rewriting)",
            "composition_time_max_ms > 3 minutes (laboured message crafting)",
            "same education topics revisited across sessions",
            "is_late_night + long session + high deletion_ratio",
        ],
        "weight": 0.6,
        "clinical_note": "Rumination predicts depression persistence. "
                         "Combined with late-night usage, suggests intrusive thoughts.",
    },
    "anhedonia": {
        "description": "Reduced interest and engagement breadth",
        "indicators": [
            "navigation_entropy declining (less exploration)",
            "session_duration declining",
            "education_taps_count declining",
            "total_touches per session declining",
        ],
        "weight": 0.75,
        "clinical_note": "PHQ-9 item 1. In IVF: patient stops engaging "
                         "with educational content or checking their journey timeline.",
    },
    "anxiety_escalation": {
        "description": "Intensifying anxiety behaviours",
        "indicators": [
            "session_frequency increasing (checking app more often)",
            "checkin_total_adjustments high (indecisive slider use)",
            "scroll_velocity_max spikes",
            "app_backgrounds high (frequent switching)",
            "stage_modal_opens > 2 per session (checking/rechecking stage)",
        ],
        "weight": 0.7,
        "clinical_note": "GAD-7 behavioural correlate. During TWW and result day, "
                         "some anxiety is expected — flag only if significantly above baseline.",
    },
    "hopelessness": {
        "description": "Declining future-oriented engagement",
        "indicators": [
            "journey panel visits declining to zero",
            "education_taps for positive_path / early_pregnancy == 0",
            "session_duration_ms trend declining sharply",
            "message_length_trend strongly negative",
            "abrupt session termination (short sessions, no check-in)",
        ],
        "weight": 0.9,
        "clinical_note": "Strongest passive predictor of suicidal ideation. "
                         "Patient stops looking forward in their journey view. "
                         "Combined with PHQ-9 item 9 history → RED.",
    },
}


class PatientBaseline:
    """
    Maintains a rolling personal baseline for each patient.
    First 7 days = calibration. After that, deviations are computed
    against the personal norm, not population averages.
    """

    def __init__(self):
        self.sessions = []
        self.calibrated = False
        self.baselines = {}  # feature_name -> {mean, std}

    def add_session(self, derived_features: dict):
        """Add a session's derived features to the baseline."""
        self.sessions.append({
            "timestamp": datetime.now().isoformat(),
            "features": derived_features,
        })

        # Calibrate after 7 sessions (roughly 1 week of daily use)
        if not self.calibrated and len(self.sessions) >= 7:
            self._calibrate()

    def _calibrate(self):
        """Compute baseline means and SDs from calibration sessions."""
        features_to_track = [
            "typing_speed_mean_ms", "typing_speed_std_ms",
            "touch_velocity_mean", "touch_velocity_std",
            "composition_time_mean_ms",
            "message_length_mean", "message_count",
            "scroll_velocity_mean", "scroll_direction_changes",
            "motion_magnitude_mean", "motion_magnitude_std",
            "session_duration_ms",
            "total_touches",
            "tab_switches",
            "education_taps_count",
            "deletion_ratio",
            "checkin_total_adjustments",
            "navigation_entropy",
        ]

        for feat in features_to_track:
            values = [s["features"].get(feat) for s in self.sessions if s["features"].get(feat) is not None]
            if len(values) >= 3:
                mean = sum(values) / len(values)
                std = math.sqrt(sum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1))
                self.baselines[feat] = {"mean": mean, "std": max(std, 0.001)}  # avoid div-by-zero

        self.calibrated = True
        logger.info(f"Baseline calibrated from {len(self.sessions)} sessions. "
                     f"Features tracked: {len(self.baselines)}")

    def z_score(self, feature: str, value: float) -> Optional[float]:
        """Compute z-score relative to personal baseline. Returns None if not calibrated."""
        if not self.calibrated or feature not in self.baselines:
            return None
        b = self.baselines[feature]
        return (value - b["mean"]) / b["std"]

    def get_recent_trend(self, feature: str, window: int = 5) -> Optional[float]:
        """Compute linear slope of a feature over recent sessions."""
        recent = self.sessions[-window:]
        values = [s["features"].get(feature) for s in recent if s["features"].get(feature) is not None]
        if len(values) < 3:
            return None
        return _linear_slope(values)


class SignalAnalyser:
    """
    Analyses passive signals for a single patient.
    Produces a structured risk assessment that feeds into the
    existing GREEN/AMBER/RED escalation engine.
    """

    def __init__(self, patient_id: str):
        self.patient_id = patient_id
        self.baseline = PatientBaseline()
        self.session_history = []  # condensed session summaries
        self.alert_history = []

    def analyse_session(self, derived_features: dict, session_metadata: dict) -> dict:
        """
        Analyse a single session's derived features.
        Returns a structured risk assessment.
        """
        self.baseline.add_session(derived_features)

        assessment = {
            "patient_id": self.patient_id,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_metadata.get("session_id"),
            "calibrated": self.baseline.calibrated,
            "constructs": {},
            "composite_risk_score": 0.0,
            "escalation_level": "GREEN",
            "escalation_triggers": [],
            "session_flags": [],
        }

        # ── Always-on flags (don't need baseline) ──
        self._check_absolute_flags(derived_features, session_metadata, assessment)

        # ── Baseline-relative analysis (needs calibration) ──
        if self.baseline.calibrated:
            self._check_psychomotor(derived_features, assessment)
            self._check_sleep(derived_features, session_metadata, assessment)
            self._check_withdrawal(derived_features, assessment)
            self._check_rumination(derived_features, assessment)
            self._check_anhedonia(derived_features, assessment)
            self._check_anxiety(derived_features, assessment)
            self._check_hopelessness(derived_features, assessment)

        # ── Compute composite risk score ──
        construct_scores = []
        for name, data in assessment["constructs"].items():
            if data["active"]:
                weight = CONSTRUCT_SIGNALS[name]["weight"]
                construct_scores.append(data["severity"] * weight)

        if construct_scores:
            assessment["composite_risk_score"] = min(1.0, sum(construct_scores) / len(construct_scores))

        # ── Map to escalation level ──
        active_count = sum(1 for c in assessment["constructs"].values() if c["active"])
        score = assessment["composite_risk_score"]

        if score >= 0.7 or any(t.get("critical") for t in assessment["escalation_triggers"]):
            assessment["escalation_level"] = "RED"
        elif score >= 0.4 or active_count >= 3:
            assessment["escalation_level"] = "AMBER"
        else:
            assessment["escalation_level"] = "GREEN"

        # Store for longitudinal tracking
        self.session_history.append({
            "timestamp": assessment["timestamp"],
            "risk_score": assessment["composite_risk_score"],
            "level": assessment["escalation_level"],
            "active_constructs": [n for n, d in assessment["constructs"].items() if d["active"]],
        })

        if assessment["escalation_level"] != "GREEN":
            self.alert_history.append(assessment)

        return assessment

    # ── ABSOLUTE FLAGS (no baseline needed) ─────────────────────────

    def _check_absolute_flags(self, feat, meta, assessment):
        """Flags that fire regardless of baseline."""

        # Late-night session
        if feat.get("is_late_night"):
            assessment["session_flags"].append({
                "flag": "late_night_session",
                "detail": f"Session at {feat.get('session_hour', '?')}:00",
                "severity": "moderate",
            })

        # Check-in abandoned
        if feat.get("checkin_abandoned"):
            assessment["session_flags"].append({
                "flag": "checkin_abandoned",
                "detail": "Patient started check-in but did not complete it",
                "severity": "mild",
            })

        # Very low battery + late night
        if feat.get("battery_level") is not None and feat["battery_level"] < 15 and feat.get("is_late_night"):
            assessment["session_flags"].append({
                "flag": "low_battery_late_night",
                "detail": f"Battery at {feat['battery_level']}% during late-night session",
                "severity": "moderate",
            })

        # Extremely short session with no engagement
        duration = feat.get("session_duration_ms", 0)
        if duration > 0 and duration < 15000 and feat.get("total_messages_sent", 0) == 0:
            assessment["session_flags"].append({
                "flag": "abrupt_session",
                "detail": f"Session lasted {duration // 1000}s with no messages",
                "severity": "mild",
            })

        # Very high deletion ratio (any session)
        if feat.get("deletion_ratio", 0) > 0.5 and feat.get("total_chars_typed", 0) > 50:
            assessment["session_flags"].append({
                "flag": "high_deletion_ratio",
                "detail": f"Deleted {int(feat['deletion_ratio'] * 100)}% of typed characters",
                "severity": "mild",
            })

    # ── CONSTRUCT DETECTORS ─────────────────────────────────────────

    def _check_psychomotor(self, feat, assessment):
        """Detect psychomotor retardation and agitation."""
        retardation_score = 0.0
        agitation_score = 0.0
        signals = []

        # Retardation: slower typing
        z = self.baseline.z_score("typing_speed_mean_ms", feat.get("typing_speed_mean_ms", 0))
        if z is not None and z > 1.5:  # Slower = higher IKI = positive z
            retardation_score += 0.4
            signals.append(f"Typing speed {z:.1f} SD slower than baseline")

        # Retardation: reduced touch velocity
        z = self.baseline.z_score("touch_velocity_mean", feat.get("touch_velocity_mean", 0))
        if z is not None and z < -1.0:
            retardation_score += 0.3
            signals.append(f"Touch velocity {abs(z):.1f} SD below baseline")

        # Retardation: longer composition time
        z = self.baseline.z_score("composition_time_mean_ms", feat.get("composition_time_mean_ms", 0))
        if z is not None and z > 2.0:
            retardation_score += 0.3
            signals.append(f"Message composition time {z:.1f} SD above baseline")

        # Retardation: reduced device motion (stillness)
        z = self.baseline.z_score("motion_magnitude_mean", feat.get("motion_magnitude_mean", 0))
        if z is not None and z < -1.0:
            retardation_score += 0.2
            signals.append(f"Device motion {abs(z):.1f} SD below baseline (unusual stillness)")

        assessment["constructs"]["psychomotor_retardation"] = {
            "active": retardation_score >= 0.4,
            "severity": min(1.0, retardation_score),
            "signals": signals,
        }

        # Agitation: erratic scrolling
        agit_signals = []
        z = self.baseline.z_score("scroll_direction_changes", feat.get("scroll_direction_changes", 0))
        if z is not None and z > 2.0:
            agitation_score += 0.3
            agit_signals.append(f"Scroll direction changes {z:.1f} SD above baseline")

        # Agitation: high motion variance
        z = self.baseline.z_score("motion_magnitude_std", feat.get("motion_magnitude_std", 0))
        if z is not None and z > 2.0:
            agitation_score += 0.3
            agit_signals.append(f"Device motion variability {z:.1f} SD above baseline")

        # Agitation: irregular tap timing
        z = self.baseline.z_score("typing_speed_std_ms", feat.get("typing_speed_std_ms", 0))
        if z is not None and z > 1.5:
            agitation_score += 0.2
            agit_signals.append(f"Typing rhythm irregularity {z:.1f} SD above baseline")

        assessment["constructs"]["psychomotor_agitation"] = {
            "active": agitation_score >= 0.4,
            "severity": min(1.0, agitation_score),
            "signals": agit_signals,
        }

    def _check_sleep(self, feat, meta, assessment):
        """Detect sleep disturbance patterns."""
        score = 0.0
        signals = []

        if feat.get("is_late_night"):
            score += 0.5
            signals.append(f"Session at {feat.get('session_hour', '?')}:00 (late night)")

        # Count late-night sessions in recent history
        recent = self.session_history[-7:] if self.session_history else []
        late_nights = sum(1 for s in self.baseline.sessions[-7:]
                         if s["features"].get("is_late_night"))
        if late_nights >= 3:
            score += 0.4
            signals.append(f"{late_nights} late-night sessions in last 7 sessions")

        # Session time irregularity
        hours = [s["features"].get("session_hour") for s in self.baseline.sessions[-7:]
                 if s["features"].get("session_hour") is not None]
        if len(hours) >= 4:
            hour_std = _std(hours)
            z = self.baseline.z_score("session_hour", feat.get("session_hour", 12))
            if hour_std > 4:  # High variance in usage times
                score += 0.2
                signals.append(f"Irregular session timing (SD={hour_std:.1f} hours)")

        assessment["constructs"]["sleep_disturbance"] = {
            "active": score >= 0.4,
            "severity": min(1.0, score),
            "signals": signals,
        }

    def _check_withdrawal(self, feat, assessment):
        """Detect social withdrawal patterns."""
        score = 0.0
        signals = []

        # Message length declining
        trend = self.baseline.get_recent_trend("message_length_mean", window=5)
        if trend is not None and trend < -5:  # Losing >5 chars per session
            score += 0.4
            signals.append(f"Message length declining (slope={trend:.1f} chars/session)")

        # Message count declining
        trend = self.baseline.get_recent_trend("message_count", window=5)
        if trend is not None and trend < -0.5:
            score += 0.3
            signals.append(f"Messages per session declining (slope={trend:.2f})")

        # Session frequency declining (inter-session gap increasing)
        if len(self.baseline.sessions) >= 5:
            recent_gaps = []
            for i in range(1, min(6, len(self.baseline.sessions))):
                t1 = datetime.fromisoformat(self.baseline.sessions[-i]["timestamp"])
                t2 = datetime.fromisoformat(self.baseline.sessions[-i - 1]["timestamp"])
                recent_gaps.append((t1 - t2).total_seconds() / 3600)  # hours
            if len(recent_gaps) >= 2:
                gap_trend = _linear_slope(recent_gaps)
                if gap_trend > 6:  # Gaps growing by >6 hours per session
                    score += 0.3
                    signals.append(f"Time between sessions increasing (slope={gap_trend:.1f} hrs)")

        # Check-in abandoned
        if feat.get("checkin_abandoned"):
            score += 0.2
            signals.append("Check-in started but not completed")

        # No education engagement for multiple sessions
        recent_edu = [s["features"].get("education_taps_count", 0)
                      for s in self.baseline.sessions[-5:]]
        if len(recent_edu) >= 3 and all(e == 0 for e in recent_edu[-3:]):
            score += 0.2
            signals.append("No education content engagement for 3+ sessions")

        assessment["constructs"]["social_withdrawal"] = {
            "active": score >= 0.4,
            "severity": min(1.0, score),
            "signals": signals,
        }

    def _check_rumination(self, feat, assessment):
        """Detect rumination patterns."""
        score = 0.0
        signals = []

        # High deletion ratio vs baseline
        z = self.baseline.z_score("deletion_ratio", feat.get("deletion_ratio", 0))
        if z is not None and z > 1.5:
            score += 0.3
            signals.append(f"Deletion ratio {z:.1f} SD above baseline (heavy rewriting)")

        # Very long composition times
        comp_time = feat.get("composition_time_max_ms", 0)
        if comp_time > 180000:  # > 3 minutes on a single message
            score += 0.3
            signals.append(f"Spent {comp_time // 60000} min composing a single message")

        # Late night + high deletion = strong rumination signal
        if feat.get("is_late_night") and feat.get("deletion_ratio", 0) > 0.3:
            score += 0.3
            signals.append("Late-night session with heavy message editing")

        # Repeated education topics (same topics across sessions)
        # This requires cross-session analysis which we do via session_history
        # Simplified: check if education taps are all on same topic
        # (full implementation needs topic tracking in session history)

        assessment["constructs"]["rumination"] = {
            "active": score >= 0.4,
            "severity": min(1.0, score),
            "signals": signals,
        }

    def _check_anhedonia(self, feat, assessment):
        """Detect anhedonia / reduced engagement patterns."""
        score = 0.0
        signals = []

        # Navigation entropy declining (less exploration)
        trend = self.baseline.get_recent_trend("navigation_entropy", window=5)
        if trend is not None and trend < -0.1:
            score += 0.3
            signals.append(f"Navigation diversity declining (entropy slope={trend:.3f})")

        # Session duration declining
        trend = self.baseline.get_recent_trend("session_duration_ms", window=5)
        if trend is not None and trend < -30000:  # Losing >30s per session
            score += 0.3
            signals.append(f"Session duration declining (slope={trend / 1000:.0f} s/session)")

        # Total touches declining
        trend = self.baseline.get_recent_trend("total_touches", window=5)
        if trend is not None and trend < -5:
            score += 0.2
            signals.append(f"Touch interactions declining (slope={trend:.1f}/session)")

        # Education engagement declining
        trend = self.baseline.get_recent_trend("education_taps_count", window=5)
        if trend is not None and trend < -0.3:
            score += 0.2
            signals.append("Education content engagement declining")

        assessment["constructs"]["anhedonia"] = {
            "active": score >= 0.4,
            "severity": min(1.0, score),
            "signals": signals,
        }

    def _check_anxiety(self, feat, assessment):
        """Detect anxiety escalation patterns."""
        score = 0.0
        signals = []

        # Increased session frequency (checking more often)
        if len(self.baseline.sessions) >= 5:
            recent_gaps = []
            for i in range(1, min(6, len(self.baseline.sessions))):
                t1 = datetime.fromisoformat(self.baseline.sessions[-i]["timestamp"])
                t2 = datetime.fromisoformat(self.baseline.sessions[-i - 1]["timestamp"])
                recent_gaps.append((t1 - t2).total_seconds() / 3600)
            if len(recent_gaps) >= 2:
                gap_trend = _linear_slope(recent_gaps)
                if gap_trend < -3:  # Sessions getting closer together
                    score += 0.3
                    signals.append("Session frequency increasing (checking more often)")

        # Indecisive slider use
        z = self.baseline.z_score("checkin_total_adjustments", feat.get("checkin_total_adjustments", 0))
        if z is not None and z > 1.5:
            score += 0.2
            signals.append(f"Check-in slider adjustments {z:.1f} SD above baseline (indecision)")

        # Rapid scroll spikes
        z = self.baseline.z_score("scroll_velocity_mean", feat.get("scroll_velocity_mean", 0))
        if z is not None and z > 2.0:
            score += 0.2
            signals.append(f"Scroll speed {z:.1f} SD above baseline")

        # Frequent app switching
        z_bg = self.baseline.z_score("app_backgrounds", feat.get("app_backgrounds", 0))
        if z_bg is not None and z_bg > 1.5:
            score += 0.2
            signals.append(f"App switching {z_bg:.1f} SD above baseline")

        # Stage modal obsessing
        if feat.get("stage_modal_opens", 0) > 2:
            score += 0.1
            signals.append(f"Opened stage selection {feat['stage_modal_opens']} times (checking)")

        assessment["constructs"]["anxiety_escalation"] = {
            "active": score >= 0.4,
            "severity": min(1.0, score),
            "signals": signals,
        }

    def _check_hopelessness(self, feat, assessment):
        """
        Detect hopelessness patterns. Highest clinical priority —
        strongest passive predictor of suicidal ideation.
        """
        score = 0.0
        signals = []

        # Journey panel visits dropping to zero
        panel_visits = feat.get("panel_visits", {})
        if panel_visits.get("panel-journey", 0) == 0:
            # Check if this is a change from baseline
            recent_journey = [s["features"].get("panel_visits", {}).get("panel-journey", 0)
                              for s in self.baseline.sessions[-5:]]
            if any(v > 0 for v in recent_journey[:-1]):  # Used to visit, now stopped
                score += 0.4
                signals.append("Stopped visiting journey/timeline view")

        # Sharp decline in session duration
        trend = self.baseline.get_recent_trend("session_duration_ms", window=5)
        if trend is not None and trend < -60000:  # Losing >60s per session
            score += 0.3
            signals.append(f"Session duration declining sharply (slope={trend / 1000:.0f} s/session)")

        # Message length collapsing
        trend = self.baseline.get_recent_trend("message_length_mean", window=5)
        if trend is not None and trend < -10:
            score += 0.3
            signals.append(f"Messages getting much shorter (slope={trend:.1f} chars/session)")

        # Abrupt sessions (opens app, does nothing, leaves)
        duration = feat.get("session_duration_ms", 0)
        if duration > 0 and duration < 30000 and feat.get("total_messages_sent", 0) == 0:
            score += 0.2
            signals.append("Abrupt session — opened app briefly with no engagement")

        # CRITICAL: if hopelessness construct active AND patient has prior RED escalation history
        if score >= 0.4:
            assessment["escalation_triggers"].append({
                "construct": "hopelessness",
                "severity": min(1.0, score),
                "signals": signals,
                "critical": score >= 0.7,
                "clinical_note": "Hopelessness pattern detected via passive signals. "
                                 "Cross-reference with PHQ-9 Item 9 history.",
            })

        assessment["constructs"]["hopelessness"] = {
            "active": score >= 0.4,
            "severity": min(1.0, score),
            "signals": signals,
        }


# ── Patient Analyser Registry ────────────────────────────────────────

_analysers: dict[str, SignalAnalyser] = {}


def get_analyser(patient_id: str) -> SignalAnalyser:
    """Get or create the signal analyser for a patient."""
    if patient_id not in _analysers:
        _analysers[patient_id] = SignalAnalyser(patient_id)
    return _analysers[patient_id]


def process_passive_signals(patient_id: str, payload: dict) -> dict:
    """
    Main entry point. Process a passive signal flush from the frontend.

    Args:
        patient_id: Patient identifier
        payload: Full payload from PassiveCollector.flush()
            - signals: list of raw events
            - derived_features: computed session features
            - session_metadata: session context

    Returns:
        Risk assessment dict with escalation level and construct analysis
    """
    analyser = get_analyser(patient_id)

    derived = payload.get("derived_features", {})
    metadata = payload.get("session_metadata", {})

    assessment = analyser.analyse_session(derived, metadata)

    if assessment["escalation_level"] != "GREEN":
        logger.warning(
            f"[Passive Signal] {assessment['escalation_level']} for patient={patient_id}. "
            f"Score={assessment['composite_risk_score']:.2f}. "
            f"Active constructs: {[n for n, d in assessment['constructs'].items() if d['active']]}"
        )

    return assessment


# ── Utilities ────────────────────────────────────────────────────────

def _mean(arr):
    if not arr:
        return 0
    return sum(arr) / len(arr)

def _std(arr):
    if len(arr) < 2:
        return 0
    m = _mean(arr)
    return math.sqrt(sum((v - m) ** 2 for v in arr) / (len(arr) - 1))

def _linear_slope(arr):
    n = len(arr)
    if n < 2:
        return 0
    x_mean = (n - 1) / 2
    y_mean = _mean(arr)
    num = sum((i - x_mean) * (arr[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den > 0 else 0
