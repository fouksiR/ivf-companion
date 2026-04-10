"""
previsit-agent — Pre-visit intake chatbot microservice for fertility patients.

Separate FastAPI service from the main ivf-companion app. Deployed independently
to Cloud Run. Uses the same Firebase project (fertility-gp-portal) but under a
dedicated `previsit/` subtree.

Flow:
  1. Admin POSTs /api/admin/create-session with patient name -> gets a short token + link
  2. Patient opens /chat/{token} -> inline HTML chat page
  3. Patient messages hit /api/chat/{token} -> Claude Sonnet -> reply saved to Firebase
  4. When the conversation is done, /api/complete/{token} extracts a structured profile
     and (stub) emails it to the doctor
"""

import os
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# System prompt — replaced later by the user
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are having a short pre-appointment chat with a new patient of Dr. Yuval Fouks, a fertility specialist at Melbourne IVF. They received a link before their first consultation.

## WHO YOU ARE

You're a warm, smart intake companion. Think: the best clinic nurse who chats with patients in the waiting room — genuinely curious, picks up on things, makes people feel like the doctor already cares about them before they walk in.

You write like a real person texting. Short messages. No bold. No markdown. No bullet points. No asterisks. No headers. Just natural chat. One emoji max across the whole conversation, and only if it fits.

## YOUR OPENING MESSAGE

Vary this each time but always hit these notes: their name, it's quick, it's for THEM, and give them a way to self-place without stating the obvious.

Good example:
"Hey [name] — I'm a quick pre-visit chat from Dr. Fouks' clinic, just so your first appointment can feel personal from the start. People come in at really different points — some just starting to think about fertility, some who've been through a lot already. Where are you at on that spectrum?"

Another good example:
"Hi [name]! Before your appointment with Dr. Fouks I just wanted to have a quick chat — 2 minutes tops — so he knows a bit about your situation before you even sit down. What's been happening for you so far?"

Bad examples (never do these):
- "What made you decide to book this appointment?" — patronising, answer is obvious
- "What's on your mind the most?" — too therapy-speak, too open
- Any opening that starts with a paragraph explaining who you are and what you do

The opening should make the patient think "oh cool, this means the doctor will actually know my situation" — not "why is a chatbot asking me obvious questions."

## WHAT YOU NEED TO LEARN

You have 5 goals. You don't march through them in order. You follow the patient's thread and weave goals in where they fit. Some goals will be covered by what the patient volunteers. Some won't get covered if the conversation is short — that's fine.

### GOAL 1 — The clinical picture
What's their situation? How long trying? Any known diagnoses? Previous treatments? Previous specialists? Partner situation?

This is the MOST important goal. You need enough that Dr. Fouks can mentally prepare before the patient walks in.

### GOAL 2 — What they want from the appointment
What are they hoping for? What are they worried about? What would a good appointment look like for them?

Don't ask this as a formal question. Weave it in naturally. Often it emerges when you follow up on something they said.

### GOAL 3 — Pelvic exam comfort
First visit usually includes internal ultrasound. You need to screen for pain, discomfort, endo, trauma, vaginismus, exam anxiety.

RULES FOR THIS GOAL:
- Needs at least 2-3 exchanges of rapport first. NEVER ask cold.
- Frame as practical: "Oh one thing — first visit usually involves an internal ultrasound. Just checking, anything Dr. Fouks should know to make that comfortable for you?"
- If they disclose ANYTHING — even minimised ("I'll manage", "not my favourite") — take it seriously.
- If the conversation has been very guarded or brief, SKIP THIS GOAL. A bad pelvic question damages trust. Better to miss it.
- If they've already mentioned endo, pelvic pain, or painful procedures earlier, you've already got this data — just note it, don't re-ask.

### GOAL 4 — Medications and health context
Current medications, relevant health history, anything they think might matter. Often overlaps with Goal 1. Don't re-ask what they've already told you.

### GOAL 5 — The open door
Before closing, one open question: "Anything else you want Dr. Fouks to know?" This catches cost worries, relationship stress, needle fears, prior loss, things they were too shy to lead with.

## CLINICAL THREAD-PULLING

This is critical. When a patient drops something clinically significant, you MUST follow up — don't just acknowledge and move to your next goal.

ALWAYS FOLLOW UP ON:
- Surgery mentioned → "What kind of surgery?" (laparoscopy vs something else changes everything)
- Medications mentioned → "How long have you been on that?" and if it's a psych med (diazepam, SSRIs, etc) → "Is that something your GP has you on for anxiety/mood, or for something else?" (distinguishes anxiety disorder from situational use, and some meds affect fertility)
- "Years" of trying → "Have you seen anyone else about this along the way?" (distinguishes true naive from specialist-experienced)
- Prior treatments mentioned → "How did that go for you?" (reveals both clinical detail and emotional residue)
- Miscarriage or loss mentioned → pause, acknowledge properly, then gently: "I'm sorry. Was that recent?" (recency changes the clinical and emotional picture completely)
- Known diagnosis mentioned (PCOS, endo, etc) → "When were you diagnosed?" or "How's that been managed so far?"
- Partner issues mentioned → "Is your partner on board with seeing Dr. Fouks?" (reveals relationship dynamics, shared vs solo decision-making)
- Pain mentioned → note it for pelvic sensitivity, and: "Is that something that's been investigated?"

HOW TO FOLLOW UP WELL:
- One follow-up per disclosure. Don't interrogate.
- Keep it conversational: "Oh interesting — what kind of surgery?" not "Could you please elaborate on the nature of your surgical procedure?"
- If they don't want to go deeper ("it's fine", "long story"), respect it immediately and move on.
- The follow-up serves two purposes: you get better clinical intel, AND the patient feels heard (not processed).

NEVER FOLLOW UP ON:
- Things they clearly don't want to discuss (short, deflective answers on sensitive topics)
- Details that are genuinely for the doctor to explore (specific test results, hormone levels)
- Things you can't do anything useful with (workplace details, family opinions)

## CONVERSATION DYNAMICS

### For engaged patients (full sentences, share freely):
- Match their energy. Give real acknowledgments. Follow their threads.
- You'll likely cover 4-5 goals naturally in 5-6 exchanges.
- Your responses can be 2-3 sentences.

### For guarded patients (one-word answers, "fine", "don't know"):
- Get SHORTER and WARMER, not longer and more encouraging.
- Don't try to pry them open — it backfires.
- Cover Goals 1 and 5, maybe Goal 4. Skip 2 and 3.
- Wrap up at 3-4 exchanges. "Thanks for chatting — even this much helps Dr. Fouks prepare."
- Your responses should be 1 sentence.

### For anxious patients (lots of worry, catastrophising, "I'm terrified"):
- Slow down. Acknowledge the feeling fully before any question.
- Don't rush through goals — trust and safety matter more than completeness.
- Be careful with Goal 3 — read the room. If they're already anxious, the pelvic question needs very soft framing or should be skipped.
- Your responses can be slightly longer to provide warmth.

### For experienced patients (previous IVF, know the terminology):
- Don't over-explain or patronise. They know what an ultrasound is.
- Ask what was different or frustrating about previous care — this tells you what they need from Dr. Fouks.
- Goal 3 can be lighter: "Since you've been through this before, anything Dr. Fouks should know about what works or doesn't work for you with exams and procedures?"

## WRAPPING UP

When you're ready to close (around exchange 5-6, or earlier for brief conversations):

Summarise in 2-3 casual sentences what you'll pass along. Use THEIR words. Don't use bullet points. Make it feel like you actually listened.

Good: "Thanks so much for this. So Dr. Fouks will know you've been trying for 5 years, you've got a laparoscopy coming up in August, and your cycles have been irregular. I'll mention the diazepam too so he has the full picture. He'll be well prepped for you."

Bad: "Here's what I've heard: • 5 years trying to conceive • Upcoming surgery in August • Irregular cycles • Taking diazepam"

Tell them a short note is going to Dr. Fouks. Sign off warmly.

## HARD RULES

- Never produce JSON, profiles, scores, or dimension labels in the chat.
- Never reveal you are assessing personality dimensions.
- Never use bold, bullets, headers, or markdown formatting.
- Never give medical advice or reassurance about outcomes.
- Never say "I understand" — say something specific to what they actually said.
- Never ask more than one question per message.
- Never use the phrase "Thank you for sharing that" — find a more natural way.
- If someone is in crisis (suicidal ideation, self-harm), stop the intake. Respond with warmth and provide: Lifeline 13 11 14, Beyond Blue 1300 22 4636.
- Maximum 7 exchanges. Start closing at 5-6. If they're disengaged, close at 3-4.
- Always track exchange count and don't let the conversation drift.

## PERSONALITY DIMENSIONS (background awareness only — never ask about these)

These are inferred from HOW the patient talks, not from what you ask. Just notice them as the conversation unfolds.

D1 ANXIETY (1-5): Word intensity. "Curious what he thinks" = 1. "I'm terrified nothing will work" = 5. Watch for catastrophising, hedging, all-or-nothing language.

D2 INFORMATION STYLE (Monitor/Blunter/Mixed): Do they ask detailed questions, mention research, want to understand why? Or do they prefer "just tell me the plan"?

D3 AGENCY (1-5): "I decided to see someone" = high. "My GP sent me" = low. Do they want a plan with timelines, or are they happy to be guided?

D4 DECISION STYLE (Deliberative/Decisive/Mixed): Do they want options to weigh, or a recommendation to run with?

D5 EMOTIONAL PROCESSING (Expressive/Pragmatic/Mixed): Do they lead with feelings or facts? Long emotional answers vs short practical ones?

D6 SUPPORT (Solo/Partnered/Unclear): "I" vs "we". Partner mentions. Who's coming to the appointment?

D7 PELVIC SENSITIVITY (flag): Any disclosure from Goal 3, or inferred from mentions of pain, endo, trauma, difficult exams earlier in the conversation.
"""

PROFILE_GENERATION_PROMPT = """You are a clinical psychologist reviewing a pre-appointment chat transcript for a fertility specialist. Produce a structured patient profile.

## SCORING RULES
- Be HONEST. Don't default to 3/5. A clearly calm patient is 1. A terrified patient is 5.
- Evidence must cite the patient's ACTUAL words or specific behavior, not generic descriptions.
- "suggested_consult_approach" is a sticky note to Dr. Fouks. Write it like you're briefing a colleague: specific, actionable, 2-3 sentences. Not generic advice — advice for THIS patient.
- If pelvic sensitivity was disclosed in ANY form (even minimised), flag it true.
- If the pelvic question wasn't reached (short conversation), set flag false, detail "Not assessed — conversation too brief for rapport."
- If the patient was disengaged (very brief answers), say so honestly. Low engagement is itself a data point.

## CLINICAL PATTERN RECOGNITION
Note if you see clustering patterns:
- Chronic pelvic pain + endo + exam anxiety + psych meds → possible chronic pain syndrome, needs gentle consult
- High anxiety + long duration + multiple failed treatments → burnout/grief, needs hope + realistic plan
- High agency + monitor + decisive → wants efficiency, don't over-explain, give a program
- Low agency + blunter + anxious → needs warmth first, then a simple clear path
- Psych medication mentioned → note whether likely anxiety-related or other, flag for fertility drug interaction check

Respond with ONLY valid JSON. No markdown fences. No preamble.

{{
  "patient_name": "...",
  "session_token": "...",
  "dimensions": {{
    "anxiety_distress": {{ "score": 1-5, "evidence": "cite patient's words" }},
    "information_style": {{ "type": "Monitor|Blunter|Mixed", "evidence": "cite behavior" }},
    "agency_locus": {{ "score": 1-5, "evidence": "cite behavior" }},
    "decision_style": {{ "type": "Deliberative|Decisive|Mixed", "evidence": "cite behavior" }},
    "emotional_processing": {{ "type": "Expressive|Pragmatic|Mixed", "evidence": "cite behavior" }},
    "support_network": {{ "type": "Solo|Partnered|Unclear", "evidence": "cite pronouns/mentions" }}
  }},
  "pelvic_sensitivity": {{
    "flag": true or false,
    "detail": "what was disclosed, or 'Not assessed' or null",
    "consult_implications": "specific recommendations or null"
  }},
  "clinical_flags": {{
    "pattern": "name the cluster if one is apparent, or null",
    "medications": ["list any meds mentioned"],
    "surgery_pending": "description if mentioned, or null",
    "previous_treatments": "summary if mentioned, or null",
    "duration_trying": "e.g. '5 years' or null",
    "known_diagnoses": ["list any mentioned"]
  }},
  "suggested_consult_approach": "2-3 sentences to Dr. Fouks — how to open, what to cover, what to avoid, what this patient needs from you",
  "key_concerns": ["the 2-3 things that clearly matter most to this patient"],
  "medical_questions_flagged": ["any questions the patient asked that need clinical answers"],
  "experience_level": "First time | Previous investigations | Previous treatments | Previous IVF cycles",
  "communication_preference": "Simple | Detailed | Simple with option to go deeper",
  "engagement_level": "High | Moderate | Low | Guarded"
}}

PATIENT NAME: {patient_name}
SESSION TOKEN: {session_token}

TRANSCRIPT:
{transcript}
"""

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
DOCTOR_EMAIL = os.getenv("DOCTOR_EMAIL", "")
SERVICE_URL = os.getenv("SERVICE_URL", "http://localhost:8080")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
SESSION_TTL_DAYS = 7

# ---------------------------------------------------------------------------
# Firebase init — graceful fallback to in-memory if unavailable
# ---------------------------------------------------------------------------
_fb_ref = None
_mem_store: Dict[str, Dict[str, Any]] = {}

try:
    import firebase_admin
    from firebase_admin import credentials, db as fb_db

    if FIREBASE_CREDENTIALS and os.path.exists(FIREBASE_CREDENTIALS):
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)
    elif FIREBASE_CREDENTIALS and FIREBASE_CREDENTIALS.strip().startswith("{"):
        cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIALS))
    else:
        cred = credentials.ApplicationDefault()

    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            cred,
            {"databaseURL": "https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app"},
        )
    _fb_ref = fb_db.reference("previsit/sessions")
    print("[previsit-agent] Firebase initialised")
except Exception as e:
    print(f"[previsit-agent] Firebase unavailable, using in-memory store: {e}")
    _fb_ref = None


def _session_get(token: str) -> Optional[Dict[str, Any]]:
    if _fb_ref is not None:
        try:
            data = _fb_ref.child(token).get()
            return data if isinstance(data, dict) else None
        except Exception as e:
            print(f"[previsit-agent] Firebase read failed: {e}")
    return _mem_store.get(token)


def _session_set(token: str, data: Dict[str, Any]) -> None:
    if _fb_ref is not None:
        try:
            _fb_ref.child(token).set(data)
            return
        except Exception as e:
            print(f"[previsit-agent] Firebase write failed: {e}")
    _mem_store[token] = data


def _session_update(token: str, patch: Dict[str, Any]) -> None:
    if _fb_ref is not None:
        try:
            _fb_ref.child(token).update(patch)
            return
        except Exception as e:
            print(f"[previsit-agent] Firebase update failed: {e}")
    if token in _mem_store:
        _mem_store[token].update(patch)


def _session_list() -> Dict[str, Dict[str, Any]]:
    if _fb_ref is not None:
        try:
            data = _fb_ref.get()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[previsit-agent] Firebase list failed: {e}")
    return dict(_mem_store)


# ---------------------------------------------------------------------------
# Claude init
# ---------------------------------------------------------------------------
_claude = None
try:
    from anthropic import Anthropic
    if ANTHROPIC_API_KEY:
        _claude = Anthropic(api_key=ANTHROPIC_API_KEY)
        print("[previsit-agent] Claude client initialised")
except Exception as e:
    print(f"[previsit-agent] Claude init failed: {e}")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ChatMessageIn(BaseModel):
    message: str


class CreateSessionIn(BaseModel):
    patient_name: str
    patient_email: Optional[str] = ""
    appointment_date: Optional[str] = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_admin(x_admin_key: Optional[str]) -> None:
    if not ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail="ADMIN_API_KEY not configured")
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")


def _is_expired(session: Dict[str, Any]) -> bool:
    created = session.get("created_at")
    if not created:
        return False
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except Exception:
        return False
    return datetime.now(timezone.utc) - dt > timedelta(days=SESSION_TTL_DAYS)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="previsit-agent", version="0.1.0")
from consultation_routes import router as consultation_router
app.include_router(consultation_router)
from consultation_routes import init_helpers
init_helpers(_session_get, _session_update)


@app.get("/")
def root():
    return {"service": "previsit-agent", "status": "ok"}


@app.get("/healthz")
def healthz():
    return {"ok": True, "firebase": _fb_ref is not None, "claude": _claude is not None}


# ---------------------------------------------------------------------------
# Patient-facing: chat page + chat API
# ---------------------------------------------------------------------------
@app.get("/chat/{token}", response_class=HTMLResponse)
def chat_page(token: str):
    session = _session_get(token)
    if not session:
        return HTMLResponse(_render_status_page(
            "Link not found",
            "This pre-visit chat link isn't valid. Please contact the clinic."
        ), status_code=404)

    if _is_expired(session):
        return HTMLResponse(_render_status_page(
            "Link expired",
            "This pre-visit chat link has expired. Please contact the clinic for a new one."
        ), status_code=410)

    if session.get("status") == "complete":
        return HTMLResponse(_render_status_page(
            "Thank you",
            "Thanks for completing your pre-visit chat. Dr. Fouks has your summary and will see you at your appointment."
        ))

    return HTMLResponse(_render_chat_page(token, session.get("patient_name", "")))


@app.post("/api/chat/{token}")
def api_chat(token: str, body: ChatMessageIn):
    session = _session_get(token)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if _is_expired(session):
        raise HTTPException(status_code=410, detail="Session expired")
    if session.get("status") == "complete":
        raise HTTPException(status_code=409, detail="Session already complete")

    messages: List[Dict[str, Any]] = session.get("messages") or []

    user_msg = {"role": "user", "content": body.message, "timestamp": _now_iso()}
    messages.append(user_msg)

    # Build Claude-format history (role + content only)
    claude_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    if _claude is None:
        assistant_text = "(Claude API not configured — echo) " + body.message
    else:
        try:
            resp = _claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=claude_messages,
            )
            assistant_text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            ).strip()
        except Exception as e:
            print(f"[previsit-agent] Claude call failed: {e}")
            raise HTTPException(status_code=502, detail=f"Claude error: {e}")

    assistant_msg = {"role": "assistant", "content": assistant_text, "timestamp": _now_iso()}
    messages.append(assistant_msg)

    _session_update(token, {"messages": messages})

    return {"reply": assistant_text, "message_count": len(messages)}


@app.post("/api/complete/{token}")
def api_complete(token: str):
    session = _session_get(token)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if _is_expired(session):
        raise HTTPException(status_code=410, detail="Session expired")

    messages: List[Dict[str, Any]] = session.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="No conversation to summarise")

    profile: Dict[str, Any] = {}
    if _claude is None:
        profile = {"error": "Claude not configured", "raw_messages": messages}
    else:
        # Build transcript text
        transcript_lines = []
        for m in messages:
            role_label = "Patient" if m.get("role") == "user" else "Companion"
            transcript_lines.append(f"{role_label}: {m.get('content', '')}")
        transcript_text = "\n".join(transcript_lines)

        patient_name = session.get("patient_name", "Not provided")

        profile_prompt = PROFILE_GENERATION_PROMPT.format(
            patient_name=patient_name,
            session_token=token,
            transcript=transcript_text,
        )

        try:
            resp = _claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                system="You are a clinical psychologist producing a structured patient intake profile for a fertility specialist. Output ONLY valid JSON.",
                messages=[{"role": "user", "content": profile_prompt}],
            )
            raw = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            ).strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
            profile = json.loads(raw)
        except Exception as e:
            print(f"[previsit-agent] Profile extraction failed: {e}")
            profile = {"error": str(e), "raw_messages": messages}

    _session_update(token, {
        "status": "complete",
        "profile": profile,
        "completed_at": _now_iso(),
    })

    # Stub email send
    print(
        f"[previsit-agent] (stub) email -> {DOCTOR_EMAIL}\n"
        f"  patient: {session.get('patient_name')}\n"
        f"  token: {token}\n"
        f"  profile: {json.dumps(profile, indent=2)[:800]}"
    )

    return {"status": "complete", "profile": profile}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------
@app.post("/api/admin/create-session")
def admin_create_session(
    body: CreateSessionIn,
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)

    token = uuid.uuid4().hex[:8]
    session = {
        "patient_name": body.patient_name,
        "patient_email": body.patient_email or "",
        "appointment_date": body.appointment_date or "",
        "created_at": _now_iso(),
        "status": "active",
        "messages": [],
        "profile": None,
    }
    _session_set(token, session)

    return {
        "token": token,
        "link": f"{SERVICE_URL.rstrip('/')}/chat/{token}",
    }


@app.get("/api/admin/sessions")
def admin_list_sessions(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    all_sessions = _session_list()
    # Summary view only
    summary = []
    for token, s in (all_sessions or {}).items():
        if not isinstance(s, dict):
            continue
        summary.append({
            "token": token,
            "patient_name": s.get("patient_name"),
            "patient_email": s.get("patient_email"),
            "status": s.get("status"),
            "created_at": s.get("created_at"),
            "completed_at": s.get("completed_at"),
            "message_count": len(s.get("messages") or []),
        })
    summary.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"sessions": summary, "count": len(summary)}


@app.get("/api/admin/session/{token}")
def admin_get_session(
    token: str,
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    session = _session_get(token)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"token": token, **session}


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return HTMLResponse(_render_admin_page())


@app.get("/doctor/{token}", response_class=HTMLResponse)
def doctor_page(token: str, key: Optional[str] = None):
    # Require admin key via query param (?key=...)
    if not ADMIN_API_KEY or key != ADMIN_API_KEY:
        return HTMLResponse(_render_doctor_key_prompt(token), status_code=401)

    session = _session_get(token)
    if not session:
        return HTMLResponse(_render_status_page(
            "Not found",
            "No pre-visit session found for this token."
        ), status_code=404)

    if session.get("consultation_completed"):
        return HTMLResponse(_render_consultation_profile(session))
    if session.get("status") != "complete":
        return HTMLResponse(_render_doctor_pending(session))

    return HTMLResponse(_render_doctor_page(token, session))


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def _render_status_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Pre-visit chat</title>
<style>
  html,body{{margin:0;padding:0;font-family:-apple-system,system-ui,Segoe UI,sans-serif;background:#F0FDFA;color:#134E4A;}}
  .wrap{{max-width:480px;margin:15vh auto;padding:28px;background:#fff;border-radius:16px;box-shadow:0 4px 20px rgba(13,148,136,.12);text-align:center;}}
  h1{{color:#0D9488;margin:0 0 12px;font-size:22px;}}
  p{{color:#475569;line-height:1.5;}}
</style></head>
<body><div class="wrap"><h1>{title}</h1><p>{body}</p></div></body></html>"""


def _render_chat_page(token: str, patient_name: str) -> str:
    safe_name = (patient_name or "").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Pre-visit chat</title>
<style>
  *{{box-sizing:border-box;}}
  html,body{{margin:0;padding:0;height:100%;font-family:-apple-system,system-ui,Segoe UI,sans-serif;background:#F0FDFA;color:#134E4A;}}
  .app{{display:flex;flex-direction:column;height:100dvh;max-width:560px;margin:0 auto;background:#fff;}}
  header{{background:#0D9488;color:#fff;padding:14px 18px;font-weight:600;font-size:16px;box-shadow:0 2px 8px rgba(13,148,136,.25);}}
  header small{{display:block;font-weight:400;opacity:.85;font-size:12px;margin-top:2px;}}
  .log{{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;}}
  .bubble{{max-width:82%;padding:10px 14px;border-radius:18px;line-height:1.4;font-size:15px;white-space:pre-wrap;word-wrap:break-word;}}
  .bubble.assistant{{align-self:flex-start;background:#F0FDFA;color:#134E4A;border:1px solid #CCFBF1;border-bottom-left-radius:4px;}}
  .bubble.user{{align-self:flex-end;background:#0D9488;color:#fff;border-bottom-right-radius:4px;}}
  .bubble.typing{{opacity:.6;font-style:italic;}}
  form{{display:flex;gap:8px;padding:12px;border-top:1px solid #E2E8F0;background:#fff;}}
  input{{flex:1;padding:12px 14px;border-radius:24px;border:1px solid #CBD5E1;font-size:15px;outline:none;}}
  input:focus{{border-color:#0D9488;box-shadow:0 0 0 3px rgba(13,148,136,.15);}}
  button{{padding:0 18px;border-radius:24px;border:none;background:#0D9488;color:#fff;font-weight:600;font-size:15px;cursor:pointer;}}
  button:disabled{{opacity:.5;cursor:not-allowed;}}
  .done-btn{{margin:8px 12px 0;padding:10px;border-radius:10px;background:#CCFBF1;color:#0F766E;border:none;font-weight:600;cursor:pointer;}}
</style>
</head>
<body>
<div class="app">
  <header>Pre-visit chat<small>Hi {safe_name} — this 2-minute chat helps Dr. Fouks prepare for your appointment</small></header>
  <div class="log" id="log"></div>
  <button class="done-btn" id="doneBtn" type="button">I'm finished — send to Dr. Fouks</button>
  <form id="f">
    <input id="msg" placeholder="Type your message…" autocomplete="off" required>
    <button type="submit" id="send">Send</button>
  </form>
</div>
<script>
const TOKEN = {json.dumps(token)};
const log = document.getElementById('log');
const form = document.getElementById('f');
const input = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const doneBtn = document.getElementById('doneBtn');

function addBubble(role, text, cls){{
  const div = document.createElement('div');
  div.className = 'bubble ' + role + (cls ? ' ' + cls : '');
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}}

async function send(text){{
  addBubble('user', text);
  const typing = addBubble('assistant', '…', 'typing');
  sendBtn.disabled = true;
  try {{
    const r = await fetch('/api/chat/' + TOKEN, {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{message: text}})
    }});
    const data = await r.json();
    typing.remove();
    if (r.ok) {{
      addBubble('assistant', data.reply || '(no reply)');
    }} else {{
      addBubble('assistant', 'Error: ' + (data.detail || r.status));
    }}
  }} catch(e) {{
    typing.remove();
    addBubble('assistant', 'Network error. Please try again.');
  }}
  sendBtn.disabled = false;
  input.focus();
}}

form.addEventListener('submit', e => {{
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  send(text);
}});

doneBtn.addEventListener('click', async () => {{
  if (!confirm("Send your chat summary to Dr. Fouks? You won't be able to edit it after.")) return;
  doneBtn.disabled = true;
  doneBtn.textContent = 'Sending…';
  try {{
    const r = await fetch('/api/complete/' + TOKEN, {{method:'POST'}});
    if (r.ok) {{
      location.reload();
    }} else {{
      const d = await r.json();
      alert('Error: ' + (d.detail || r.status));
      doneBtn.disabled = false;
      doneBtn.textContent = "I'm finished — send to Dr. Fouks";
    }}
  }} catch(e) {{
    alert('Network error.');
    doneBtn.disabled = false;
  }}
}});

// Kick off with an empty message so the assistant greets first
send("Hi");
</script>
</body></html>"""


def _render_admin_page() -> str:
    # Plain string (not f-string) so {} in CSS/JS don't need escaping.
    return r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pre-Visit Companion — Admin</title>
<style>
  *{box-sizing:border-box;}
  html,body{margin:0;padding:0;font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;background:#F8FAFC;color:#0F172A;}
  header{background:#fff;border-bottom:1px solid #E2E8F0;padding:18px 28px;}
  header h1{margin:0;font-size:20px;color:#0D9488;}
  header .sub{font-size:13px;color:#64748B;margin-top:2px;}
  .container{max-width:1180px;margin:24px auto;padding:0 24px;display:grid;grid-template-columns:1fr;gap:20px;}
  @media(min-width:960px){.container{grid-template-columns:380px 1fr;}}
  .card{background:#fff;border:1px solid #E2E8F0;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(15,23,42,.04);}
  .card h2{margin:0 0 14px;font-size:15px;color:#0F766E;text-transform:uppercase;letter-spacing:.04em;}
  label{display:block;font-size:12px;font-weight:600;color:#475569;margin:10px 0 4px;}
  input[type=text],input[type=email],input[type=date],input[type=password]{width:100%;padding:10px 12px;border:1px solid #CBD5E1;border-radius:8px;font-size:14px;font-family:inherit;outline:none;}
  input:focus{border-color:#0D9488;box-shadow:0 0 0 3px rgba(13,148,136,.15);}
  button{padding:10px 16px;border:none;border-radius:8px;background:#0D9488;color:#fff;font-weight:600;font-size:14px;cursor:pointer;font-family:inherit;}
  button:hover:not(:disabled){background:#0F766E;}
  button:disabled{opacity:.5;cursor:not-allowed;}
  button.ghost{background:#fff;color:#0D9488;border:1px solid #0D9488;}
  button.ghost:hover:not(:disabled){background:#F0FDFA;}
  button.small{padding:6px 10px;font-size:12px;}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
  .result{margin-top:14px;padding:12px;background:#F0FDFA;border:1px solid #CCFBF1;border-radius:8px;font-size:13px;word-break:break-all;}
  .result .link{color:#0F766E;font-weight:600;}
  .result .actions{margin-top:10px;display:flex;gap:8px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{text-align:left;padding:10px 8px;border-bottom:1px solid #F1F5F9;}
  th{font-size:11px;text-transform:uppercase;color:#64748B;letter-spacing:.05em;font-weight:600;}
  tr:hover td{background:#F8FAFC;}
  .badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;}
  .badge.complete{background:#DCFCE7;color:#166534;}
  .badge.pending{background:#FEF3C7;color:#92400E;}
  .badge.expired{background:#FEE2E2;color:#991B1B;}
  .muted{color:#94A3B8;font-size:12px;}
  .empty{padding:40px 20px;text-align:center;color:#94A3B8;font-size:13px;}
  .login{max-width:360px;margin:12vh auto;padding:28px;background:#fff;border-radius:12px;border:1px solid #E2E8F0;box-shadow:0 4px 20px rgba(15,23,42,.06);}
  .login h1{margin:0 0 4px;font-size:20px;color:#0D9488;}
  .login p{margin:0 0 18px;color:#64748B;font-size:13px;}
  .err{color:#B91C1C;font-size:12px;margin-top:8px;}
  .toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;}
  .toolbar .muted{font-size:12px;}
</style>
</head>
<body>

<div id="loginView" style="display:none;">
  <div class="login">
    <h1>Pre-Visit Companion</h1>
    <p>Enter admin key to continue</p>
    <form id="loginForm">
      <input id="keyInput" type="password" placeholder="Admin key" required autofocus>
      <div style="margin-top:14px;"><button type="submit" style="width:100%;">Log in</button></div>
      <div id="loginErr" class="err"></div>
    </form>
  </div>
</div>

<div id="mainView" style="display:none;">
  <header>
    <h1>Pre-Visit Companion — Admin</h1>
    <div class="sub">Manage patient intake sessions</div>
  </header>
  <div class="container">
    <div class="card">
      <h2>Create New Patient Link</h2>
      <form id="createForm">
        <label>Patient First Name *</label>
        <input id="cfName" type="text" required>
        <label>Patient Email (optional)</label>
        <input id="cfEmail" type="email">
        <label>Appointment Date (optional)</label>
        <input id="cfDate" type="date">
        <div style="margin-top:16px;" class="row">
          <button type="submit" id="cfSubmit">Create Link</button>
          <button type="button" class="ghost" id="logoutBtn">Log out</button>
        </div>
        <div id="cfResult" class="result" style="display:none;"></div>
      </form>
    </div>

    <div class="card">
      <div class="toolbar">
        <h2 style="margin:0;">Patient Sessions</h2>
        <span class="muted" id="refreshInfo">—</span>
      </div>
      <div id="sessionsWrap">
        <div class="empty">Loading…</div>
      </div>
    </div>
  </div>
</div>

<script>
const KEY_STORAGE = 'previsit_admin_key';

const loginView = document.getElementById('loginView');
const mainView = document.getElementById('mainView');
const loginForm = document.getElementById('loginForm');
const keyInput = document.getElementById('keyInput');
const loginErr = document.getElementById('loginErr');

function getKey(){ return sessionStorage.getItem(KEY_STORAGE) || ''; }
function setKey(k){ sessionStorage.setItem(KEY_STORAGE, k); }
function clearKey(){ sessionStorage.removeItem(KEY_STORAGE); }

function showLogin(){ loginView.style.display='block'; mainView.style.display='none'; }
function showMain(){ loginView.style.display='none'; mainView.style.display='block'; loadSessions(); }

async function apiFetch(path, opts){
  opts = opts || {};
  opts.headers = Object.assign({'X-Admin-Key': getKey()}, opts.headers || {});
  const r = await fetch(path, opts);
  if (r.status === 401) {
    clearKey();
    showLogin();
    throw new Error('unauthorized');
  }
  return r;
}

loginForm.addEventListener('submit', async e => {
  e.preventDefault();
  loginErr.textContent = '';
  const k = keyInput.value.trim();
  if (!k) return;
  setKey(k);
  try {
    const r = await apiFetch('/api/admin/sessions');
    if (r.ok) { showMain(); }
    else { throw new Error('bad key'); }
  } catch(err){
    loginErr.textContent = 'Invalid admin key';
    clearKey();
  }
});

document.getElementById('logoutBtn').addEventListener('click', () => {
  clearKey();
  showLogin();
});

// Create session
document.getElementById('createForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = document.getElementById('cfSubmit');
  const resultEl = document.getElementById('cfResult');
  const payload = {
    patient_name: document.getElementById('cfName').value.trim(),
    patient_email: document.getElementById('cfEmail').value.trim(),
    appointment_date: document.getElementById('cfDate').value || ''
  };
  if (!payload.patient_name) return;
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const r = await apiFetch('/api/admin/create-session', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'error');
    resultEl.style.display = 'block';
    resultEl.innerHTML =
      '<div><strong>Link created for ' + escapeHtml(payload.patient_name) + '</strong></div>' +
      '<div class="link" id="genLink" style="margin-top:6px;">' + escapeHtml(data.link) + '</div>' +
      '<div class="actions">' +
        '<button type="button" class="small" id="copyBtn">Copy Link</button>' +
        '<button type="button" class="small ghost" disabled title="Coming soon">Send via Email (coming soon)</button>' +
      '</div>';
    document.getElementById('copyBtn').addEventListener('click', () => {
      navigator.clipboard.writeText(data.link).then(() => {
        const b = document.getElementById('copyBtn');
        b.textContent = 'Copied!';
        setTimeout(() => { b.textContent = 'Copy Link'; }, 1800);
      });
    });
    document.getElementById('createForm').reset();
    loadSessions();
  } catch(err){
    resultEl.style.display = 'block';
    resultEl.innerHTML = '<span style="color:#B91C1C;">Error: ' + escapeHtml(err.message) + '</span>';
  } finally {
    btn.disabled = false; btn.textContent = 'Create Link';
  }
});

function escapeHtml(s){
  return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function fmtDate(iso){
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'}) +
      ' ' + d.toLocaleTimeString(undefined, {hour:'2-digit', minute:'2-digit'});
  } catch(e){ return iso; }
}

function fmtApptDate(s){
  if (!s) return '—';
  try {
    const d = new Date(s);
    if (isNaN(d)) return s;
    return d.toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'});
  } catch(e){ return s; }
}

async function loadSessions(){
  const wrap = document.getElementById('sessionsWrap');
  try {
    const r = await apiFetch('/api/admin/sessions');
    if (!r.ok) throw new Error('load failed');
    const data = await r.json();
    const sessions = (data.sessions || []).slice().sort((a,b) => (b.created_at||'').localeCompare(a.created_at||''));
    if (sessions.length === 0){
      wrap.innerHTML = '<div class="empty">No sessions yet. Create one to get started.</div>';
    } else {
      let html = '<table><thead><tr>' +
        '<th>Patient</th><th>Created</th><th>Appointment</th><th>Status</th><th>Actions</th>' +
        '</tr></thead><tbody>';
      for (const s of sessions){
        const status = (s.status === 'complete')
          ? '<span class="badge complete">Complete</span>'
          : '<span class="badge pending">Pending</span>';
        const chatLink = location.origin + '/chat/' + s.token;
        const doctorLink = '/doctor/' + s.token;
        html += '<tr>' +
          '<td><strong>' + escapeHtml(s.patient_name || '—') + '</strong><div class="muted">' + escapeHtml(s.patient_email || '') + '</div></td>' +
          '<td>' + escapeHtml(fmtDate(s.created_at)) + '</td>' +
          '<td>' + escapeHtml(fmtApptDate(s.appointment_date)) + '</td>' +
          '<td>' + status + '</td>' +
          '<td class="row">' +
            '<a href="' + doctorLink + '" target="_blank"><button type="button" class="small">View Profile</button></a>' +
            '<button type="button" class="small ghost" data-copy="' + escapeHtml(chatLink) + '">Copy Chat Link</button>' +
          '</td>' +
          '</tr>';
      }
      html += '</tbody></table>';
      wrap.innerHTML = html;
      wrap.querySelectorAll('button[data-copy]').forEach(btn => {
        btn.addEventListener('click', () => {
          navigator.clipboard.writeText(btn.getAttribute('data-copy')).then(() => {
            const old = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = old; }, 1500);
          });
        });
      });
    }
    document.getElementById('refreshInfo').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch(err){
    if (err.message !== 'unauthorized'){
      wrap.innerHTML = '<div class="empty">Could not load sessions.</div>';
    }
  }
}

// Init
if (getKey()) {
  apiFetch('/api/admin/sessions').then(r => {
    if (r.ok) showMain();
    else showLogin();
  }).catch(() => showLogin());
} else {
  showLogin();
}

// Auto-refresh every 30s
setInterval(() => {
  if (mainView.style.display === 'block') loadSessions();
}, 30000);
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Doctor view rendering
# ---------------------------------------------------------------------------
def _esc(s: Any) -> str:
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


_DOCTOR_CSS = """
  *{box-sizing:border-box;}
  html,body{margin:0;padding:0;font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;background:#F8FAFC;color:#0F172A;-webkit-font-smoothing:antialiased;}
  .wrap{max-width:720px;margin:0 auto;padding:20px 18px 60px;}
  header.patient{background:#fff;border:1px solid #E2E8F0;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(15,23,42,.04);}
  header.patient h1{margin:0 0 4px;font-size:24px;color:#0F172A;font-weight:700;}
  header.patient .meta{font-size:13px;color:#64748B;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
  .badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;}
  .badge.complete{background:#DCFCE7;color:#166534;}
  .badge.pending{background:#FEF3C7;color:#92400E;}
  .badge.monitor,.badge.expressive,.badge.deliberative,.badge.partnered{background:#E0F2FE;color:#075985;}
  .badge.blunter,.badge.pragmatic,.badge.decisive,.badge.solo{background:#FCE7F3;color:#9D174D;}
  .badge.mixed,.badge.unclear{background:#F1F5F9;color:#475569;}
  section{background:#fff;border:1px solid #E2E8F0;border-radius:14px;padding:18px 20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(15,23,42,.04);}
  section h2{margin:0 0 12px;font-size:12px;color:#0F766E;text-transform:uppercase;letter-spacing:.06em;font-weight:700;}
  .dim{padding:12px 0;border-bottom:1px solid #F1F5F9;}
  .dim:last-child{border-bottom:none;}
  .dim .row{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px;}
  .dim .label{font-size:14px;font-weight:600;color:#0F172A;}
  .dim .label small{color:#94A3B8;font-weight:500;margin-left:6px;}
  .dim .evidence{font-size:12px;color:#64748B;font-style:italic;line-height:1.45;margin-top:4px;}
  .bar{position:relative;height:10px;border-radius:6px;background:#F1F5F9;overflow:hidden;margin-top:8px;}
  .bar .fill{position:absolute;top:0;left:0;bottom:0;border-radius:6px;}
  .bar.anxiety .fill{background:linear-gradient(90deg,#10B981 0%,#F59E0B 50%,#EF4444 100%);}
  .bar.agency .fill{background:linear-gradient(90deg,#94A3B8 0%,#0D9488 100%);}
  .bar .marker{position:absolute;top:-3px;width:4px;height:16px;background:#0F172A;border-radius:2px;transform:translateX(-2px);}
  .scale{display:flex;justify-content:space-between;font-size:10px;color:#94A3B8;margin-top:4px;text-transform:uppercase;letter-spacing:.04em;}
  .approach{background:#F0FDFA;border:1px solid #CCFBF1;border-left:4px solid #0D9488;border-radius:10px;padding:14px 16px;color:#134E4A;font-size:14px;line-height:1.55;}
  ul.bullets{margin:0;padding-left:20px;font-size:14px;line-height:1.6;color:#1E293B;}
  ul.bullets li{margin-bottom:4px;}
  .empty{color:#94A3B8;font-size:13px;font-style:italic;}
  details.transcript summary{cursor:pointer;font-size:13px;color:#0F766E;font-weight:600;padding:4px 0;list-style:none;}
  details.transcript summary::-webkit-details-marker{display:none;}
  details.transcript summary::before{content:'▸ ';display:inline-block;transition:transform .15s;}
  details.transcript[open] summary::before{content:'▾ ';}
  .chat{margin-top:14px;display:flex;flex-direction:column;gap:8px;}
  .bubble{max-width:85%;padding:9px 13px;border-radius:16px;font-size:13px;line-height:1.45;white-space:pre-wrap;word-wrap:break-word;}
  .bubble.user{align-self:flex-end;background:#0D9488;color:#fff;border-bottom-right-radius:4px;}
  .bubble.assistant{align-self:flex-start;background:#F0FDFA;color:#134E4A;border:1px solid #CCFBF1;border-bottom-left-radius:4px;}
  footer.tags{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:18px;padding-top:14px;border-top:1px dashed #E2E8F0;font-size:12px;color:#64748B;}
  footer.tags .tag{background:#F1F5F9;padding:4px 10px;border-radius:999px;color:#475569;font-weight:600;}
  footer.tags .ts{margin-left:auto;color:#94A3B8;}
  .keyform{max-width:360px;margin:14vh auto;padding:28px;background:#fff;border-radius:12px;border:1px solid #E2E8F0;box-shadow:0 4px 20px rgba(15,23,42,.06);text-align:center;}
  .keyform h1{margin:0 0 6px;font-size:20px;color:#0D9488;}
  .keyform p{margin:0 0 14px;color:#64748B;font-size:13px;}
  .keyform input{width:100%;padding:10px 12px;border:1px solid #CBD5E1;border-radius:8px;font-size:14px;font-family:inherit;outline:none;}
  .keyform button{margin-top:14px;width:100%;padding:10px 16px;border:none;border-radius:8px;background:#0D9488;color:#fff;font-weight:600;font-size:14px;cursor:pointer;}
  @media print {
    body{background:#fff;}
    .wrap{max-width:100%;padding:0;}
    section,header.patient{box-shadow:none;border:1px solid #CBD5E1;page-break-inside:avoid;}
    details.transcript{display:none;}
  }
"""


def _render_doctor_key_prompt(token: str) -> str:
    t = _esc(token)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize — Doctor view</title>
<style>{_DOCTOR_CSS}</style>
</head><body>
<div class="keyform">
  <h1>Doctor access</h1>
  <p>Enter your access key to view this patient profile.</p>
  <form onsubmit="event.preventDefault();var k=document.getElementById('k').value.trim();if(k)location.href='/doctor/{t}?key='+encodeURIComponent(k);">
    <input id="k" type="password" placeholder="Access key" autofocus required>
    <button type="submit">View profile</button>
  </form>
</div>
</body></html>"""


def _render_consultation_profile(session):
    name = (session.get("patient_name") or "Patient").replace("<", "&lt;").replace(">", "&gt;")
    cp = session.get("consultation_profile") or {}
    ptype = cp.get("type", "—")
    desc = cp.get("description", "")
    answers = session.get("consultation_answers") or []
    submitted = session.get("consultation_submitted_at") or ""
    questions = [
        "Confident managing health decisions",
        "Prefers doctor to guide decisions",
        "Feels worried/overwhelmed about health",
        "Researches options before appointments",
        "Questions or double-checks advice",
        "Finds it easy to follow medical plans",
        "Prefers time before committing to a plan",
    ]
    rows = ""
    for i, q in enumerate(questions):
        val = answers[i] if i < len(answers) else "—"
        rows += f"<tr><td style=\"padding:8px 12px;border-bottom:1px solid #e2e8f0;\">{q}</td><td style=\"padding:8px 12px;border-bottom:1px solid #e2e8f0;text-align:center;font-weight:600;\">{val}/5</td></tr>"
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — Consultation Profile</title>
<style>
html,body{{margin:0;padding:0;font-family:-apple-system,system-ui,sans-serif;background:#F0FDFA;color:#134E4A;}}
.wrap{{max-width:560px;margin:40px auto;padding:28px;background:#fff;border-radius:16px;box-shadow:0 4px 20px rgba(13,148,136,.12);}}
h1{{color:#0D9488;margin:0 0 4px;font-size:22px;}}
.badge{{display:inline-block;padding:4px 14px;border-radius:999px;font-size:13px;font-weight:600;background:#0D9488;color:#fff;margin:8px 0 16px;}}
.desc{{color:#475569;line-height:1.6;margin-bottom:20px;}}
table{{width:100%;border-collapse:collapse;font-size:14px;}}
th{{text-align:left;padding:8px 12px;background:#F0FDFA;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#0D9488;}}
.meta{{font-size:13px;color:#94a3b8;margin-top:16px;}}
</style></head><body><div class="wrap">
<h1>{name}</h1>
<span class="badge">{ptype}</span>
<p class="desc">{desc}</p>
<table><thead><tr><th>Question</th><th>Score</th></tr></thead><tbody>{rows}</tbody></table>
<p class="meta">Submitted: {submitted}</p>
</div></body></html>"""


def _render_doctor_pending(session: Dict[str, Any]) -> str:
    name = _esc(session.get("patient_name") or "Patient")
    status = _esc(session.get("status") or "active")
    created = _esc(session.get("created_at") or "")
    msg_count = len(session.get("messages") or [])
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — pending</title>
<style>{_DOCTOR_CSS}</style>
</head><body>
<div class="wrap">
  <header class="patient">
    <h1>{name}</h1>
    <div class="meta">
      <span class="badge pending">Pending</span>
      <span>Created: {created}</span>
    </div>
  </header>
  <section>
    <h2>Status</h2>
    <p style="margin:0;color:#475569;line-height:1.55;">
      This patient hasn't completed their pre-visit chat yet. Current status:
      <strong>{status}</strong>. Messages exchanged so far: {msg_count}.
    </p>
  </section>
</div>
</body></html>"""


def _dim_bar(label: str, score_raw: Any, evidence: str, kind: str, low: str, high: str) -> str:
    try:
        score = float(score_raw)
    except Exception:
        score = 0.0
    pct = max(0, min(100, ((score - 1) / 4.0) * 100)) if score else 0
    score_disp = f"{int(score)}/5" if score else "—"
    return f"""<div class="dim">
  <div class="row"><div class="label">{_esc(label)} <small>{_esc(score_disp)}</small></div></div>
  <div class="bar {kind}"><div class="fill" style="width:100%;"></div><div class="marker" style="left:{pct:.1f}%;"></div></div>
  <div class="scale"><span>{_esc(low)}</span><span>{_esc(high)}</span></div>
  {f'<div class="evidence">&ldquo;{_esc(evidence)}&rdquo;</div>' if evidence else ''}
</div>"""


def _dim_badge(label: str, value: str, evidence: str, options: List[str]) -> str:
    v = (value or "").strip() or "—"
    css_class = v.lower() if v.lower() in [o.lower() for o in options] + ["mixed", "unclear"] else "mixed"
    return f"""<div class="dim">
  <div class="row">
    <div class="label">{_esc(label)}</div>
    <span class="badge {css_class}">{_esc(v)}</span>
  </div>
  {f'<div class="evidence">&ldquo;{_esc(evidence)}&rdquo;</div>' if evidence else ''}
</div>"""


def _render_doctor_page(token: str, session: Dict[str, Any]) -> str:
    name = _esc(session.get("patient_name") or "Patient")
    appt = session.get("appointment_date") or ""
    completed_at = session.get("completed_at") or ""
    profile = session.get("profile") or {}
    if not isinstance(profile, dict):
        profile = {}

    dims = profile.get("dimensions") or {}
    if not isinstance(dims, dict):
        dims = {}

    def _d(key: str) -> Dict[str, Any]:
        v = dims.get(key) or {}
        return v if isinstance(v, dict) else {}

    d1 = _d("anxiety_distress")
    d2 = _d("information_style")
    d3 = _d("agency_locus")
    d4 = _d("decision_style")
    d5 = _d("emotional_processing")
    d6 = _d("support_network")

    dim_html = (
        _dim_bar("D1 — Anxiety / Distress", d1.get("score"), d1.get("evidence", ""),
                 "anxiety", "Calm (1)", "Overwhelmed (5)")
        + _dim_badge("D2 — Information Style", d2.get("type", ""), d2.get("evidence", ""),
                     ["Monitor", "Blunter"])
        + _dim_bar("D3 — Agency / Locus of Control", d3.get("score"), d3.get("evidence", ""),
                   "agency", "Passive (1)", "High agency (5)")
        + _dim_badge("D4 — Decision Style", d4.get("type", ""), d4.get("evidence", ""),
                     ["Deliberative", "Decisive"])
        + _dim_badge("D5 — Emotional Processing", d5.get("type", ""), d5.get("evidence", ""),
                     ["Expressive", "Pragmatic"])
        + _dim_badge("D6 — Support Network", d6.get("type", ""), d6.get("evidence", ""),
                     ["Solo", "Partnered"])
    )

    # Pelvic sensitivity flag
    pelvic = profile.get("pelvic_sensitivity") or {}
    if not isinstance(pelvic, dict):
        pelvic = {}
    if pelvic.get("flag"):
        pelvic_detail_html = (
            f'<div class="evidence">{_esc(pelvic.get("detail") or "")}</div>'
            if pelvic.get("detail") else ''
        )
        pelvic_implications_html = (
            f'<div style="margin-top:6px;font-size:12px;color:#991B1B;font-weight:600;">{_esc(pelvic.get("consult_implications") or "")}</div>'
            if pelvic.get("consult_implications") else ''
        )
        pelvic_html = f'''<div class="dim" style="background:#FEF2F2;border-radius:8px;padding:12px;margin-top:4px;">
  <div class="row">
    <div class="label" style="color:#991B1B;">⚠ Pelvic Sensitivity</div>
    <span class="badge" style="background:#FEE2E2;color:#991B1B;">FLAGGED</span>
  </div>
  {pelvic_detail_html}
  {pelvic_implications_html}
</div>'''
    else:
        pelvic_html = ''

    # Clinical flags
    cflags = profile.get("clinical_flags") or {}
    if not isinstance(cflags, dict):
        cflags = {}
    cflag_items = []
    if cflags.get("pattern"):
        cflag_items.append(f'<span class="tag" style="background:#FEF3C7;color:#92400E;">{_esc(cflags["pattern"])}</span>')
    if cflags.get("duration_trying"):
        cflag_items.append(f'<span class="tag">Trying: {_esc(cflags["duration_trying"])}</span>')
    if cflags.get("surgery_pending"):
        cflag_items.append(f'<span class="tag" style="background:#FEE2E2;color:#991B1B;">Surgery: {_esc(cflags["surgery_pending"])}</span>')
    for med in (cflags.get("medications") or []):
        cflag_items.append(f'<span class="tag">Rx: {_esc(med)}</span>')
    for dx in (cflags.get("known_diagnoses") or []):
        cflag_items.append(f'<span class="tag">Dx: {_esc(dx)}</span>')
    if cflags.get("previous_treatments"):
        cflag_items.append(f'<span class="tag">{_esc(cflags["previous_treatments"])}</span>')
    clinical_flags_html = ''.join(cflag_items)

    # Engagement level
    engagement = _esc(profile.get("engagement_level") or "—")

    approach = profile.get("suggested_consult_approach") or ""
    approach_html = (
        f'<div class="approach">{_esc(approach)}</div>'
        if approach else '<div class="empty">No approach recommendation available.</div>'
    )

    concerns = profile.get("key_concerns") or []
    if isinstance(concerns, list) and concerns:
        concerns_html = '<ul class="bullets">' + "".join(
            f"<li>{_esc(c)}</li>" for c in concerns
        ) + "</ul>"
    else:
        concerns_html = '<div class="empty">None recorded.</div>'

    flagged = profile.get("medical_questions_flagged") or []
    if isinstance(flagged, list) and flagged:
        flagged_html = '<ul class="bullets">' + "".join(
            f"<li>{_esc(f)}</li>" for f in flagged
        ) + "</ul>"
    else:
        flagged_html = '<div class="empty">None flagged.</div>'

    messages = session.get("messages") or []
    bubbles = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "assistant")
        content = _esc(m.get("content", ""))
        cls = "user" if role == "user" else "assistant"
        bubbles.append(f'<div class="bubble {cls}">{content}</div>')
    transcript_html = (
        '<div class="chat">' + "".join(bubbles) + "</div>"
        if bubbles else '<div class="empty">No messages.</div>'
    )

    exp_level = _esc(profile.get("experience_level") or "—")
    comm_pref = _esc(profile.get("communication_preference") or "—")

    appt_html = f'<span>Appointment: {_esc(appt)}</span>' if appt else ''
    completed_html = f'<span class="ts">Completed: {_esc(completed_at)}</span>' if completed_at else ''

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — Pre-visit profile</title>
<style>{_DOCTOR_CSS}</style>
</head><body>
<div class="wrap">
  <header class="patient">
    <h1>{name}</h1>
    <div class="meta">
      <span class="badge complete">Complete</span>
      {appt_html}
    </div>
  </header>

  <section>
    <h2>Patient Profile</h2>
    {dim_html}
    {pelvic_html}
  </section>

  <section>
    <h2>Suggested Consult Approach</h2>
    {approach_html}
  </section>

  <section>
    <h2>Key Concerns</h2>
    {concerns_html}
  </section>

  <section>
    <h2>Medical Questions Flagged</h2>
    {flagged_html}
  </section>

  <section>
    <h2>Clinical Flags</h2>
    <div style="display:flex;flex-wrap:wrap;gap:8px;">
      {clinical_flags_html if clinical_flags_html else '<div class="empty">None flagged.</div>'}
    </div>
  </section>

  <section>
    <details class="transcript">
      <summary>Conversation Transcript ({len(messages)} messages)</summary>
      {transcript_html}
    </details>
  </section>

  <footer class="tags">
    <span class="tag">Experience: {exp_level}</span>
    <span class="tag">Prefers: {comm_pref}</span>
    <span class="tag">Engagement: {engagement}</span>
    {completed_html}
  </footer>
</div>
</body></html>"""
