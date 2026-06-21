"""
postvisit_routes.py — Post-visit review pathway for previsit-agent.

A self-contained module, parallel to consultation_routes.py. Sent to every
patient after a visit or at completion of care. One screen: a single 1–5
experience rating + optional free text. Optionally appends a Google review
prompt on the thank-you screen (toggled per-link by the admin).

Data lives under a dedicated Firebase subtree: previsit/reviews/{token}
(kept separate from previsit/sessions so it never tangles with the
pre-visit personality profiling).

Wiring in app.py (3 lines, after the consultation router is included):

    from postvisit_routes import router as postvisit_router, init_config as _pv_init
    app.include_router(postvisit_router)
    _pv_init(SERVICE_URL, ADMIN_API_KEY, os.getenv("GOOGLE_REVIEW_URL", ""))

Endpoints:
    GET  /review/{token}            patient-facing rating page
    POST /api/review/{token}        store rating + comment
    POST /api/admin/create-review   admin: mint a link (X-Admin-Key)
    GET  /api/admin/reviews         admin: list all reviews (X-Admin-Key)
    GET  /reviews                   admin dashboard (create links + view results)
"""

import os
import uuid
import pathlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter()

# ---------------------------------------------------------------------------
# Config — injected from app.py via init_config()
# ---------------------------------------------------------------------------
SERVICE_URL = "http://localhost:8080"
ADMIN_API_KEY = ""
GOOGLE_REVIEW_URL = ""  # default clinic Google review link (per-link override allowed)

# Email (Resend) — read from env in init_config(). Email is OPTIONAL: if these
# aren't set, link creation still works and the dashboard reports "not sent".
# Reuses the SAME provider/credentials as the fouks-intake service.
RESEND_API_KEY = ""
EMAIL_FROM = ""               # must be a Resend-verified sender address
EMAIL_FROM_NAME = "Dr Yuval Fouks"

# ---------------------------------------------------------------------------
# Storage — own Firebase subtree, graceful in-memory fallback
# (mirrors app.py's session store but under previsit/reviews)
# ---------------------------------------------------------------------------
_rv_ref = None
_rv_mem: Dict[str, Dict[str, Any]] = {}


def init_config(service_url: str, admin_key: str, google_url: str = "") -> None:
    global SERVICE_URL, ADMIN_API_KEY, GOOGLE_REVIEW_URL, _rv_ref
    global RESEND_API_KEY, EMAIL_FROM, EMAIL_FROM_NAME
    SERVICE_URL = service_url or SERVICE_URL
    ADMIN_API_KEY = admin_key or ""
    GOOGLE_REVIEW_URL = google_url or ""
    # Email config pulled straight from env (same var names as fouks-intake).
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    EMAIL_FROM = (os.getenv("EMAIL_FROM_ADDRESS", "")
                  or os.getenv("EMAIL_FROM", "")
                  or os.getenv("DOCTOR_EMAIL", ""))
    EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Dr Yuval Fouks")
    print(f"[postvisit] email send {'ENABLED' if (RESEND_API_KEY and EMAIL_FROM) else 'disabled'}"
          f" (from={EMAIL_FROM or '—'})")
    try:
        # firebase_admin is already initialised by app.py at import time.
        from firebase_admin import db as fb_db
        _rv_ref = fb_db.reference("previsit/reviews")
        print("[postvisit] Firebase subtree previsit/reviews ready")
    except Exception as e:
        print(f"[postvisit] Firebase unavailable, using in-memory store: {e}")
        _rv_ref = None


def _rv_get(token: str) -> Optional[Dict[str, Any]]:
    if _rv_ref is not None:
        try:
            data = _rv_ref.child(token).get()
            return data if isinstance(data, dict) else None
        except Exception as e:
            print(f"[postvisit] read failed: {e}")
    return _rv_mem.get(token)


def _rv_set(token: str, data: Dict[str, Any]) -> None:
    if _rv_ref is not None:
        try:
            _rv_ref.child(token).set(data)
            return
        except Exception as e:
            print(f"[postvisit] write failed: {e}")
    _rv_mem[token] = data


def _rv_update(token: str, patch: Dict[str, Any]) -> None:
    if _rv_ref is not None:
        try:
            _rv_ref.child(token).update(patch)
            return
        except Exception as e:
            print(f"[postvisit] update failed: {e}")
    if token in _rv_mem:
        _rv_mem[token].update(patch)


def _rv_list() -> Dict[str, Dict[str, Any]]:
    if _rv_ref is not None:
        try:
            data = _rv_ref.get()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[postvisit] list failed: {e}")
    return dict(_rv_mem)


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


def _esc_js(s: str) -> str:
    """Escape a value for safe injection into a JS double-quoted string literal."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")


def _email_html(greeting: str, link: str) -> str:
    """Simple, mobile-friendly HTML email. Inline styles only (email clients ignore <style>)."""
    safe_link = link.replace('"', "%22")
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:480px;margin:0 auto;padding:8px 4px;color:#1f2d3d;line-height:1.6;font-size:15px;">'
        f'<p style="margin:0 0 14px;">{greeting}</p>'
        '<p style="margin:0 0 18px;">Thank you for coming in. We\'d really value hearing how your '
        'experience was — it takes about 20 seconds, and it helps us look after you better.</p>'
        '<p style="text-align:center;margin:26px 0;">'
        f'<a href="{safe_link}" style="background:#0D9488;color:#ffffff;text-decoration:none;'
        'padding:13px 26px;border-radius:999px;font-weight:600;display:inline-block;">'
        'Share your feedback</a></p>'
        '<p style="margin:0 0 4px;font-size:13px;color:#64748b;">If the button doesn\'t work, paste this link into your browser:</p>'
        f'<p style="margin:0 0 22px;font-size:13px;word-break:break-all;"><a href="{safe_link}" style="color:#0D9488;">{link}</a></p>'
        '<p style="margin:0;color:#334155;">With thanks,<br>Dr Yuval Fouks</p>'
        '</div>'
    )


def _send_review_email(to_email: str, link: str, patient_name: str = ""):
    """Send the review link to a patient via Resend. Returns (ok: bool, error: str).

    Uses the same Resend account/sender as the fouks-intake service. Never raises —
    email is best-effort and must not block link creation.
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return False, "no recipient email"
    if not RESEND_API_KEY:
        return False, "email not configured (RESEND_API_KEY unset)"
    if not EMAIL_FROM or "@" not in EMAIL_FROM:
        return False, "sender not configured (set EMAIL_FROM_ADDRESS to a verified address)"

    name = (patient_name or "").strip()
    greeting = f"Hi {name}," if name else "Hi,"
    from_field = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>" if EMAIL_FROM_NAME else EMAIL_FROM
    text_body = (
        f"{greeting}\n\n"
        "Thank you for coming in. We'd really value hearing how your experience was "
        "— it takes about 20 seconds:\n\n"
        f"{link}\n\n"
        "With thanks,\nDr Yuval Fouks"
    )
    payload = {
        "from": from_field,
        "to": [to_email],
        "subject": "How was your visit?",
        "html": _email_html(greeting, link),
        "text": text_body,
    }

    import json as _json
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=_json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "previsit-agent/1.0 (+https://previsit-agent.run.app)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            if 200 <= code < 300:
                return True, ""
            return False, f"Resend returned {code}"
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            detail = ""
        return False, f"Resend {e.code}: {detail}".strip()
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Patient-facing
# ---------------------------------------------------------------------------
@router.get("/review/{token}", response_class=HTMLResponse)
async def serve_review(token: str):
    record = _rv_get(token)
    if not record:
        return HTMLResponse(
            "<h2 style='text-align:center;margin-top:40vh;font-family:sans-serif;color:#555'>"
            "This link isn't valid. Please contact the clinic.</h2>",
            status_code=404,
        )
    if record.get("status") == "complete":
        return HTMLResponse(
            "<h2 style='text-align:center;margin-top:40vh;font-family:sans-serif;color:#555'>"
            "Thank you — your feedback has already been received.</h2>"
        )

    google_enabled = bool(record.get("google_review"))
    google_url = record.get("google_url") or GOOGLE_REVIEW_URL or ""
    # Only surface the Google button when enabled AND a URL exists.
    show_google = google_enabled and bool(google_url)

    html = pathlib.Path("static/postvisit.html").read_text()
    html = (
        html.replace("{{TOKEN}}", _esc_js(token))
        .replace("{{GOOGLE_ENABLED}}", "true" if show_google else "false")
        .replace("{{GOOGLE_URL}}", _esc_js(google_url))
    )
    return HTMLResponse(html)


class ReviewSubmission(BaseModel):
    rating: int
    comment: Optional[str] = ""


@router.post("/api/review/{token}")
async def submit_review(token: str, body: ReviewSubmission):
    record = _rv_get(token)
    if not record:
        raise HTTPException(404, "Link not found")
    if record.get("status") == "complete":
        raise HTTPException(400, "Already submitted")
    if not (1 <= body.rating <= 5):
        raise HTTPException(422, "Rating must be 1-5")

    comment = (body.comment or "").strip()[:2000]
    _rv_update(token, {
        "status": "complete",
        "rating": body.rating,
        "comment": comment,
        "completed_at": _now_iso(),
    })

    google_url = record.get("google_url") or GOOGLE_REVIEW_URL or ""
    show_google = bool(record.get("google_review")) and bool(google_url)
    # NB: shown to every respondent (no sentiment gating) — keeps it within
    # Google's review policy. If you ever want to gate, do it here, but be aware
    # selectively soliciting only happy patients violates Google's guidelines.
    return {"ok": True, "show_google": show_google, "google_url": google_url if show_google else ""}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class CreateReviewIn(BaseModel):
    patient_name: Optional[str] = ""
    patient_email: Optional[str] = ""
    google_review: bool = False
    google_url: Optional[str] = ""   # optional override of the default clinic URL
    send_email: bool = False         # if True + patient_email set, email the link now


@router.post("/api/admin/create-review")
def admin_create_review(
    body: CreateReviewIn,
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    token = uuid.uuid4().hex[:8]
    record = {
        "patient_name": (body.patient_name or "").strip(),
        "patient_email": (body.patient_email or "").strip(),
        "google_review": bool(body.google_review),
        "google_url": (body.google_url or "").strip(),  # blank -> falls back to GOOGLE_REVIEW_URL
        "created_at": _now_iso(),
        "status": "pending",
        "rating": None,
        "comment": "",
    }
    _rv_set(token, record)
    link = f"{SERVICE_URL.rstrip('/')}/review/{token}"

    email_sent = False
    email_error = ""
    if body.send_email:
        email_sent, email_error = _send_review_email(
            record["patient_email"], link, record["patient_name"]
        )
        if email_sent:
            _rv_update(token, {"emailed_at": _now_iso()})

    return {
        "token": token,
        "link": link,
        "google_review": record["google_review"],
        "email_sent": email_sent,
        "email_error": email_error,
    }


@router.get("/api/admin/reviews")
def admin_list_reviews(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    rows: List[Dict[str, Any]] = []
    for token, r in (_rv_list() or {}).items():
        if not isinstance(r, dict):
            continue
        rows.append({
            "token": token,
            "patient_name": r.get("patient_name") or "",
            "google_review": bool(r.get("google_review")),
            "status": r.get("status"),
            "rating": r.get("rating"),
            "comment": r.get("comment") or "",
            "created_at": r.get("created_at"),
            "completed_at": r.get("completed_at"),
        })
    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    completed = [x for x in rows if x["rating"]]
    avg = round(sum(x["rating"] for x in completed) / len(completed), 2) if completed else None
    return {"reviews": rows, "count": len(rows), "responses": len(completed), "average": avg}


@router.get("/reviews", response_class=HTMLResponse)
def reviews_dashboard():
    return HTMLResponse(pathlib.Path("static/reviews.html").read_text())




@router.get("/send", response_class=HTMLResponse)
def send_page():
    import os
    from fastapi.responses import HTMLResponse
    path = os.path.join(os.path.dirname(__file__), "static", "send.html")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())
