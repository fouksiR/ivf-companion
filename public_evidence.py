"""
public_evidence.py — Curated multi-source evidence layer for the PUBLIC ask widget.

Complements nice_ng257_evidence.py (match_nice_evidence) and ANZARD charts.
Focus: IVF add-ons / "optional extras" and success-rate realism.

Sources (all paraphrased, never quoted; attribute by name in the answer):
  - ESHRE     European Society of Human Reproduction and Embryology — patient guidelines
  - Cochrane  Cochrane Gynaecology & Fertility Group — systematic reviews
  - HFEA      UK Human Fertilisation & Embryology Authority — add-on traffic-light ratings
  - EvIVF     Evidence-based IVF (Univ. of Melbourne, Dr S. Lensen, with Cochrane)
"""

import re

PUBLIC_EVIDENCE = [
    {
        "id": "pgt_a",
        "source": "HFEA / Cochrane / ESHRE",
        "keywords": ["pgt-a", "pgta", "pgt a", "genetic testing", "embryo testing",
                     "aneuploidy", "screen embryos", "chromosome", "pgs", "test my embryos",
                     "normal embryo", "abnormal embryo", "mosaic"],
        "summary": ("PGT-A (genetic screening of embryos for chromosome number) does not "
                    "reliably increase the chance of a baby for most people, and HFEA rates "
                    "it amber/red. It can be reasonable in specific situations (e.g. recurrent "
                    "loss, older age) - worth discussing case-by-case with your specialist."),
    },
    {
        "id": "endometrial_scratch",
        "source": "Cochrane / HFEA",
        "keywords": ["scratch", "endometrial scratch", "scratching", "endometrial injury"],
        "summary": ("The best recent evidence (large trials, Cochrane) shows endometrial "
                    "scratching does not improve live birth rates. HFEA rates it red. "
                    "It is generally not recommended."),
    },
    {
        "id": "time_lapse",
        "source": "HFEA / Cochrane",
        "keywords": ["time-lapse", "time lapse", "embryoscope", "timelapse", "embryo camera",
                     "incubator imaging"],
        "summary": ("Time-lapse embryo imaging is interesting technology but current evidence "
                    "does not show it improves your chance of a baby versus standard methods. "
                    "HFEA rates it amber/red - fine if free, not worth paying extra for."),
    },
    {
        "id": "assisted_hatching",
        "source": "Cochrane / HFEA",
        "keywords": ["assisted hatching", "hatching", "zona"],
        "summary": ("Cochrane finds no clear evidence that assisted hatching improves live "
                    "birth, and it may carry small risks. HFEA rates it red. Not routinely "
                    "recommended."),
    },
    {
        "id": "embryo_glue",
        "source": "EvIVF / HFEA / Cochrane",
        "keywords": ["embryo glue", "embryoglue", "hyaluronan", "hyaluronate", "glue"],
        "summary": ("EmbryoGlue (hyaluronan-rich transfer medium) is low-risk and adds no "
                    "burden on you; some evidence suggests a modest benefit, but it is not "
                    "definitive. Many clinics include it at no/low cost - reasonable to use, "
                    "not worth a large fee."),
    },
    {
        "id": "immune_intralipid_steroids",
        "source": "EvIVF / ESHRE",
        "keywords": ["intralipid", "immune", "immunology", "steroid", "steroids",
                     "corticosteroid", "prednisolone", "nk cells", "natural killer",
                     "immune therapy", "ivig", "infusion"],
        "summary": ("So-called immune therapies (intralipid infusions, steroids, IVIG, NK-cell "
                    "treatment) are not supported by good evidence for routine IVF and can carry "
                    "real risks and cost. ESHRE does not recommend them outside research. Be "
                    "cautious and discuss carefully with your specialist before paying."),
    },
    {
        "id": "acupuncture",
        "source": "Cochrane",
        "keywords": ["acupuncture", "needles", "tcm", "chinese medicine", "alternative therapy"],
        "summary": ("Cochrane finds no convincing evidence that acupuncture improves IVF live "
                    "birth rates. It is generally safe and some people find it relaxing, so it "
                    "is fine as a comfort measure - just not as a treatment that changes success."),
    },
    {
        "id": "icsi_no_male_factor",
        "source": "ESHRE / Cochrane",
        "keywords": ["icsi", "sperm injection", "do i need icsi", "icsi vs ivf"],
        "summary": ("ICSI (injecting a single sperm into the egg) clearly helps when there is a "
                    "sperm problem, but for couples without male-factor infertility it does not "
                    "improve the chance of a baby over standard IVF. ESHRE advises reserving it "
                    "for male-factor cases."),
    },
    {
        "id": "supplements",
        "source": "ESHRE / Cochrane",
        "keywords": ["dhea", "coq10", "coenzyme", "supplement", "supplements", "vitamins",
                     "melatonin", "antioxidant", "myo-inositol", "inositol"],
        "summary": ("Evidence for fertility supplements (DHEA, CoQ10, antioxidants) is weak and "
                    "uncertain; they are not proven to increase live birth. Folic acid before/"
                    "early pregnancy is the one clearly recommended supplement. Always tell your "
                    "specialist what you are taking."),
    },
    {
        "id": "what_is_ivf",
        "source": "EvIVF",
        "keywords": ["what is ivf", "how does ivf work", "ivf process", "ivf steps",
                     "in vitro", "what happens in ivf", "explain ivf"],
        "summary": ("IVF means eggs are collected from the ovaries, combined with sperm in the "
                    "lab to form embryos, grown for about 3-5 days, then one embryo is placed "
                    "back in the uterus. Extra embryos can be frozen for later. It is a "
                    "step-by-step process, not a single event."),
    },
    {
        "id": "success_rates",
        "source": "HFEA",
        "keywords": ["success rate", "chances", "how likely", "odds", "will it work",
                     "clinic success", "best clinic", "per cycle", "live birth rate"],
        "summary": ("Success depends most on age and is best understood per cycle, with chances "
                    "rising over several cycles. HFEA warns against choosing a clinic on "
                    "headline success figures alone, as these can be presented in misleading "
                    "ways - ask for age-matched, per-cycle live birth rates."),
    },
    {
        "id": "addons_general",
        "source": "HFEA / EvIVF",
        "keywords": ["add-on", "add on", "addon", "extra", "extras", "optional", "should i pay",
                     "worth it", "upgrade", "package", "is it worth"],
        "summary": ("Most IVF 'add-ons' (optional paid extras) are not proven to give you a "
                    "better chance of a baby. HFEA's traffic-light system rates each one; a good "
                    "rule is: if it is not green-rated, treat the claimed benefit sceptically and "
                    "ask your specialist what the evidence is for YOUR situation before paying."),
    },
]


def match_public_evidence(message: str, top_k: int = 2) -> str:
    if not message:
        return ""
    msg_lower = message.lower()
    msg_words = set(re.findall(r"\b\w+\b", msg_lower))
    scored = []
    for topic in PUBLIC_EVIDENCE:
        score = 0
        for kw in topic["keywords"]:
            kw_lower = kw.lower()
            if kw_lower in msg_words or (len(kw_lower) > 3 and kw_lower in msg_lower):
                score += 2
            elif any(w in kw_lower for w in msg_words if len(w) > 3):
                score += 1
        if score > 0:
            scored.append((score, topic))
    if not scored:
        return ""
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]
    parts = [
        "EVIDENCE TO GROUND YOUR ANSWER (name the source naturally, e.g. 'per HFEA' or "
        "'Cochrane reviews find' - rephrase warmly, do NOT dump raw text, do NOT overload):"
    ]
    for _score, topic in top:
        parts.append(f"\n  [{topic['source']}] {topic['summary']}")
    return "\n".join(parts)
