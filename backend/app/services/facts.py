"""Deterministic extraction of facts and salient tokens from text.

Used by document comparison (conflict detection) and consolidation
(fact merging + preservation checks). Content is treated purely as data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_KV = re.compile(r"^\s*(?:[-*•]\s*)?(?:\*\*)?([A-Za-z][A-Za-z0-9 /&()'._-]{1,60}?)(?:\*\*)?\s*[:=]\s*(.+?)\s*$")
_MONEY = re.compile(
    r"(?:[$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|bn|million|billion|thousand))?|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|MAD|dollars|euros))",
    re.I,
)
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
_DATE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    re.I,
)
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PROPER = re.compile(
    r"\b(?:[A-Z][a-z]+(?:[ \t]+(?:[A-Z][a-z]+|&|of|and))*[ \t]+[A-Z][a-z]+|[A-Z]{2,}[a-z]*(?:[ \t]+[A-Z][a-z]+)*)\b"
)
_STOP_PROPER = {"The", "This", "That", "These", "Those", "It", "We", "Our", "In", "On", "At", "For", "And", "But"}


@dataclass(frozen=True)
class KeyValueFact:
    key: str  # normalised key
    label: str  # original key text
    value: str  # original value text
    norm_value: str
    line: str


def normalize_key(key: str) -> str:
    key = re.sub(r"[^a-z0-9 ]+", " ", key.lower())
    key = re.sub(r"\b(the|project|total|planned|estimated|approx|approximately)\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def normalize_value(value: str) -> str:
    value = value.strip().strip(".;").lower()
    value = re.sub(r"(?<=\d),(?=\d{3}\b)", "", value)
    value = re.sub(r"\s+", " ", value)
    value = value.replace("usd", "$").replace("dollars", "$")
    return value.replace(" ", "")


def extract_kv_facts(text: str) -> list[KeyValueFact]:
    facts: list[KeyValueFact] = []
    for line in text.splitlines():
        match = _KV.match(line)
        if not match:
            continue
        label, value = match.group(1).strip(), match.group(2).strip()
        if value.startswith("//") or len(value) > 200 or len(label.split()) > 6:
            continue
        key = normalize_key(label)
        if not key:
            continue
        facts.append(KeyValueFact(key, label, value, normalize_value(value), line.strip()))
    return facts


def numeric_tokens(text: str) -> set[str]:
    """Numbers, money, percentages and dates appearing in text (normalised)."""
    text = text.replace("\r", "")
    tokens: set[str] = set()
    for pattern in (_MONEY, _PERCENT, _DATE):
        tokens.update(normalize_value(m.group(0)) for m in pattern.finditer(text))
    for match in _NUMBER.finditer(text):
        tokens.add(normalize_value(match.group(0)))
    return tokens


def salient_tokens(text: str) -> set[str]:
    """Tokens whose loss would lose information: figures, dates, emails, proper names."""
    text = text.replace("\r", "")
    tokens = numeric_tokens(text)
    tokens.update(m.group(0).lower() for m in _EMAIL.finditer(text))
    for match in _PROPER.finditer(text):
        phrase = match.group(0)
        if phrase.split()[0] in _STOP_PROPER and len(phrase.split()) == 1:
            continue
        tokens.add(phrase.lower())
    return tokens


def token_present(token: str, normalised_haystack: str) -> bool:
    return token.replace(" ", "") in normalised_haystack


def normalised_for_search(text: str) -> str:
    return normalize_value(text.lower())
