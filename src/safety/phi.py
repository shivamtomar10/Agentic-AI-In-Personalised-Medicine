"""PHI guard: blocks obvious personal identifiers from being included in free-text fields.
Patients enter their data via structured fields (account profile, lab values, medication lists).
This guard prevents accidental submission of raw identifiers (phone, email, SSN) in notes fields.
Regex-based, NOT a certified de-identification system — do not rely on it as your sole control."""
from __future__ import annotations

import re
from typing import Iterable, List

_PATTERNS = {
    "email address": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "phone number": r"(?<!\d)(\+?\d{1,3}[\s-]?)?(\(?\d{3}\)?[\s-]?)\d{3}[\s-]?\d{4}(?!\d)",
    "US SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "Aadhaar-like 12-digit number": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
}


def find_phi(texts: Iterable[str]) -> List[str]:
    found = []
    for label, pat in _PATTERNS.items():
        if any(re.search(pat, t or "") for t in texts):
            found.append(label)
    return found
