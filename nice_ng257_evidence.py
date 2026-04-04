"""
NICE NG257 Evidence Chunks — Patient-Friendly Fertility Evidence
================================================================
Extracted from NICE NG257 (Fertility problems: assessment and treatment, March 2026).
20 topics with keyword matching for injection into Claude system prompt.

Usage:
    from nice_ng257_evidence import match_nice_evidence
    evidence_text = match_nice_evidence("what are my chances at 38?")
"""

import re
import logging

logger = logging.getLogger(__name__)

NICE_EVIDENCE: list[dict] = [
    {
        "id": "conception_chances_age",
        "keywords": ["age", "chances", "older", "younger", "conceive", "natural", "pregnancy rate",
                      "how long", "fertility age", "biological clock", "too old", "35", "38", "40",
                      "decline", "egg quality", "ovarian"],
        "summary": (
            "Natural conception rates by age (with regular unprotected sex for 2 years): "
            "ages 19-26: 92-98% conceive; 27-29: 87-95%; 30-34: 86-94%; 35-39: 82-90%. "
            "Fertility does decline with age, but the majority of women under 40 can still conceive. "
            "After 36, investigation is recommended sooner (after 6 months of trying)."
        ),
        "nice_ref": "NICE NG257 Table 1, 1.16.5",
    },
    {
        "id": "iui_success_rates",
        "keywords": ["iui", "insemination", "donor sperm", "donor", "iui success", "iui rate",
                      "artificial insemination", "sperm donor"],
        "summary": (
            "IUI with donor sperm cumulative success rates (up to 6 cycles): "
            "ages 18-34: 57% per cycle, 81% cumulative; 35-37: 54%/78%; 38-39: 47%/72%; 40-42: 31%/52%. "
            "IUI is a less invasive option before IVF. Multiple cycles improve cumulative chances significantly."
        ),
        "nice_ref": "NICE NG257 Table 2",
    },
    {
        "id": "when_to_investigate",
        "keywords": ["how long try", "when see doctor", "when investigate", "how long before",
                      "12 months", "6 months", "trying to conceive", "ttc", "been trying",
                      "should i worry", "not pregnant yet", "refer", "investigation"],
        "summary": (
            "Investigate after 12 months of regular unprotected sex or 6 cycles of artificial insemination. "
            "If you're 36 or older, or there's a known cause (e.g. irregular periods, previous surgery), "
            "referral should happen sooner — don't wait the full 12 months. "
            "Important: previous miscarriage does NOT restart the clock for investigation."
        ),
        "nice_ref": "NICE NG257 1.16.4-1.16.8",
    },
    {
        "id": "amh_ovarian_reserve",
        "keywords": ["amh", "ovarian reserve", "egg count", "egg reserve", "afc", "antral follicle",
                      "fsh", "how many eggs", "egg supply", "diminished reserve", "low amh", "high amh"],
        "summary": (
            "AMH and AFC tests predict how your ovaries might respond to IVF stimulation — "
            "but they do NOT predict your chances of natural conception. "
            "A low AMH does not mean you can't conceive naturally. "
            "FSH testing is NOT recommended by NICE as it's less reliable than AMH/AFC. "
            "These tests help your specialist plan the right medication dose for IVF."
        ),
        "nice_ref": "NICE NG257 1.18.2-1.18.5",
    },
    {
        "id": "ivf_access_cycles",
        "keywords": ["how many cycles", "ivf cycles", "funded cycles", "nhs cycles", "access",
                      "how many ivf", "3 cycles", "one cycle", "full cycle", "fresh and frozen",
                      "eligibility", "ivf funding"],
        "summary": (
            "NICE recommends: under 40 → offer 3 full cycles of IVF (consider 3 more if needed); "
            "ages 40-41 → offer 1 full cycle. "
            "A 'full cycle' means stimulation plus ALL fresh and frozen embryo transfers from that batch. "
            "This is important — a single stimulation often produces multiple embryos, "
            "and each frozen transfer counts as part of the same cycle."
        ),
        "nice_ref": "NICE NG257 1.39.3-1.39.9",
    },
    {
        "id": "endometriosis_fertility",
        "keywords": ["endometriosis", "endo", "endo fertility", "endometrioma", "chocolate cyst",
                      "stage 1", "stage 2", "stage 3", "stage 4", "endo surgery", "laparoscopy"],
        "summary": (
            "For mild-moderate endometriosis: try expectant management for up to 2 years, "
            "then consider surgical excision/ablation, followed by IUI with gonadotrophins (up to 4 cycles) "
            "OR IVF. Surgery to remove endometriosis can improve natural conception chances. "
            "IVF is recommended if other treatments haven't worked or if endometriosis is severe."
        ),
        "nice_ref": "NICE NG257 1.36.1-1.36.3",
    },
    {
        "id": "unexplained_infertility",
        "keywords": ["unexplained", "no reason", "nothing wrong", "all tests normal", "idiopathic",
                      "don't know why", "can't find reason", "no cause", "diagnosis unexplained"],
        "summary": (
            "For unexplained infertility: try naturally for up to 2 years total. "
            "Ovarian stimulation ALONE (Clomid/letrozole without insemination) is NOT recommended. "
            "If not successful: IUI with gonadotrophins OR move to IVF. "
            "Unexplained infertility is common (~25% of couples) and does NOT mean nothing can be done."
        ),
        "nice_ref": "NICE NG257 1.38.1-1.38.3",
    },
    {
        "id": "fresh_vs_frozen",
        "keywords": ["fresh vs frozen", "frozen embryo", "fresh transfer", "fet", "freeze all",
                      "frozen better", "fresh better", "natural fet", "medicated fet",
                      "hormone replacement", "natural cycle transfer"],
        "summary": (
            "Fresh and frozen embryo transfers have similar live birth rates. "
            "For frozen transfers, natural cycle FET and hormone-supplemented FET "
            "have similar outcomes — your specialist will recommend what suits your body best. "
            "Freezing all embryos (freeze-all strategy) is sometimes recommended to reduce OHSS risk."
        ),
        "nice_ref": "NICE NG257 1.49.12",
    },
    {
        "id": "pgta_testing",
        "keywords": ["pgt", "pgta", "pgs", "genetic testing", "embryo testing", "chromosomal",
                      "aneuploidy", "mosaic", "biopsy embryo", "tested embryos", "normal embryo"],
        "summary": (
            "NICE does NOT recommend PGT-A (preimplantation genetic testing for aneuploidy) "
            "to improve live birth rates. The evidence is inconclusive, "
            "and testing may reduce the number of usable embryos (some mosaic embryos "
            "that would be discarded can actually result in healthy pregnancies). "
            "Discuss with your specialist whether the potential benefits outweigh the risks for your situation."
        ),
        "nice_ref": "NICE NG257 1.48.1",
    },
    {
        "id": "icsi_vs_ivf",
        "keywords": ["icsi", "intracytoplasmic", "sperm injection", "icsi vs ivf", "need icsi",
                      "conventional ivf", "fertilisation method", "icsi better"],
        "summary": (
            "ICSI is NOT recommended for non-male-factor infertility — standard IVF works just as well. "
            "ICSI IS recommended for: significantly abnormal sperm parameters, previous failed fertilisation, "
            "surgically retrieved sperm, or using frozen eggs. "
            "Your specialist will recommend ICSI if there's a clear clinical reason for it."
        ),
        "nice_ref": "NICE NG257 1.50.1-1.50.3",
    },
    {
        "id": "endometrial_scratch",
        "keywords": ["scratch", "endometrial scratch", "endometrial biopsy", "uterine scratch",
                      "scratch procedure", "scratch before ivf", "scratch improve"],
        "summary": (
            "NICE does NOT recommend endometrial scratching before IVF. "
            "The evidence is mixed and inconclusive, and it's an invasive, often painful procedure. "
            "Despite being widely offered, current evidence does not support it improving pregnancy rates."
        ),
        "nice_ref": "NICE NG257 1.40.1",
    },
    {
        "id": "immune_treatments",
        "keywords": ["immune", "intralipid", "ivig", "steroid", "prednisolone", "nk cells",
                      "natural killer", "immune testing", "immune treatment", "immunology",
                      "autoimmune", "implantation failure"],
        "summary": (
            "NICE does NOT recommend immune treatments for fertility — this includes intralipids, "
            "IVIG (intravenous immunoglobulin), and steroids like prednisolone. "
            "There is no good evidence they improve pregnancy rates, "
            "and they carry potential safety concerns. "
            "NK cell testing in blood does not reflect what's happening in the uterus."
        ),
        "nice_ref": "NICE NG257 1.42.1",
    },
    {
        "id": "sperm_dna_fragmentation",
        "keywords": ["dna fragmentation", "sperm dna", "sperm quality", "sperm test", "dna damage",
                      "sperm supplements", "antioxidant", "coq10 sperm", "sperm vitamin"],
        "summary": (
            "NICE recommends: do NOT routinely test for sperm DNA fragmentation, "
            "do NOT treat with antioxidant supplements, and do NOT do surgical sperm retrieval for it. "
            "While DNA fragmentation is a real phenomenon, current tests don't reliably predict fertility outcomes, "
            "and treatments haven't been shown to improve pregnancy rates."
        ),
        "nice_ref": "NICE NG257 1.17.6, 1.24.6, 1.27.1",
    },
    {
        "id": "male_factor",
        "keywords": ["sperm count", "motility", "morphology", "semen analysis", "male factor",
                      "male infertility", "low sperm", "abnormal sperm", "varicocele", "azoospermia",
                      "oligospermia", "sperm test results"],
        "summary": (
            "WHO reference values: concentration ≥16 million/mL, motility ≥42%, morphology ≥4% normal. "
            "If the first semen analysis is abnormal, a repeat test is recommended. "
            "Physical examination is recommended after 2 abnormal results. "
            "Varicocele treatment is only recommended if it's clinically palpable AND semen is abnormal."
        ),
        "nice_ref": "NICE NG257 1.17.1-1.28.1",
    },
    {
        "id": "lifestyle_factors",
        "keywords": ["alcohol", "smoking", "caffeine", "coffee", "weight", "bmi", "exercise",
                      "diet", "lifestyle", "overweight", "underweight", "drinking", "smoke",
                      "body weight", "healthy lifestyle"],
        "summary": (
            "Lifestyle factors and fertility: "
            "Alcohol — safest to avoid when trying to conceive. "
            "Smoking — reduces fertility in both partners; support to quit should be offered. "
            "Caffeine — no consistent evidence of harm, but moderation is sensible. "
            "BMI >30 — associated with longer time to conceive and lower IVF success. "
            "BMI <18.5 — may need to gain weight to restore regular ovulation."
        ),
        "nice_ref": "NICE NG257 1.6-1.13",
    },
    {
        "id": "fertility_preservation",
        "keywords": ["freeze eggs", "freeze sperm", "egg freezing", "sperm freezing",
                      "fertility preservation", "cancer", "chemo", "chemotherapy",
                      "gonadotoxic", "preserve fertility", "social egg freezing", "elective freezing"],
        "summary": (
            "Fertility preservation should be discussed with anyone facing gonadotoxic treatment (e.g. chemotherapy). "
            "Options include egg, sperm, or embryo cryopreservation. "
            "There should be no age limits restricting access to preservation. "
            "For social/elective egg freezing: eggs frozen at a younger age have better outcomes."
        ),
        "nice_ref": "NICE NG257 1.53.1-1.53.8",
    },
    {
        "id": "psychological_support",
        "keywords": ["stress", "anxiety", "depression", "mental health", "counselling", "counseling",
                      "psychologist", "emotional support", "therapy", "coping", "struggling",
                      "overwhelmed", "mental wellbeing"],
        "summary": (
            "Stress can affect fertility indirectly by reducing libido and sexual frequency. "
            "NICE recommends offering counselling before, during, and after treatment. "
            "The counsellor should NOT be directly involved in your treatment to ensure independence. "
            "Psychological support is a standard part of good fertility care, not a sign of weakness."
        ),
        "nice_ref": "NICE NG257 1.2.1-1.2.5",
    },
    {
        "id": "donor_conception",
        "keywords": ["donor", "donor egg", "donor sperm", "donor embryo", "egg donor",
                      "sperm donor", "donation", "licensed clinic", "known donor",
                      "anonymous donor", "ici", "home insemination"],
        "summary": (
            "NICE recommends using licensed clinics for donor conception rather than unregulated routes. "
            "IUI (clinic-based insemination) is preferred over ICI (intracervical insemination). "
            "Licensed clinics ensure donor screening, legal protections, and proper consent. "
            "Counselling about the implications of donor conception is recommended for all parties."
        ),
        "nice_ref": "NICE NG257 1.1.4, 1.51.1-1.51.4",
    },
    {
        "id": "era_testing",
        "keywords": ["era", "era test", "emma", "alice", "endometrial receptivity",
                      "receptivity array", "window of implantation", "implantation window",
                      "era biopsy"],
        "summary": (
            "NICE does NOT recommend ERA, EMMA, or ALICE testing. "
            "Evidence shows no improvement in live birth or pregnancy rates, "
            "and there may be an increased risk of pregnancy loss. "
            "These are invasive tests that have not been shown to benefit patients."
        ),
        "nice_ref": "NICE NG257 1.41.1",
    },
    {
        "id": "hysteroscopy_before_ivf",
        "keywords": ["hysteroscopy", "uterine cavity", "hysteroscopy before ivf", "polyp",
                      "fibroid", "uterine abnormality", "uterine scan", "cavity check"],
        "summary": (
            "Routine hysteroscopy before IVF is NOT recommended by NICE. "
            "It should only be performed if a uterine abnormality is suspected "
            "(e.g. from an ultrasound finding). Unnecessary procedures add cost, discomfort, and delay."
        ),
        "nice_ref": "NICE NG257 1.40.2",
    },
    {
        "id": "embryo_transfer_strategy",
        "keywords": ["single embryo", "double embryo", "how many embryos", "transfer one",
                      "transfer two", "set", "det", "twins", "multiple pregnancy",
                      "elective single", "embryo transfer number"],
        "summary": (
            "NICE recommends single embryo transfer (SET) for women under 37 in their first cycle. "
            "Never transfer more than 2 embryos. "
            "For donor eggs, transfer strategy should be based on the donor's age, not the recipient's. "
            "Multiple pregnancies carry significantly higher risks for both mother and babies."
        ),
        "nice_ref": "NICE NG257 1.49.5-1.49.10",
    },
]


def match_nice_evidence(message: str, top_k: int = 3) -> str:
    """Match patient message against NICE evidence topics and return formatted text.

    Scoring: exact keyword match in message = 2 points, partial match = 1 point.
    Returns top_k highest-scoring topics formatted for Claude system prompt injection.
    """
    if not message:
        return ""

    msg_lower = message.lower()
    msg_words = set(re.findall(r'\b\w+\b', msg_lower))

    scored: list[tuple[int, dict]] = []
    for topic in NICE_EVIDENCE:
        score = 0
        for kw in topic["keywords"]:
            kw_lower = kw.lower()
            if kw_lower in msg_words or (len(kw_lower) > 3 and kw_lower in msg_lower):
                score += 2  # exact or substring match
            elif any(w in kw_lower for w in msg_words if len(w) > 3):
                score += 1  # partial match
        if score > 0:
            scored.append((score, topic))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    parts = [
        "EVIDENCE FROM NICE 2026 GUIDELINES (cite as 'NICE 2026 guidelines' — "
        "rephrase in warm, patient-friendly language, do NOT dump raw data):"
    ]
    for score, topic in top:
        parts.append(f"\n  [{topic['nice_ref']}] {topic['summary']}")

    return "\n".join(parts)
