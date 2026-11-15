"""Product search that understands how traders actually type.

"سماعات iphone" should find "سماعة ابل": every word is looked up on its own, in any order, with
singular/plural and "ال" ignored, and with brand / product-type synonyms (iphone = ابل = apple).
A word only matches at the start of a word in the name, so "ابل" does not hit "قابلة".

All matching runs on name keys made by wholesale_service.normalize_name (أ→ا, ة→ه, ى→ي, lower case).
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

# Words that mean the same thing to a shopper. Written in normalized form (ا not أ, ه not ة, ي not ى).
SYNONYMS: List[List[str]] = [
    # brands
    ["iphone", "ايفون", "ايفن", "apple", "ابل", "ابول"],
    ["airpods", "ايربودز", "ايربود", "airpod"],
    ["samsung", "سامسونج", "سامسونغ", "galaxy", "جالاكسي", "جلاكسي"],
    ["xiaomi", "شاومي", "redmi", "ريدمي"],
    ["huawei", "هواوي"],
    ["honor", "هونر", "اونر"],
    ["oppo", "اوبو"],
    ["realme", "ريلمي"],
    ["infinix", "انفينكس"],
    ["vivo", "فيفو"],
    ["tecno", "تكنو"],
    ["nokia", "نوكيا"],
    ["sony", "سوني"],
    ["lenovo", "لينوفو"],
    ["anker", "انكر"],
    # product types
    ["سماعه", "سماعات", "headphone", "headphones", "headset", "earphone", "earphones", "earbuds", "هيدفون", "هيدسيت", "هاندفري"],
    ["شاحن", "شواحن", "charger", "adapter", "ادابتر"],
    ["كابل", "كابلات", "كيبل", "وصله", "cable"],
    ["جراب", "جرابات", "كفر", "cover", "case"],
    ["باور", "powerbank", "باوربانك"],
    ["ماوس", "mouse"],
    ["كيبورد", "keyboard"],
    ["لابتوب", "لاب", "laptop"],
    ["ساعه", "ساعات", "watch"],
    ["شاشه", "شاشات", "سكرينه", "اسكرينه", "سكرين", "screen"],
    ["بسكويت", "بسكوت", "biscuit"],
    ["شيبسي", "شيبس", "chips"],
    ["مياه", "ميه", "مايه", "water"],
    ["عصير", "juice"],
    ["زيت", "oil"],
    ["صابون", "soap"],
    ["شامبو", "shampoo"],
    ["مناديل", "منديل", "tissue", "كلينكس"],
]

_ARABIC = re.compile(r"[؀-ۿ]")


def stem(word: str) -> str:
    """Rough singular / no-article form: السماعات -> سماع, سماعه -> سماع, headphones -> headphone."""
    if _ARABIC.search(word):
        if len(word) >= 5 and word.startswith("ال"):
            word = word[2:]
        if len(word) >= 5 and word.endswith("ات"):
            word = word[:-2]
        elif len(word) >= 6 and word.endswith(("ين", "ون")):
            word = word[:-2]
        elif len(word) >= 4 and word.endswith("ه"):
            word = word[:-1]
        return word
    if len(word) >= 5 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


_GROUPS: Dict[str, List[str]] = {}
for _group in SYNONYMS:
    _stems = sorted({stem(w) for w in _group})
    for _w in _group:
        _GROUPS[stem(_w)] = _stems


def query_terms(key: str) -> List[List[str]]:
    """Normalized query -> one list of acceptable word starts per query word."""
    terms = []
    for word in key.split():
        if len(word) < 2 and not word.isdigit():
            continue  # stray letters like "و"
        base = stem(word)
        alts = set(_GROUPS.get(base, [])) | {base}
        terms.append(sorted(alts))
    return terms


def _word_clause(alts: Sequence[str], column: str) -> Tuple[str, List[str]]:
    """SQL: some word in `column` starts with one of `alts` (or with ال + it)."""
    parts, params = [], []
    for alt in alts:
        parts.append(f"(' ' || {column}) LIKE ?")
        params.append(f"% {alt}%")
        if _ARABIC.search(alt):
            parts.append(f"(' ' || {column}) LIKE ?")
            params.append(f"% ال{alt}%")
    return "(" + " OR ".join(parts) + ")", params


def all_words(terms: List[List[str]], column: str) -> Tuple[str, List[str]]:
    """Every query word is in the name."""
    sql, params = [], []
    for alts in terms:
        part, p = _word_clause(alts, column)
        sql.append(part)
        params += p
    return " AND ".join(sql), params


def score(terms: List[List[str]], column: str) -> Tuple[str, List[str]]:
    """How many query words the name has (for "closest" results when nothing has them all)."""
    sql, params = [], []
    for alts in terms:
        part, p = _word_clause(alts, column)
        sql.append(f"(CASE WHEN {part} THEN 1 ELSE 0 END)")
        params += p
    return "(" + " + ".join(sql) + ")", params
