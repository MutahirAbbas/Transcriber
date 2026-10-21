"""Accuracy metrics. All text is normalized the same way before comparison —
otherwise "$5,000" vs "five thousand dollars" counts as four errors and the
numbers mean nothing.

No external deps: word-level Levenshtein is ~20 lines and we want exact control
over normalization anyway.
"""
from __future__ import annotations

import re

# ---- normalization -----------------------------------------------------------

_FILLERS = {"um", "uh", "uh-huh", "mm", "mhm", "hmm", "ah", "er", "mm-hmm"}

_CONTRACTIONS = [
    (r"\bwon't\b", "will not"), (r"\bcan't\b", "cannot"), (r"\bn't\b", " not"),
    (r"\b(\w+)'re\b", r"\1 are"), (r"\b(\w+)'ve\b", r"\1 have"), (r"\b(\w+)'ll\b", r"\1 will"),
    (r"\b(\w+)'d\b", r"\1 would"), (r"\bi'm\b", "i am"),
    # "it's / that's / what's" are left alone: apostrophe stripping makes them
    # "its / thats / whats", which is what most providers emit without punctuation.
]

_SMALL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALE = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def _words_to_digits(tokens: list[str]) -> list[str]:
    """'fifty dollars' → '50 dollars'; 'five thousand' → '5000'. Greedy, good enough."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] not in _SMALL and tokens[i] not in _SCALE:
            out.append(tokens[i]); i += 1; continue
        total = cur = 0
        j = i
        while j < len(tokens) and (tokens[j] in _SMALL or tokens[j] in _SCALE or tokens[j] == "and"):
            t = tokens[j]
            if t in _SMALL:
                cur += _SMALL[t]
            elif t == "hundred":
                cur = max(cur, 1) * 100
            elif t in _SCALE:
                total += max(cur, 1) * _SCALE[t]; cur = 0
            j += 1
        out.append(str(total + cur))
        i = j
    return out


def normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\$\s*([\d,.]+)", r"\1 dollars", t)   # $50 → 50 dollars
    t = t.replace("$", " dollars ").replace("%", " percent ")
    t = re.sub(r"\bdollar\b", "dollars", t)
    for pat, rep in _CONTRACTIONS:
        t = re.sub(pat, rep, t)
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)   # 5,000 → 5000
    t = re.sub(r"[^\w\s'-]", " ", t)              # drop punctuation
    t = re.sub(r"[-']", " ", t)
    toks = [w for w in t.split() if w not in _FILLERS]
    toks = _words_to_digits(toks)
    return " ".join(toks)


# ---- word error rate ---------------------------------------------------------

def _align(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """Returns (substitutions, deletions, insertions) via DP backtrace."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): d[i][0] = i
    for j in range(m + 1): d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            d[i][j] = min(d[i - 1][j - 1] + cost, d[i - 1][j] + 1, d[i][j - 1] + 1)
    subs = dels = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            if ref[i - 1] != hyp[j - 1]: subs += 1
            i -= 1; j -= 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dels += 1; i -= 1
        else:
            ins += 1; j -= 1
    return subs, dels, ins


def wer(reference: str, hypothesis: str) -> dict:
    ref, hyp = normalize(reference).split(), normalize(hypothesis).split()
    if not ref:
        return {"wer": None, "subs": 0, "dels": 0, "ins": 0, "ref_words": 0}
    s, d, i = _align(ref, hyp)
    return {"wer": round((s + d + i) / len(ref), 4), "subs": s, "dels": d, "ins": i, "ref_words": len(ref)}


# ---- domain keyword accuracy -------------------------------------------------

def keyword_recall(reference: str, hypothesis: str, keywords: list[str]) -> dict:
    """For each keyword occurrence in the reference, did the hypothesis get it?"""
    ref, hyp = normalize(reference), normalize(hypothesis)
    total = hit = 0
    misses: dict[str, int] = {}
    for kw in keywords:
        k = normalize(kw)
        if not k:
            continue
        pat = r"\b" + re.escape(k) + r"\b"
        n_ref = len(re.findall(pat, ref))
        if n_ref == 0:
            continue
        got = min(n_ref, len(re.findall(pat, hyp)))
        total += n_ref; hit += got
        if got < n_ref:
            misses[kw] = n_ref - got
    return {
        "keyword_recall": round(hit / total, 4) if total else None,
        "keyword_occurrences": total,
        "keyword_misses": misses,
    }


# ---- diarization -------------------------------------------------------------

def speaker_turn_count(segments: list) -> int:
    """Cheap diarization sanity check: number of speaker changes detected.
    Real DER (pyannote.metrics) is a later upgrade and needs time-aligned references."""
    turns, prev = 0, None
    for s in segments:
        if s.speaker and s.speaker != prev:
            turns += 1; prev = s.speaker
    return turns
