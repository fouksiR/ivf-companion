#!/usr/bin/env python3
"""
patch_backend.py — Run this once against your app.py to:
  1. Rename all "Mira" references to "Melod-AI" / "Melod"
  2. Wire signal_analysis.py into the /passive-signals endpoint
  3. Update the companion personality prompt

Usage:
  python patch_backend.py          # patches app.py in-place (backup created)
  python patch_backend.py --dry    # preview changes without writing
"""

import re
import sys
import shutil
from pathlib import Path

DRY_RUN = '--dry' in sys.argv

APP_PATH = Path('app.py')
if not APP_PATH.exists():
    print("ERROR: app.py not found in current directory")
    sys.exit(1)

# Read original
original = APP_PATH.read_text(encoding='utf-8')
patched = original

# ════════════════════════════════════════════════════════════════
# 1. RENAME: Mira → Melod-AI / Melod
# ════════════════════════════════════════════════════════════════

# Doc title / comments
patched = patched.replace('IVF Companion — Phase 1 MVP Backend', 'Melod-AI — IVF Companion Backend')
patched = patched.replace('IVF Companion API', 'Melod-AI API')
patched = patched.replace('IVF Companion backend', 'Melod-AI backend')

# FastAPI app metadata
patched = patched.replace('title="IVF Companion API"', 'title="Melod-AI API"')
patched = patched.replace(
    'description="Longitudinal AI companion for emotional support & education during IVF/ART"',
    'description="Melod-AI: Longitudinal AI companion for emotional support & education during IVF/ART"'
)

# Health check response
patched = patched.replace('"service": "IVF Companion"', '"service": "Melod-AI"')

# Logger name
patched = patched.replace('getLogger("ivf-companion")', 'getLogger("melod-ai")')

# Companion personality — the core Mira → Melod rename
# In the COMPANION_SYSTEM prompt:
patched = patched.replace(
    'You are Mira, a warm and caring AI companion',
    'You are Melod, a warm and caring AI companion'
)
patched = patched.replace(
    "Introduce yourself as Mira.",
    "Introduce yourself as Melod."
)

# In any conversation context references
patched = patched.replace("Mira adapts her support", "Melod adapts its support")

# Welcome message generation
patched = patched.replace(
    "Generate a warm welcome message for",
    "Generate a warm welcome message from Melod for"
)

# Screening companion messages
patched = re.sub(
    r'Respond warmly.*?as Mira',
    lambda m: m.group(0).replace('Mira', 'Melod'),
    patched
)

print(f"[1/3] Renamed Mira → Melod-AI ({patched.count('Melod')} occurrences)")

# ════════════════════════════════════════════════════════════════
# 2. ADD signal_analysis import at top of file
# ════════════════════════════════════════════════════════════════

IMPORT_LINE = "from signal_analysis import process_passive_signals, get_analyser"

if 'signal_analysis' not in patched:
    # Add after the last existing import block
    # Find "import anthropic" and add after it
    if 'import anthropic' in patched:
        patched = patched.replace(
            'import anthropic',
            'import anthropic\n\n# Passive digital phenotyping analysis\ntry:\n    from signal_analysis import process_passive_signals, get_analyser\n    SIGNAL_ANALYSIS_AVAILABLE = True\nexcept ImportError:\n    SIGNAL_ANALYSIS_AVAILABLE = False'
        )
        print("[2/3] Added signal_analysis import (with graceful fallback)")
    else:
        print("[2/3] WARNING: Could not find 'import anthropic' — add import manually")
else:
    print("[2/3] signal_analysis import already present")

# ════════════════════════════════════════════════════════════════
# 3. UPGRADE /passive-signals endpoint to use signal_analysis
# ════════════════════════════════════════════════════════════════

# The current /passive-signals endpoint just stores raw signals.
# We need to add analysis processing.

# Find the passive-signals endpoint and enhance it.
# The original likely looks something like:
#   @app.post("/passive-signals")
#   async def receive_passive_signals(req: PassiveSignalBatch):
#       patient = get_or_create_patient(req.patient_id)
#       passive_signals_db.setdefault(req.patient_id, []).extend(req.signals)
#       return {"status": "ok", "signals_received": len(req.signals)}

# We need a more robust replacement. Look for the endpoint:
OLD_PASSIVE_PATTERNS = [
    # Pattern 1: simple store and return
    'return {"status": "ok", "signals_received": len(req.signals)}',
    # Pattern 2: might have stored count
    '"signals_received": len(req.signals)',
]

PASSIVE_UPGRADE = '''
    # ── Run passive signal analysis if available ──
    analysis_result = None
    if SIGNAL_ANALYSIS_AVAILABLE:
        try:
            # Build payload matching PassiveCollector.flush() format
            payload = {
                "signals": req.signals if hasattr(req, 'signals') else [],
                "derived_features": req.dict().get("derived_features", {}),
                "session_metadata": req.dict().get("session_metadata", {}),
            }
            analysis_result = process_passive_signals(req.patient_id, payload)

            # Feed passive escalation into existing escalation system
            if analysis_result and analysis_result.get("escalation_level") in ("AMBER", "RED"):
                escalation = {
                    "level": analysis_result["escalation_level"],
                    "reason": f"Passive signal analysis: {', '.join(analysis_result.get('escalation_triggers', [{}])[:1])}",
                    "signals": [c for c, d in analysis_result.get("constructs", {}).items() if d.get("active")],
                    "source": "passive_phenotyping",
                    "composite_risk_score": analysis_result.get("composite_risk_score", 0),
                    "timestamp": datetime.now().isoformat(),
                }
                escalations_db.setdefault(req.patient_id, []).append(escalation)
                logger.warning(f"[Passive] {escalation['level']} escalation for patient={req.patient_id}. "
                             f"Active constructs: {escalation['signals']}")
        except Exception as e:
            logger.warning(f"[Passive] Analysis error for patient={req.patient_id}: {e}")

'''

# Try to insert the analysis code before the return statement
for pattern in OLD_PASSIVE_PATTERNS:
    if pattern in patched:
        # Insert analysis block before the return
        patched = patched.replace(
            pattern,
            PASSIVE_UPGRADE + '    return {"status": "ok", "signals_received": len(req.signals), "analysis": analysis_result}'
        )
        print("[3/3] Upgraded /passive-signals endpoint with signal analysis integration")
        break
else:
    # If we can't find the pattern, check if endpoint exists at all
    if '/passive-signals' in patched or 'passive_signals' in patched:
        print("[3/3] WARNING: /passive-signals endpoint found but pattern didn't match.")
        print("       You'll need to manually add the analysis call. See MANUAL_INTEGRATION below.")
    else:
        print("[3/3] WARNING: No /passive-signals endpoint found. Adding complete endpoint.")
        # Add the endpoint before the last line (if __name__)
        NEW_ENDPOINT = '''

# ── Passive Signal Collection + Analysis ─────────────────────────────

class PassiveSignalPayload(BaseModel):
    """Payload from PassiveCollector.flush()"""
    patient_id: str
    session_id: Optional[str] = None
    signals: list = []
    derived_features: dict = {}
    session_metadata: dict = {}

@app.post("/passive-signals")
async def receive_passive_signals(req: PassiveSignalPayload):
    """Receive passive digital phenotyping signals and run analysis."""
    patient = get_or_create_patient(req.patient_id)

    # Store raw signals
    passive_signals_db.setdefault(req.patient_id, []).extend(req.signals)

    # Run signal analysis
    analysis_result = None
    if SIGNAL_ANALYSIS_AVAILABLE:
        try:
            payload = {
                "signals": req.signals,
                "derived_features": req.derived_features,
                "session_metadata": req.session_metadata,
            }
            analysis_result = process_passive_signals(req.patient_id, payload)

            # Feed passive escalation into existing escalation system
            if analysis_result and analysis_result.get("escalation_level") in ("AMBER", "RED"):
                escalation = {
                    "level": analysis_result["escalation_level"],
                    "reason": "Passive behavioural signal analysis",
                    "signals": [c for c, d in analysis_result.get("constructs", {}).items() if d.get("active")],
                    "source": "passive_phenotyping",
                    "composite_risk_score": analysis_result.get("composite_risk_score", 0),
                    "timestamp": datetime.now().isoformat(),
                }
                escalations_db.setdefault(req.patient_id, []).append(escalation)
                logger.warning(f"[Passive] {escalation['level']} for patient={req.patient_id}")
        except Exception as e:
            logger.warning(f"[Passive] Analysis error: {e}")

    return {
        "status": "ok",
        "signals_received": len(req.signals),
        "analysis": analysis_result,
    }
'''
        # Insert before EOF
        if 'if __name__' in patched:
            patched = patched.replace('if __name__', NEW_ENDPOINT + '\nif __name__')
        else:
            patched += NEW_ENDPOINT
        print("[3/3] Added complete /passive-signals endpoint with analysis")


# ════════════════════════════════════════════════════════════════
# 4. UPDATE PassiveSignalBatch model to accept full payload
# ════════════════════════════════════════════════════════════════

# The original model only accepts patient_id + signals list.
# The new frontend sends derived_features and session_metadata too.
OLD_MODEL = '''class PassiveSignalBatch(BaseModel):
    """Passive behavioural signals collected silently from the patient app."""
    patient_id: str
    signals: list[dict] # Each: {signal_type, value, timestamp, metadata}'''

NEW_MODEL = '''class PassiveSignalBatch(BaseModel):
    """Passive behavioural signals + derived features from digital phenotyping collector."""
    patient_id: str
    session_id: Optional[str] = None
    signals: list = Field(default_factory=list)
    derived_features: dict = Field(default_factory=dict)
    session_metadata: dict = Field(default_factory=dict)'''

if OLD_MODEL in patched:
    patched = patched.replace(OLD_MODEL, NEW_MODEL)
    print("[+] Updated PassiveSignalBatch model to accept derived_features + session_metadata")
elif 'PassiveSignalBatch' in patched:
    print("[~] PassiveSignalBatch exists but pattern didn't match exactly — check manually")

# ════════════════════════════════════════════════════════════════
# WRITE RESULT
# ════════════════════════════════════════════════════════════════

if DRY_RUN:
    # Show diff summary
    orig_lines = original.splitlines()
    patch_lines = patched.splitlines()
    added = len(patch_lines) - len(orig_lines)
    changed = sum(1 for a, b in zip(orig_lines, patch_lines) if a != b)
    print(f"\n=== DRY RUN ===")
    print(f"Lines: {len(orig_lines)} → {len(patch_lines)} (+{added})")
    print(f"Lines changed: {changed}")
    print(f"Run without --dry to apply changes.")
else:
    # Backup original
    backup = APP_PATH.with_suffix('.py.bak')
    shutil.copy2(APP_PATH, backup)
    print(f"\n[Backup] Original saved to {backup}")

    # Write patched version
    APP_PATH.write_text(patched, encoding='utf-8')
    print(f"[Done] app.py patched successfully ({len(patched)} bytes)")

print(f"""
═══════════════════════════════════════════════════════════
NEXT STEPS:
═══════════════════════════════════════════════════════════
1. Ensure signal_analysis.py is in the same directory as app.py
2. Test locally:  uvicorn app:app --reload --port 8080
3. Deploy:
   gcloud run deploy ivf-companion \\
     --source . \\
     --region australia-southeast1 \\
     --allow-unauthenticated \\
     --set-env-vars ANTHROPIC_API_KEY=your_key \\
     --memory 2Gi
═══════════════════════════════════════════════════════════
""")
