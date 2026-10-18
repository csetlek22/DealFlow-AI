"""Iterative market-mapping discovery.

Enumerate candidate companies per (commodity, country) using the LLM's own
knowledge, then verify each candidate via web search so nothing unverified is
reported as fact. Results are deduplicated against the Notion CRM and against
previously discovered prospects, and cached to a JSON file so long runs can
resume.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from hunter import _extract_domain
from observability import emit, format_duration, get_logger
from prompts import ENUMERATION_PROMPT, VERIFICATION_PROMPT
from research import format_search_results, search_company_verification
from schemas import (
    CompanyCandidate,
    CompanyCandidateList,
    CompanyVerificationList,
)

logger = get_logger("market_map")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = BASE_DIR / "discovery_cache.json"

# Emit a heartbeat event if no other progress event has fired within this many
# seconds, so a long enumeration/verification call never leaves the UI silent.
HEARTBEAT_SECONDS = 30

# Each commodity maps to a short description of the segment the LLM should
# enumerate (importers, traders, processors, manufacturers, etc.).
COMMODITIES = {
    "coffee": "green coffee importers, coffee traders, industrial coffee roasters, coffee processors",
    "cocoa": "cocoa bean importers, cocoa traders, cocoa processors, chocolate manufacturers",
    "soy": "soybean importers, soy crushers, palm oil importers/refiners, vegetable oil processors",
    "wood": "timber importers, wood traders, plywood importers, paper and pulp manufacturers",
    "rubber": "natural rubber importers, rubber processors, tire manufacturers",
    "cattle": "cattle importers, beef processors, meat importers, leather importers, tanneries",
}

COUNTRIES = {
    "tier1": ["Germany", "Netherlands"],
    "tier2": ["Belgium", "France", "Italy", "Spain", "Austria", "Poland"],
    "tier3": [
        "Sweden",
        "Denmark",
        "Finland",
        "Portugal",
        "Czech Republic",
        "Ireland",
        "Luxembourg",
        "Romania",
        "Hungary",
        "Slovakia",
        "Slovenia",
        "Croatia",
        "Lithuania",
        "Latvia",
        "Estonia",
        "Greece",
        "Bulgaria",
    ],
}

_DEFAULT_TIERS = ["tier1", "tier2", "tier3"]


def all_countries(tiers=None) -> list[str]:
    """Return the ordered country list for the given tiers."""
    tiers = tiers or _DEFAULT_TIERS
    out: list[str] = []
    for tier in tiers:
        out.extend(COUNTRIES.get(tier, []))
    return out


def _norm_name(name) -> str:
    """Normalize a company name for duplicate matching."""
    value = (name or "").strip().lower()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _dedup_key(candidate: CompanyCandidate) -> tuple[str, str]:
    return (
        _norm_name(candidate.company_name),
        _extract_domain(candidate.website or ""),
    )


def _profile_fingerprint(profile: str) -> str:
    return hashlib.md5(profile.encode("utf-8")).hexdigest()[:12]


def _load_cache(profile: str) -> dict:
    """Load persisted prospects + dedup sets; reset if the profile changed."""
    empty = {"prospects": [], "seen_names": set(), "seen_domains": set()}
    if not CACHE_FILE.exists():
        return empty

    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return empty

    if raw.get("profile_fingerprint") != _profile_fingerprint(profile):
        return empty

    prospects = [
        CompanyCandidate(**item) for item in raw.get("prospects", [])
    ]
    return {
        "prospects": prospects,
        "seen_names": set(raw.get("seen_names", [])),
        "seen_domains": set(raw.get("seen_domains", [])),
    }


def _save_cache(prospects, seen_names, seen_domains, profile: str) -> None:
    data = {
        "profile_fingerprint": _profile_fingerprint(profile),
        "prospects": [p.model_dump() for p in prospects],
        "seen_names": sorted(seen_names),
        "seen_domains": sorted(seen_domains),
    }
    CACHE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def assign_priority(activity, industry, size) -> str:
    """Heuristic A–E priority per the target profile's segment definitions."""
    text = f"{activity or ''} {industry or ''}".lower()

    if any(
        key in text
        for key in ("importer", "import", "trader", "trading", "wholesale")
    ):
        return "A"

    if any(
        key in text
        for key in (
            "roaster",
            "roasting",
            "processor",
            "processing",
            "refiner",
            "refinery",
            "crusher",
            "crushing",
            "manufacturer",
            "manufacturing",
            "tannery",
            "tire",
            "tyre",
            "pulp",
            "plywood",
            "panel",
        )
    ):
        return "B"

    size_text = (size or "").lower().replace(",", "")
    if size and any(
        key in size_text
        for key in ("1000", "enterprise", "multinational", "global")
    ):
        return "D"

    return "C"


def _enumerate(
    llm,
    commodity: str,
    country: str,
    country_list: list[str],
    profile: str,
    max_companies: int,
) -> list[CompanyCandidate]:
    prompt = ENUMERATION_PROMPT.format(
        commodity=commodity,
        country=country,
        country_list=", ".join(country_list),
        target_profile=profile[:4000],
        max_companies=max_companies,
    )
    try:
        result = llm.with_structured_output(
            CompanyCandidateList,
            method="function_calling",
        ).invoke(prompt)
        return list(result.candidates or [])
    except Exception as e:
        logger.warning(
            "Enumeration failed for %s/%s: %s: %s",
            commodity, country, type(e).__name__, e,
        )
        return []


def _verify_batch(llm, candidates_with_evidence) -> list:
    blocks = []
    for candidate, evidence_text in candidates_with_evidence:
        blocks.append(
            f"NAME: {candidate.company_name}\n"
            f"CLAIMED WEBSITE: {candidate.website or 'unknown'}\n"
            f"CLAIMED COUNTRY: {candidate.country or 'unknown'}\n"
            f"CLAIMED ACTIVITY: {candidate.activity or 'unknown'}\n"
            f"SEARCH EVIDENCE:\n{evidence_text}\n"
        )

    prompt = VERIFICATION_PROMPT.format(candidates="\n---\n".join(blocks))
    try:
        result = llm.with_structured_output(
            CompanyVerificationList,
            method="function_calling",
        ).invoke(prompt)
        return list(result.verifications or [])
    except Exception as e:
        logger.warning(
            "Verification batch failed: %s: %s",
            type(e).__name__, e,
        )
        return []


def _merge(candidate: CompanyCandidate, verification) -> CompanyCandidate:
    candidate.confidence = verification.confidence
    candidate.website = verification.website or candidate.website
    candidate.country = verification.country or candidate.country
    candidate.industry = verification.industry or candidate.industry
    candidate.activity = verification.activity or candidate.activity
    candidate.company_size = (
        verification.company_size or candidate.company_size
    )
    candidate.linkedin_url = verification.linkedin_url
    candidate.evidence = verification.evidence
    return candidate


def run_discovery(
    llm,
    profile: str,
    limit: int | None = None,
    max_per_cell: int = 6,
    verify_batch_size: int = 12,
    tiers: list[str] | None = None,
    existing_names: set | None = None,
    existing_domains: set | None = None,
    progress=None,
) -> list[CompanyCandidate]:
    """Run iterative market mapping and return verified prospects.

    ``progress`` is an optional ``Callable[[dict], None]`` that receives a
    structured stage event (see ``observability.emit``) so a UI can show
    real-time progress without this module depending on a specific UI.
    """
    start = time.time()
    limit = limit or int(os.getenv("DISCOVERY_LIMIT", "500"))
    tiers = tiers or _DEFAULT_TIERS
    existing_names = existing_names or set()
    existing_domains = existing_domains or set()

    countries = all_countries(tiers)
    total_cells = len(COMMODITIES) * len(countries)

    cache = _load_cache(profile)
    prospects: list[CompanyCandidate] = cache["prospects"]
    seen_names: set = cache["seen_names"]
    seen_domains: set = cache["seen_domains"]

    for name in existing_names:
        seen_names.add(_norm_name(name))
    for domain in existing_domains:
        seen_domains.add(_extract_domain(domain))

    logger.info(
        "Discovery start: %d commodities x %d countries = %d cells; "
        "%d prospects restored from cache",
        len(COMMODITIES), len(countries), total_cells, len(prospects),
    )
    emit(
        progress, start, "start",
        f"Starting discovery across {total_cells} cells",
        total_cells=total_cells, found=len(prospects), processed=0,
    )

    cell_index = 0
    processed = 0
    last_heartbeat = start

    def heartbeat():
        nonlocal last_heartbeat
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            last_heartbeat = now
            logger.info(
                "heartbeat: elapsed=%s found=%d processed=%d",
                format_duration(now - start), len(prospects), processed,
            )
            emit(
                progress, start, "heartbeat",
                f"Still running — {format_duration(now - start)} elapsed",
                found=len(prospects), processed=processed,
            )

    for commodity in COMMODITIES:
        for country in countries:
            if len(prospects) >= limit:
                _save_cache(prospects, seen_names, seen_domains, profile)
                emit(
                    progress, start, "complete",
                    f"Reached target limit of {limit} companies",
                    found=len(prospects), processed=processed,
                )
                return prospects[:limit]

            cell_index += 1
            heartbeat()
            emit(
                progress, start, "enumerate",
                f"Enumerating {commodity} in {country}",
                commodity=commodity, country=country,
                cell_index=cell_index, total_cells=total_cells,
                found=len(prospects), processed=processed,
            )
            logger.info(
                "[%d/%d] Enumerating %s / %s",
                cell_index, total_cells, commodity, country,
            )

            candidates = _enumerate(
                llm,
                commodity,
                country,
                countries,
                profile,
                max_per_cell,
            )
            logger.info(
                "[%d/%d] %s / %s returned %d candidates",
                cell_index, total_cells, commodity, country, len(candidates),
            )

            # Search-verify each not-yet-seen candidate.
            with_evidence = []
            for candidate in candidates:
                key = _dedup_key(candidate)
                if key[0] and key[0] in seen_names:
                    continue
                if key[1] and key[1] in seen_domains:
                    continue

                heartbeat()
                emit(
                    progress, start, "search",
                    f"Verifying {candidate.company_name}",
                    commodity=commodity, country=country,
                    cell_index=cell_index, total_cells=total_cells,
                    found=len(prospects), processed=processed,
                )

                results = search_company_verification(
                    candidate.company_name,
                    candidate.country or country,
                    max_results=4,
                )
                evidence_text = format_search_results(results, max_chars=1200)
                with_evidence.append((candidate, evidence_text))
                processed += 1
                time.sleep(0.2)

            for i in range(0, len(with_evidence), verify_batch_size):
                chunk = with_evidence[i:i + verify_batch_size]
                heartbeat()
                emit(
                    progress, start, "verify",
                    f"Qualifying {len(chunk)} candidates in {country}",
                    commodity=commodity, country=country,
                    cell_index=cell_index, total_cells=total_cells,
                    found=len(prospects), processed=processed,
                )

                verifications = _verify_batch(llm, chunk)
                ver_map = {
                    v.company_name.strip().lower(): v for v in verifications
                }

                for candidate, _evidence in chunk:
                    verification = ver_map.get(
                        candidate.company_name.strip().lower()
                    )
                    if verification is None:
                        continue

                    merged = _merge(candidate, verification)
                    if not verification.is_relevant:
                        continue

                    key = _dedup_key(merged)
                    if key[0] and key[0] in seen_names:
                        continue
                    if key[1] and key[1] in seen_domains:
                        continue

                    merged.priority = assign_priority(
                        merged.activity,
                        merged.industry,
                        merged.company_size,
                    )
                    merged.commodity = commodity
                    merged.discovery_source = (
                        f"enumeration:{commodity}:{country}"
                    )

                    prospects.append(merged)
                    if key[0]:
                        seen_names.add(key[0])
                    if key[1]:
                        seen_domains.add(key[1])

                    if len(prospects) >= limit:
                        break

                if len(prospects) >= limit:
                    break

            if len(prospects) >= limit:
                break

    _save_cache(prospects, seen_names, seen_domains, profile)
    emit(
        progress, start, "complete",
        f"Discovery complete: {len(prospects)} companies",
        found=len(prospects), processed=processed,
    )
    logger.info(
        "Discovery complete: %d companies found, %d candidates processed in %s",
        len(prospects), processed, format_duration(time.time() - start),
    )
    return prospects[:limit]
