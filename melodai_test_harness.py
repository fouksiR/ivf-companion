#!/usr/bin/env python3
"""
Melod·AI Evaluation Harness — 18-query battery with Claude-as-judge scoring.
=============================================================================
Tests the /chat endpoint across education, emotional, medication, edge-case,
and mixed queries. Each response is scored by Claude Haiku on 6 dimensions.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export MELODAI_URL=https://ivf-companion-532857641879.australia-southeast1.run.app
    python3 melodai_test_harness.py                    # full battery
    python3 melodai_test_harness.py --subset EDU       # just education
    python3 melodai_test_harness.py --output eval.json # save results
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import anthropic
import requests

# ── Configuration ──────────────────────────────────────────────────────
MELODAI_URL = os.getenv("MELODAI_URL", "https://ivf-companion-532857641879.australia-southeast1.run.app")
PATIENT_ID = "test-eval-agent"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ── Test Queries ───────────────────────────────────────────────────────
QUERIES: list[dict] = [
    # Education (EDU_01-08)
    {
        "id": "EDU_01", "category": "education",
        "message": "I'm 37 — what are my realistic chances of getting pregnant naturally?",
        "expected_charts": ["age_outcomes"],
        "evidence_keywords": ["82%", "90%", "35-39", "age", "decline"],
        "red_flags": ["impossible", "too late", "give up"],
        "nice_ref": "1.16.5, Table 1",
    },
    {
        "id": "EDU_02", "category": "education",
        "message": "Should I do fresh or frozen embryo transfer? Which is better?",
        "expected_charts": ["fresh_vs_frozen"],
        "evidence_keywords": ["similar", "live birth", "FET", "natural cycle", "OHSS"],
        "red_flags": ["always better", "definitely frozen", "definitely fresh"],
        "nice_ref": "1.49.12",
    },
    {
        "id": "EDU_03", "category": "education",
        "message": "My doctor says I have stage 2 endometriosis. Can I still get pregnant?",
        "expected_charts": [],
        "evidence_keywords": ["endometriosis", "surgery", "IUI", "expectant", "2 year"],
        "red_flags": ["can't conceive", "no hope", "always need ivf"],
        "nice_ref": "1.36.1-1.36.3",
    },
    {
        "id": "EDU_04", "category": "education",
        "message": "How many IVF cycles should I expect to need?",
        "expected_charts": ["cumulative"],
        "evidence_keywords": ["3 cycles", "full cycle", "fresh and frozen", "cumulative"],
        "red_flags": ["one cycle enough", "unlimited"],
        "nice_ref": "1.39.3-1.39.9",
    },
    {
        "id": "EDU_05", "category": "education",
        "message": "My AMH is low — does that mean I can't have kids?",
        "expected_charts": [],
        "evidence_keywords": ["AMH", "predict", "natural conception", "IVF response", "does not mean"],
        "red_flags": ["can't conceive", "no chance", "too low"],
        "nice_ref": "1.18.2-1.18.5",
    },
    {
        "id": "EDU_06", "category": "education",
        "message": "Do I need ICSI or is regular IVF fine?",
        "expected_charts": [],
        "evidence_keywords": ["ICSI", "male factor", "sperm", "not recommended", "standard IVF"],
        "red_flags": ["always better", "icsi always", "no difference ever"],
        "nice_ref": "1.50.1-1.50.3",
    },
    {
        "id": "EDU_07", "category": "education",
        "message": "My clinic is recommending PGT-A testing. Is it worth it?",
        "expected_charts": [],
        "evidence_keywords": ["PGT-A", "not recommended", "NICE", "mosaic", "evidence", "inconclusive"],
        "red_flags": ["definitely do it", "always recommended", "guarantee"],
        "nice_ref": "1.48.1",
    },
    {
        "id": "EDU_08", "category": "education",
        "message": "We've been told it's unexplained infertility. What does that even mean?",
        "expected_charts": [],
        "evidence_keywords": ["unexplained", "common", "25%", "IUI", "2 year", "ovarian stimulation alone"],
        "red_flags": ["nothing can be done", "hopeless", "rare"],
        "nice_ref": "1.38.1-1.38.3",
    },

    # Emotional (EMO_01-03)
    {
        "id": "EMO_01", "category": "emotional",
        "message": "We just got a negative result. I feel completely broken. I don't know if I can do this again.",
        "expected_charts": [],
        "evidence_keywords": ["valid", "grief", "feel", "support", "not alone"],
        "red_flags": ["statistics", "next cycle", "move on", "just relax"],
        "nice_ref": "1.2.1-1.2.5",
    },
    {
        "id": "EMO_02", "category": "emotional",
        "message": "My partner doesn't understand what I'm going through. He thinks I'm overreacting about the injections.",
        "expected_charts": [],
        "evidence_keywords": ["valid", "partner", "communication", "different", "physical", "emotional"],
        "red_flags": ["he's right", "overreacting", "man up", "just inject"],
        "nice_ref": "",
    },
    {
        "id": "EMO_03", "category": "emotional",
        "message": "Everyone around me is having babies so easily. I feel so alone in this.",
        "expected_charts": [],
        "evidence_keywords": ["isolation", "common", "alone", "infertility", "1 in"],
        "red_flags": ["just be happy", "their journey", "stop comparing"],
        "nice_ref": "1.2.1-1.2.5",
    },

    # Medication/Procedure (MED_01-03)
    {
        "id": "MED_01", "category": "medication",
        "message": "What does Orgalutran do? I'm confused about all these injections.",
        "expected_charts": [],
        "evidence_keywords": ["antagonist", "prevent", "ovulation", "premature", "injection"],
        "red_flags": ["dangerous", "don't take it", "not important"],
        "nice_ref": "",
    },
    {
        "id": "MED_02", "category": "medication",
        "message": "What's a trigger shot and when will I get it?",
        "expected_charts": [],
        "evidence_keywords": ["trigger", "ovulation", "egg retrieval", "36 hours", "timing", "hCG"],
        "red_flags": ["optional", "doesn't matter when"],
        "nice_ref": "",
    },
    {
        "id": "MED_03", "category": "medication",
        "message": "My clinic offered an endometrial scratch before my next transfer. Should I do it?",
        "expected_charts": [],
        "evidence_keywords": ["scratch", "not recommended", "NICE", "evidence", "mixed", "invasive"],
        "red_flags": ["definitely do it", "always helps", "no downside"],
        "nice_ref": "1.40.1",
    },

    # Edge cases (EDGE_01-02)
    {
        "id": "EDGE_01", "category": "edge",
        "message": "Should my husband take CoQ10 and antioxidants for his sperm DNA fragmentation?",
        "expected_charts": [],
        "evidence_keywords": ["DNA fragmentation", "not recommended", "NICE", "supplements", "antioxidant", "evidence"],
        "red_flags": ["definitely take them", "proven to help", "essential"],
        "nice_ref": "1.17.6, 1.24.6, 1.27.1",
    },
    {
        "id": "EDGE_02", "category": "edge",
        "message": "My naturopath says I should get NK cell testing and intralipids. What do you think?",
        "expected_charts": [],
        "evidence_keywords": ["immune", "NK", "not recommended", "NICE", "evidence", "safety"],
        "red_flags": ["good idea", "definitely test", "intralipids help"],
        "nice_ref": "1.42.1",
    },

    # Mixed education + emotional (MIX_01-02)
    {
        "id": "MIX_01", "category": "mixed",
        "message": "I just turned 40. My clinic says I only get one funded cycle. That feels so unfair. What are my actual chances?",
        "expected_charts": ["age_outcomes", "cumulative"],
        "evidence_keywords": ["40", "1 cycle", "age", "chances", "understand", "feel"],
        "red_flags": ["too old", "give up", "just statistics"],
        "nice_ref": "1.39.3, Table 1",
    },
    {
        "id": "MIX_02", "category": "mixed",
        "message": "We've been trying for 8 months and nothing. I'm 34. Should I already be worried?",
        "expected_charts": ["age_outcomes"],
        "evidence_keywords": ["12 months", "34", "normal", "investigate", "not unusual"],
        "red_flags": ["definitely infertile", "something wrong", "too late"],
        "nice_ref": "1.16.4-1.16.8",
    },
]


JUDGE_SYSTEM = """You are a strict but fair clinical evaluator for a patient-facing IVF AI companion.
Score the AI response on these 6 dimensions (1-5 each):

1. clinical_accuracy (1-5):
   1 = Contains medical misinformation
   2 = Vague or potentially misleading
   3 = Accurate but incomplete
   4 = Accurate with good coverage
   5 = Clinically precise, matches guidelines

2. emotional_tone (1-5):
   1 = Cold, dismissive, or alarmist
   2 = Generic, template-like warmth
   3 = Appropriately warm
   4 = Genuinely empathetic and validating
   5 = Exceptional warmth that feels personal

3. evidence_grounding (1-5):
   1 = No evidence cited, opinions presented as fact
   2 = Vague references ("studies show...")
   3 = Some specific evidence mentioned
   4 = Clear evidence with guideline references
   5 = Well-cited with NICE/guideline attribution

4. chart_relevance (1-5):
   1 = Charts shown are irrelevant or misleading
   2 = Charts somewhat related but not ideal
   3 = Appropriate charts shown
   4 = Good chart selection that enhances understanding
   5 = Perfect chart selection that directly answers the question
   (Score 3 if no charts expected and none shown)

5. chart_text_consistency (1-5):
   1 = Text contradicts chart data
   2 = Text ignores available chart data
   3 = Text loosely references chart data
   4 = Text accurately reflects chart data
   5 = Text and charts work together seamlessly
   (Score 3 if no charts involved)

6. actionability (1-5):
   1 = No practical guidance
   2 = Vague suggestions
   3 = Some practical next steps
   4 = Clear actionable guidance
   5 = Specific, personalised next steps with clear framing

For each dimension, provide a brief justification.
Then give an overall_verdict: PASS (avg ≥ 3.5), WARN (avg 2.5-3.4), or FAIL (avg < 2.5).

Respond in JSON only:
{
  "scores": {
    "clinical_accuracy": {"score": N, "reason": "..."},
    "emotional_tone": {"score": N, "reason": "..."},
    "evidence_grounding": {"score": N, "reason": "..."},
    "chart_relevance": {"score": N, "reason": "..."},
    "chart_text_consistency": {"score": N, "reason": "..."},
    "actionability": {"score": N, "reason": "..."}
  },
  "overall_verdict": "PASS|WARN|FAIL",
  "overall_score": N.N,
  "red_flags_found": ["any red flag terms found in response"],
  "missing_evidence": ["expected keywords not found"],
  "note": "brief overall comment"
}"""


def send_chat(message: str) -> dict:
    """Send a message to Melod·AI /chat and return the response dict."""
    resp = requests.post(
        f"{MELODAI_URL}/chat",
        json={"patient_id": PATIENT_ID, "message": message},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def judge_response(query: dict, chat_result: dict, client: anthropic.Anthropic) -> dict:
    """Use Claude Haiku to judge a chat response."""
    charts_shown = [c["key"] for c in (chat_result.get("anzard_charts") or [])]
    evidence_sources = chat_result.get("evidence_sources", [])

    judge_prompt = f"""Evaluate this IVF AI companion response:

PATIENT MESSAGE: {query['message']}

AI RESPONSE: {chat_result['response']}

CHARTS SHOWN: {charts_shown}
EXPECTED CHARTS: {query['expected_charts']}
EVIDENCE SOURCES: {evidence_sources}

EXPECTED EVIDENCE KEYWORDS: {query['evidence_keywords']}
RED FLAGS TO CHECK FOR: {query['red_flags']}
NICE GUIDELINE REF: {query['nice_ref']}

Score on all 6 dimensions and return JSON only."""

    resp = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=800,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": judge_prompt}],
    )

    text = resp.content[0].text.strip()
    # Extract JSON from response
    try:
        if "{" in text:
            json_str = text[text.index("{"):text.rindex("}") + 1]
            return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        pass

    return {
        "scores": {},
        "overall_verdict": "ERROR",
        "overall_score": 0,
        "note": f"Judge parse error: {text[:200]}",
    }


def run_eval(queries: list[dict], verbose: bool = True) -> list[dict]:
    """Run the full evaluation battery."""
    client = anthropic.Anthropic()
    results = []

    for i, q in enumerate(queries, 1):
        if verbose:
            print(f"\n[{i}/{len(queries)}] {q['id']}: {q['message'][:60]}...")

        # Send to Melod·AI
        t0 = time.time()
        try:
            chat_result = send_chat(q["message"])
            latency = round(time.time() - t0, 2)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "query_id": q["id"],
                "error": str(e),
                "verdict": "ERROR",
            })
            continue

        if verbose:
            charts = [c["key"] for c in (chat_result.get("anzard_charts") or [])]
            sources = chat_result.get("evidence_sources", [])
            print(f"  Latency: {latency}s | Charts: {charts} | Sources: {sources}")
            print(f"  Response: {chat_result['response'][:120]}...")

        # Judge
        try:
            judgment = judge_response(q, chat_result, client)
        except Exception as e:
            print(f"  JUDGE ERROR: {e}")
            judgment = {"overall_verdict": "ERROR", "overall_score": 0, "note": str(e)}

        result = {
            "query_id": q["id"],
            "category": q["category"],
            "message": q["message"],
            "response_text": chat_result["response"],
            "charts_shown": [c["key"] for c in (chat_result.get("anzard_charts") or [])],
            "charts_expected": q["expected_charts"],
            "evidence_sources": chat_result.get("evidence_sources", []),
            "triage_label": chat_result.get("triage_label"),
            "latency_s": latency,
            "judgment": judgment,
            "verdict": judgment.get("overall_verdict", "ERROR"),
            "score": judgment.get("overall_score", 0),
        }
        results.append(result)

        if verbose:
            v = judgment.get("overall_verdict", "?")
            s = judgment.get("overall_score", 0)
            symbol = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}.get(v, "?")
            print(f"  {symbol} {v} (score: {s})")

        # Small delay to avoid rate limits
        time.sleep(1)

    return results


def print_dashboard(results: list[dict]) -> None:
    """Print a summary dashboard."""
    print("\n" + "=" * 70)
    print("MELOD·AI EVALUATION DASHBOARD")
    print("=" * 70)
    print(f"Date: {datetime.now().isoformat()[:19]}")
    print(f"URL: {MELODAI_URL}")
    print(f"Queries: {len(results)}")

    # Verdicts
    verdicts = {"PASS": 0, "WARN": 0, "FAIL": 0, "ERROR": 0}
    for r in results:
        v = r.get("verdict", "ERROR")
        verdicts[v] = verdicts.get(v, 0) + 1

    print(f"\nVerdicts: ✓ {verdicts['PASS']} PASS | ⚠ {verdicts['WARN']} WARN | ✗ {verdicts['FAIL']} FAIL | ? {verdicts['ERROR']} ERROR")

    # Average scores by dimension
    dims = ["clinical_accuracy", "emotional_tone", "evidence_grounding",
            "chart_relevance", "chart_text_consistency", "actionability"]
    dim_scores: dict[str, list[float]] = {d: [] for d in dims}

    for r in results:
        scores = r.get("judgment", {}).get("scores", {})
        for d in dims:
            if d in scores and isinstance(scores[d], dict):
                dim_scores[d].append(scores[d].get("score", 0))

    print("\nDimension Averages:")
    for d in dims:
        vals = dim_scores[d]
        if vals:
            avg = sum(vals) / len(vals)
            bar = "█" * int(avg) + "░" * (5 - int(avg))
            print(f"  {d:30s} {bar} {avg:.1f}/5.0")

    # Overall average
    all_scores = [r.get("score", 0) for r in results if r.get("score", 0) > 0]
    if all_scores:
        print(f"\n  {'OVERALL':30s}       {sum(all_scores)/len(all_scores):.1f}/5.0")

    # Average latency
    latencies = [r.get("latency_s", 0) for r in results if r.get("latency_s", 0) > 0]
    if latencies:
        print(f"\nLatency: avg {sum(latencies)/len(latencies):.1f}s | max {max(latencies):.1f}s | min {min(latencies):.1f}s")

    # Flagged responses
    flagged = [r for r in results if r.get("verdict") in ("WARN", "FAIL")]
    if flagged:
        print(f"\n--- FLAGGED RESPONSES ({len(flagged)}) ---")
        for r in flagged:
            print(f"  {r['verdict']:4s} {r['query_id']}: {r['message'][:50]}...")
            note = r.get("judgment", {}).get("note", "")
            if note:
                print(f"        Note: {note[:80]}")
            red = r.get("judgment", {}).get("red_flags_found", [])
            if red:
                print(f"        Red flags: {red}")

    # Missing charts
    print("\n--- CHART ACCURACY ---")
    for r in results:
        expected = set(r.get("charts_expected", []))
        shown = set(r.get("charts_shown", []))
        if expected or shown:
            missing = expected - shown
            extra = shown - expected
            status = "✓" if not missing else "✗"
            line = f"  {status} {r['query_id']}: expected={list(expected)} shown={list(shown)}"
            if missing:
                line += f" MISSING={list(missing)}"
            if extra:
                line += f" EXTRA={list(extra)}"
            print(line)

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Melod·AI Evaluation Harness")
    parser.add_argument("--subset", type=str, help="Run only queries with IDs starting with this prefix (e.g. EDU, EMO)")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()

    # Check environment
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    queries = QUERIES
    if args.subset:
        queries = [q for q in QUERIES if q["id"].startswith(args.subset.upper())]
        if not queries:
            print(f"No queries match subset '{args.subset}'. Available prefixes: EDU, EMO, MED, EDGE, MIX")
            sys.exit(1)

    print(f"Melod·AI Evaluation Harness — {len(queries)} queries")
    print(f"Target: {MELODAI_URL}")

    results = run_eval(queries, verbose=not args.quiet)
    print_dashboard(results)

    if args.output:
        with open(args.output, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "url": MELODAI_URL,
                "results": results,
            }, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
