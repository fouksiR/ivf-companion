"""
Firebase Realtime DB persistence layer for Melod-AI.

Wraps the in-memory dicts with Firebase read/write so data survives Cloud Run restarts.
Uses firebase-admin SDK with Application Default Credentials (works out of the box on GCP).

Setup:
1. Enable Firebase Realtime DB in your GCP project (Firebase Console → Build → Realtime Database)
2. Set FIREBASE_DB_URL env var to your database URL (e.g., https://your-project-default-rtdb.firebaseio.com)
3. On Cloud Run, Application Default Credentials handle auth automatically
4. For local dev, set GOOGLE_APPLICATION_CREDENTIALS to a service account key JSON

Usage:
    from firebase_db import db
    db.save_patient(patient_id, patient_dict)
    patient = db.load_patient(patient_id)
    db.append_checkin(patient_id, checkin_dict)
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("melod-firebase")

# ── Firebase init ────────────────────────────────────────
_fb_app = None
_fb_ref = None
_enabled = False

def _init_firebase():
    """Initialize Firebase Admin SDK. Safe to call multiple times."""
    global _fb_app, _fb_ref, _enabled

    db_url = os.environ.get("FIREBASE_DB_URL", "")
    if not db_url:
        logger.warning("FIREBASE_DB_URL not set — running in memory-only mode (data will be lost on restart)")
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, db as fb_db

        if not _fb_app:
            # On Cloud Run, Application Default Credentials work automatically
            # For local dev, set GOOGLE_APPLICATION_CREDENTIALS env var
            cred = credentials.ApplicationDefault()
            _fb_app = firebase_admin.initialize_app(cred, {
                'databaseURL': db_url
            })
            logger.info(f"Firebase initialized: {db_url}")

        _fb_ref = fb_db.reference('melod_ai')
        _enabled = True
        return True
    except Exception as e:
        logger.warning(f"Firebase init failed: {e} — running in memory-only mode")
        return False


class FirebaseDB:
    """
    Firebase Realtime DB wrapper.
    All methods are safe to call even if Firebase is not configured —
    they silently no-op and the app falls back to in-memory only.
    """

    def __init__(self):
        self.ready = _init_firebase()

    # ── Patients ────────────────────────────────────────

    def save_patient(self, patient_id: str, data: dict):
        """Save/update a patient record."""
        if not _enabled: return
        try:
            _fb_ref.child('patients').child(patient_id).set(data)
        except Exception as e:
            logger.warning(f"Firebase save_patient error: {e}")

    def load_patient(self, patient_id: str) -> dict | None:
        """Load a single patient. Returns None if not found."""
        if not _enabled: return None
        try:
            return _fb_ref.child('patients').child(patient_id).get()
        except Exception as e:
            logger.warning(f"Firebase load_patient error: {e}")
            return None

    def load_all_patients(self) -> dict:
        """Load all patients. Returns {patient_id: data}."""
        if not _enabled: return {}
        try:
            result = _fb_ref.child('patients').get()
            return result or {}
        except Exception as e:
            logger.warning(f"Firebase load_all_patients error: {e}")
            return {}

    # ── Conversations ───────────────────────────────────

    def append_conversation(self, patient_id: str, message: dict):
        """Append a message to a patient's conversation."""
        if not _enabled: return
        try:
            _fb_ref.child('conversations').child(patient_id).push(message)
        except Exception as e:
            logger.warning(f"Firebase append_conversation error: {e}")

    def load_conversations(self, patient_id: str) -> list:
        """Load conversation history for a patient."""
        if not _enabled: return []
        try:
            result = _fb_ref.child('conversations').child(patient_id).get()
            if result is None: return []
            # Firebase returns dict with push keys — convert to list sorted by key
            if isinstance(result, dict):
                return [v for k, v in sorted(result.items())]
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning(f"Firebase load_conversations error: {e}")
            return []

    # ── Check-ins ───────────────────────────────────────

    def append_checkin(self, patient_id: str, checkin: dict):
        """Append a check-in to a patient's history."""
        if not _enabled: return
        try:
            _fb_ref.child('checkins').child(patient_id).push(checkin)
        except Exception as e:
            logger.warning(f"Firebase append_checkin error: {e}")

    def load_checkins(self, patient_id: str) -> list:
        """Load check-in history."""
        if not _enabled: return []
        try:
            result = _fb_ref.child('checkins').child(patient_id).get()
            if result is None: return []
            if isinstance(result, dict):
                return [v for k, v in sorted(result.items())]
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning(f"Firebase load_checkins error: {e}")
            return []

    # ── Screenings ──────────────────────────────────────

    def append_screening(self, patient_id: str, screening: dict):
        if not _enabled: return
        try:
            _fb_ref.child('screenings').child(patient_id).push(screening)
        except Exception as e:
            logger.warning(f"Firebase append_screening error: {e}")

    def load_screenings(self, patient_id: str) -> list:
        if not _enabled: return []
        try:
            result = _fb_ref.child('screenings').child(patient_id).get()
            if result is None: return []
            if isinstance(result, dict):
                return [v for k, v in sorted(result.items())]
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning(f"Firebase load_screenings error: {e}")
            return []

    # ── Escalations ─────────────────────────────────────

    def append_escalation(self, patient_id: str, escalation: dict):
        if not _enabled: return
        try:
            _fb_ref.child('escalations').child(patient_id).push(escalation)
        except Exception as e:
            logger.warning(f"Firebase append_escalation error: {e}")

    def load_escalations(self, patient_id: str) -> list:
        if not _enabled: return []
        try:
            result = _fb_ref.child('escalations').child(patient_id).get()
            if result is None: return []
            if isinstance(result, dict):
                return [v for k, v in sorted(result.items())]
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning(f"Firebase load_escalations error: {e}")
            return []

    # ── Phenotype Snapshots ──────────────────────────────

    def save_phenotype_snapshot(self, patient_id: str, data: dict):
        """Save a phenotype snapshot for longitudinal tracking.

        Writes to melod_ai/phenotype_history/{patient_id}/{timestamp_key}.
        Each snapshot contains construct z-scores, escalation level,
        raw derived features, and check-in scores.
        """
        if not _enabled:
            return
        try:
            ts_key = data.get("timestamp", datetime.now().isoformat()).replace(".", "_").replace(":", "-")
            _fb_ref.child('phenotype_history').child(patient_id).child(ts_key).set(data)
        except Exception as e:
            logger.warning(f"Firebase save_phenotype_snapshot error: {e}")

    def load_phenotype_history(self, patient_id: str, limit: int = 200) -> list:
        """Load phenotype snapshots for a patient, sorted by timestamp."""
        if not _enabled:
            return []
        try:
            result = _fb_ref.child('phenotype_history').child(patient_id).get()
            if result is None:
                return []
            if isinstance(result, dict):
                items = [v for k, v in sorted(result.items())]
                return items[-limit:]
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning(f"Firebase load_phenotype_history error: {e}")
            return []

    # ── Passive Signals ─────────────────────────────────

    def append_passive_signals(self, patient_id: str, signals: list):
        """Append a batch of passive signals."""
        if not _enabled: return
        try:
            # Store as a single batch entry to avoid too many writes
            batch = {
                "timestamp": datetime.now().isoformat(),
                "signals": signals[:50]  # Cap per batch to avoid huge entries
            }
            _fb_ref.child('passive_signals').child(patient_id).push(batch)
        except Exception as e:
            logger.warning(f"Firebase append_passive_signals error: {e}")

    def load_passive_signals(self, patient_id: str) -> list:
        if not _enabled: return []
        try:
            result = _fb_ref.child('passive_signals').child(patient_id).get()
            if result is None: return []
            # Flatten batches into single list
            all_signals = []
            items = sorted(result.items()) if isinstance(result, dict) else []
            for k, v in items:
                if isinstance(v, dict) and 'signals' in v:
                    all_signals.extend(v['signals'])
                elif isinstance(v, dict):
                    all_signals.append(v)
            return all_signals
        except Exception as e:
            logger.warning(f"Firebase load_passive_signals error: {e}")
            return []

    # ── Bulk load on startup ────────────────────────────

    def load_all_into_memory(self, patients_db, conversations_db, checkins_db,
                              screenings_db, escalations_db, passive_signals_db):
        """
        Load all data from Firebase into the in-memory dicts on startup.
        This lets the app warm up its cache from persistent storage.
        """
        if not _enabled:
            logger.info("Firebase not enabled — starting with empty in-memory stores")
            return 0

        try:
            # Load patients
            patients = self.load_all_patients()
            count = 0
            for pid, pdata in patients.items():
                patients_db[pid] = pdata
                conversations_db[pid] = self.load_conversations(pid)
                checkins_db[pid] = self.load_checkins(pid)
                screenings_db[pid] = self.load_screenings(pid)
                escalations_db[pid] = self.load_escalations(pid)
                passive_signals_db[pid] = self.load_passive_signals(pid)
                count += 1

            logger.info(f"Firebase: loaded {count} patients into memory")
            return count
        except Exception as e:
            logger.warning(f"Firebase bulk load error: {e}")
            return 0


# Singleton instance
db = FirebaseDB()
