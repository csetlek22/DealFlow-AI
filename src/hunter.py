"""Hunter.io email enrichment provider."""

import os
from urllib.parse import urlsplit

import requests

HUNTER_ENDPOINT = "https://api.hunter.io/v2/domain-search"


def _extract_domain(value):
    """Normalize a URL or bare domain into a lowercased hostname."""
    value = (value or "").strip()
    if not value:
        return ""

    if "://" not in value:
        value = "https://" + value

    try:
        host = urlsplit(value).netloc.lower()
    except Exception:
        return ""

    if host.startswith("www."):
        host = host[4:]

    return host


def enrich_email(domain, api_key=None):
    """Return the most relevant verified email for a domain, or None."""
    api_key = (api_key or os.getenv("HUNTER_API_KEY", "")).strip()
    if not api_key or not domain:
        return None

    try:
        response = requests.get(
            HUNTER_ENDPOINT,
            params={"domain": domain, "api_key": api_key},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Hunter enrichment failed for '{domain}': {e}")
        return None

    emails = (data.get("data") or {}).get("emails") or []
    if not emails:
        return None

    # Rank candidates: prefer personal (non-generic) addresses, higher Hunter
    # confidence, and entries that include a full name.
    best = None
    best_score = -1

    for email in emails:
        score = 0
        if email.get("type") == "personal":
            score += 2
        confidence = email.get("confidence") or 0
        score += int(confidence)
        if email.get("first_name") and email.get("last_name"):
            score += 1

        if score > best_score:
            best_score = score
            best = email

    if not best or not best.get("value"):
        return None

    name = " ".join(
        part for part in (best.get("first_name"), best.get("last_name"))
        if part
    ).strip()

    return {
        "contact_email": best.get("value"),
        "contact_name": name or None,
        "title": best.get("position") or None,
        "email_source": "hunter",
        "provider": "hunter",
        "confidence": (
            "high" if (best.get("confidence") or 0) >= 80 else "medium"
        ),
    }
