"""Regex-based PII scanner for blocking sensitive data from being shared.

Detects: SSN, credit card numbers (Luhn-validated), email addresses, phone numbers.
Used by the memory API to prevent accidental sharing of personal information.
"""

import re
from dataclasses import dataclass


@dataclass
class PIIMatch:
    """A detected PII match."""
    pii_type: str
    pattern_match: str


# SSN pattern: 3 digits - 2 digits - 4 digits
_SSN_PATTERN = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')

# Credit card patterns (common formats: spaces, dashes, or continuous)
_CC_PATTERN = re.compile(
    r'\b(?:'
    r'4\d{3}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}'  # Visa
    r'|5[1-5]\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}'  # Mastercard
    r'|3[47]\d{2}[\s-]?\d{6}[\s-]?\d{5}'  # Amex
    r'|6(?:011|5\d{2})[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}'  # Discover
    r')\b'
)

# Email pattern
_EMAIL_PATTERN = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
)

# US phone number patterns
_PHONE_PATTERN = re.compile(
    r'\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b'
)


def _luhn_check(number: str) -> bool:
    """Validate a credit card number using the Luhn algorithm."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def scan_for_pii(text: str) -> list[PIIMatch]:
    """Scan text for PII patterns.

    Returns a list of PIIMatch objects for each detected PII type.
    Empty list means no PII detected.
    """
    matches: list[PIIMatch] = []

    # Check SSN
    for match in _SSN_PATTERN.finditer(text):
        matches.append(PIIMatch(pii_type="SSN", pattern_match=match.group()))

    # Check credit cards (with Luhn validation)
    for match in _CC_PATTERN.finditer(text):
        raw = match.group()
        if _luhn_check(raw):
            matches.append(PIIMatch(pii_type="credit card number", pattern_match=raw))

    # Check email
    for match in _EMAIL_PATTERN.finditer(text):
        matches.append(PIIMatch(pii_type="email address", pattern_match=match.group()))

    # Check phone numbers
    for match in _PHONE_PATTERN.finditer(text):
        matches.append(PIIMatch(pii_type="phone number", pattern_match=match.group()))

    return matches


def has_pii(text: str) -> tuple[bool, str | None]:
    """Check if text contains PII.

    Returns:
        Tuple of (has_pii, pii_type_description).
        If no PII: (False, None)
        If PII found: (True, "SSN detected") etc.
    """
    matches = scan_for_pii(text)
    if not matches:
        return False, None
    # Return the first detected type
    return True, f"{matches[0].pii_type} detected"
