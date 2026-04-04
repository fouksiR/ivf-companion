"""
Fertool RAG Bridge — Queries the Fertool backend for clinical evidence.
========================================================================
Simple HTTP bridge to the Fertool Cloud Run service. Returns formatted
evidence text for injection into the Claude system prompt.

Usage:
    from fertool_bridge import query_fertool
    evidence = query_fertool("What is AMH and what does it mean?")
"""

import os
import logging

import requests

logger = logging.getLogger(__name__)

FERTOOL_URL = os.getenv(
    "FERTOOL_URL",
    "https://fertility-gp-backend-532857641879.australia-southeast2.run.app",
)


def query_fertool(question: str, top_k: int = 5, timeout: float = 6.0) -> str:
    """Query Fertool RAG backend and return formatted evidence string.

    Returns empty string on any failure (network, timeout, bad response).
    This is intentionally fire-and-forget — evidence enrichment should never
    block or break the chat flow.
    """
    if not question or not question.strip():
        return ""

    try:
        resp = requests.post(
            f"{FERTOOL_URL}/query",
            json={"question": question, "top_k": top_k},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        statements = data.get("guidelines", {}).get("guideline_statements", [])
        if not statements:
            return ""

        perspective = data.get("specialist_insight", {}).get("specialist_perspective", "")
        pitfalls = data.get("specialist_insight", {}).get("common_pitfalls", [])

        parts = ["CLINICAL EVIDENCE (rephrase in warm patient-friendly language):"]
        for i, s in enumerate(statements[:5], 1):
            parts.append(f"  {i}. {s}")

        if perspective:
            parts.append(f"\nSPECIALIST NOTE: {perspective}")

        if pitfalls:
            parts.append(f"\nCOMMON MISCONCEPTIONS: {'; '.join(pitfalls[:3])}")

        return "\n".join(parts)

    except requests.Timeout:
        logger.warning("Fertool bridge: request timed out after %.1fs", timeout)
        return ""
    except requests.RequestException as e:
        logger.warning("Fertool bridge: %s", e)
        return ""
    except Exception as e:
        logger.warning("Fertool bridge unexpected error: %s", e)
        return ""
