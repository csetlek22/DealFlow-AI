import json
import os
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from notion_client import Client

from schemas import (
    CompanyCandidateList,
    CompanyResearch,
    LeadAnalysis,
    ContactResearch,
    EmailDraft,
)
from research import (
    search_target_companies,
    search_company,
    search_contact,
    format_search_results,
    get_source_urls,
)
from hunter import enrich_email, _extract_domain
from prompts import (
    DISCOVERY_PROMPT,
    QUALIFICATION_PROMPT,
    RESEARCHER_PROMPT,
    ANALYST_PROMPT,
    CONTACT_RESEARCHER_PROMPT,
    COPYWRITER_PROMPT,
)


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
DB_FILE = BASE_DIR / "state.db"

load_dotenv(ENV_FILE)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY is missing.")

if not NOTION_TOKEN:
    raise ValueError("NOTION_TOKEN is missing.")

if not NOTION_DATABASE_ID:
    raise ValueError("NOTION_DATABASE_ID is missing.")


llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0,
    google_api_key=GOOGLE_API_KEY,
)

notion = Client(auth=NOTION_TOKEN)

# Companies with a fit_score at or above this value are considered qualified.
QUALIFIED_SCORE = 6
MAX_COMPANIES = 12

DEFAULT_PROFILE = """
B2B operational consulting.
Ideal customers are manufacturing or logistics companies with approximately
20–150 employees showing growth, hiring, expansion, supply-chain activity,
new leadership, increased capacity or operational transformation.
Prioritize recent, verifiable signals indicating potential consulting need.
""".strip()


class AgentState(TypedDict, total=False):
    """State shared across the lead-processing graph."""

    target_profile: str
    target_company: str
    research_data: str
    source_urls: list[str]
    company_research: dict
    analysis: dict
    contact: dict
    draft_email: dict
    human_feedback: str
    approved: bool
    crm_page_id: str
    final_status: str


def _json(data):
    """Serialize structured state for LLM prompts."""
    return json.dumps(
        data or {},
        ensure_ascii=False,
        indent=2,
    )


def _clean_companies(companies):
    """Remove duplicate or empty company names."""
    seen = set()
    result = []

    for company in companies or []:
        company = company.strip() if company else ""

        if not company:
            continue

        key = company.lower()

        if key not in seen:
            seen.add(key)
            result.append(company)

    return result[:MAX_COMPANIES]


def _clean_candidates(candidates, limit=25):
    """Deduplicate candidate companies by name."""
    seen = set()
    result = []

    for candidate in candidates or []:
        name = (candidate.company_name or "").strip()

        if not name:
            continue

        key = name.lower()

        if key not in seen:
            seen.add(key)
            result.append(candidate)

    return result[:limit]


def _normalize_company_name(value):
    """Normalize a name for exact duplicate matching.

    Lowercasing and stripping punctuation collapse formatting differences
    (case, commas, periods) so the same company written differently still
    matches. Matching is intentionally exact rather than fuzzy to avoid false
    positives against the CRM.
    """
    value = (value or "").strip().lower()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def get_existing_crm_companies():
    """Return (names, domains) already present in the Notion CRM database."""
    if not NOTION_DATABASE_ID:
        raise ValueError("NOTION_DATABASE_ID is missing.")

    # notion-client 3.x removed ``databases.query``; CRM pages are read from
    # the database's linked data source instead.
    database = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
    data_sources = database.get("data_sources") or []

    if not data_sources:
        raise ValueError("Notion database has no linked data source.")

    names = set()
    domains = set()
    cursor = None

    while True:
        params = {
            "data_source_id": data_sources[0]["id"],
            "page_size": 100,
        }

        if cursor:
            params["start_cursor"] = cursor

        response = notion.data_sources.query(**params)

        for page in response.get("results", []):
            properties = page.get("properties") or {}

            title_blocks = (properties.get("Company") or {}).get("title") or []

            name = "".join(
                block.get("plain_text", "")
                for block in title_blocks
            ).strip()

            if name:
                names.add(name)

                # A name that is itself a URL/domain is also a domain hint.
                name_domain = _extract_domain(name)

                if (
                    name_domain
                    and "." in name_domain
                    and " " not in name_domain
                ):
                    domains.add(name_domain)

            # "Contact Email" is the only domain-bearing property the CRM stores.
            email = (properties.get("Contact Email") or {}).get("email")

            if email and "@" in email:
                email_domain = email.rsplit("@", 1)[-1].lower().strip()

                if (
                    email_domain
                    and "." in email_domain
                    and " " not in email_domain
                ):
                    domains.add(email_domain)

        if not response.get("has_more"):
            break

        cursor = response.get("next_cursor")

        if not cursor:
            break

    return names, domains


def _filter_existing(candidates, existing_names, existing_domains):
    """Split candidates into new (kept) and already-in-CRM (excluded).

    An exact normalized name match is the primary duplicate signal; a domain
    match is a secondary check that catches renames and alternate spellings.
    """
    existing = {
        _normalize_company_name(name)
        for name in existing_names
    }

    kept = []
    excluded = []

    for candidate in candidates:
        name_key = _normalize_company_name(candidate.company_name)
        domain = _extract_domain(candidate.website) if candidate.website else ""

        if (
            (name_key and name_key in existing)
            or (domain and domain in existing_domains)
        ):
            excluded.append(candidate.company_name)
        else:
            kept.append(candidate)

    return kept, excluded


def generate_leads(target_profile):
    """Discover candidates via Tavily, filter out CRM duplicates, then qualify."""
    profile = target_profile.strip() or DEFAULT_PROFILE

    # Load existing CRM companies once. If we can't read them, fail safe (skip
    # the batch) so we never risk surfacing a company already in the CRM.
    try:
        existing_names, existing_domains = get_existing_crm_companies()
    except Exception as e:
        print(
            f"Notion CRM lookup failed: {type(e).__name__}: {e} — "
            "skipping batch (fail-safe)."
        )
        return []

    existing_set = {
        _normalize_company_name(name)
        for name in existing_names
    }

    print(f"Existing CRM companies loaded: {len(existing_names)}")

    print("Master Agent: Discovering candidate companies via Tavily...")

    results = search_target_companies(
        profile,
        max_results_per_query=8,
    )

    context = format_search_results(
        results,
        max_chars=40000,
    )

    if not context.strip():
        print("Master Agent: no search results found.")
        return []

    try:
        discovered = llm.with_structured_output(
            CompanyCandidateList
        ).invoke(
            DISCOVERY_PROMPT.format(
                target_profile=profile,
                search_results=context,
            )
        )

        candidates = _clean_candidates(
            discovered.candidates,
            limit=25,
        )

    except Exception as e:
        print(f"Discovery error: {e}")
        return []

    print(f"Discovery candidates: {len(candidates)}")

    if not candidates:
        print("Master Agent: no candidates discovered.")
        return []

    candidates, excluded = _filter_existing(
        candidates,
        existing_names,
        existing_domains,
    )

    if excluded:
        preview = ", ".join(excluded[:5])
        suffix = "..." if len(excluded) > 5 else ""
        print(f"Excluded as existing ({len(excluded)}): {preview}{suffix}")

    if not candidates:
        print("Master Agent: all candidates already exist in CRM.")
        return []

    print(f"Candidates sent to Gemini: {len(candidates)}")

    try:
        qualified = llm.with_structured_output(
            CompanyCandidateList
        ).invoke(
            QUALIFICATION_PROMPT.format(
                target_profile=profile,
                candidates=_json(
                    [candidate.model_dump() for candidate in candidates]
                ),
            )
        )

        companies = [
            candidate.company_name
            for candidate in qualified.candidates
        ]

    except Exception as e:
        print(f"Qualification error: {e}")
        companies = [
            candidate.company_name
            for candidate in candidates[:10]
        ]

    companies = _clean_companies(companies)

    # Final safety check: never return a company already in the CRM.
    companies = [
        company
        for company in companies
        if _normalize_company_name(company) not in existing_set
    ]

    print(f"Qualified companies: {len(companies)}")
    return companies


def researcher_node(state):
    """Collect multi-source evidence and create structured company research."""
    company = state["target_company"]
    profile = state.get("target_profile") or DEFAULT_PROFILE

    results = search_company(
        company,
        max_results_per_query=4,
    )

    context = format_search_results(
        results,
        max_chars=18000,
    )

    if not context:
        context = f"No reliable search results were found for {company}."

    try:
        research = llm.with_structured_output(
            CompanyResearch
        ).invoke(
            RESEARCHER_PROMPT.format(
                company=company,
                target_profile=profile,
                research_context=context,
            )
        )

        structured = research.model_dump()

    except Exception as e:
        print(f"Research error for {company}: {e}")

        structured = CompanyResearch(
            company_name=company,
            company_summary="Research synthesis failed.",
            evidence_quality="low",
        ).model_dump()

    return {
        "research_data": context,
        "source_urls": get_source_urls(results),
        "company_research": structured,
    }


def analyst_node(state):
    """Score the company against ICP, need, recency and signal strength."""
    company = state["target_company"]
    profile = state.get("target_profile") or DEFAULT_PROFILE

    try:
        result = llm.with_structured_output(
            LeadAnalysis
        ).invoke(
            ANALYST_PROMPT.format(
                company=company,
                target_profile=profile,
                company_research=_json(
                    state.get("company_research")
                ),
                raw_evidence=state.get(
                    "research_data",
                    "",
                )[:12000],
            )
        )

    except Exception as e:
        print(f"Analysis error for {company}: {e}")

        result = LeadAnalysis(
            company_name=company,
            fit_score=0,
            icp_score=0,
            business_need_score=0,
            recency_score=0,
            signal_strength_score=0,
            signal="Insufficient evidence",
            rationale="Lead analysis failed.",
            signal_category="unknown",
            confidence="low",
        )

    return {
        "analysis": result.model_dump()
    }


def _hunter_enrich(state):
    """Derive a company domain and enrich with Hunter if configured."""
    research = state.get("company_research") or {}
    domain = _extract_domain(research.get("website"))

    if not domain:
        for url in state.get("source_urls") or []:
            domain = _extract_domain(url)
            if domain:
                break

    if not domain:
        return None

    return enrich_email(domain)


def contact_researcher_node(state):
    """Find a relevant decision-maker using public evidence."""
    company = state["target_company"]

    results = search_contact(
        company,
        analysis=state.get("analysis") or {},
        max_results_per_query=3,
    )

    context = format_search_results(
        results,
        max_chars=12000,
    )

    if not context:
        context = "No reliable public contact information was found."

    try:
        result = llm.with_structured_output(
            ContactResearch
        ).invoke(
            CONTACT_RESEARCHER_PROMPT.format(
                company=company,
                target_profile=(
                    state.get("target_profile")
                    or DEFAULT_PROFILE
                ),
                analysis=_json(
                    state.get("analysis")
                ),
                research_context=context,
            )
        )

    except Exception as e:
        print(f"Contact research error for {company}: {e}")

        result = ContactResearch(
            rationale="No verified contact was found.",
            confidence="low",
        )

    contact = result.model_dump()

    # Hunter email enrichment as an additional verified-email provider.
    hunter = _hunter_enrich(state)

    if hunter:
        if hunter.get("contact_email"):
            contact["contact_email"] = hunter["contact_email"]
            contact["email_source"] = hunter.get("email_source", "hunter")
            contact["provider"] = "hunter"

        if hunter.get("contact_name") and not contact.get("contact_name"):
            contact["contact_name"] = hunter["contact_name"]

        if hunter.get("title") and not contact.get("title"):
            contact["title"] = hunter["title"]

        print(
            f"Hunter: enriched contact for {company} "
            f"({hunter.get('contact_email') or 'no email'})"
        )
    else:
        print(f"Hunter: no enrichment available for {company}")

    return {
        "contact": contact
    }


def copywriter_node(state):
    """Generate or revise the personalized outbound email."""
    print(f"Copywriter Agent: Drafting email for {state.get('target_company', 'Unknown')}...")
    
    existing_draft = state.get("draft_email") or {}
    feedback = state.get("human_feedback", "").strip()

    # Use explicit prose instead of an empty dict so the model writes a fresh
    # draft rather than hallucinating content.
    draft_text = "No existing draft. Write a new email from scratch."
    if existing_draft.get("body"):
        draft_text = f"Subject: {existing_draft.get('subject', '')}\nBody: {existing_draft.get('body', '')}"

    feedback_text = feedback if feedback else "No feedback. Write the initial draft."

    try:
        result = llm.with_structured_output(
            EmailDraft
        ).invoke(
            COPYWRITER_PROMPT.format(
                company=state["target_company"],
                target_profile=(
                    state.get("target_profile")
                    or DEFAULT_PROFILE
                ),
                analysis=_json(state.get("analysis")),
                contact=_json(state.get("contact")),
                company_research=_json(state.get("company_research")),
                existing_draft=draft_text,
                human_feedback=feedback_text,
            )
        )

    except Exception as e:
        print(f"Copywriter error: {e}")
        analysis = state.get("analysis") or {}
        signal = analysis.get("signal", "a recent operational development")

        result = EmailDraft(
            subject=f"Operational opportunity at {state['target_company']}",
            body=(
                f"Hello,\n\n"
                f"I came across {state['target_company']} while researching the market and noticed {signal.lower()}.\n\n"
                f"This may create an opportunity to improve operational efficiency as the business scales. I'd be happy to share a few relevant observations.\n\n"
                f"Best,"
            ),
        )

    return {
        "draft_email": result.model_dump(),
        "human_feedback": "",
    }



def _rt(value, limit=1900):
    """Return a Notion rich-text value, truncated to avoid the block-size cap."""
    value = str(value or "")
    return [{"text": {"content": value[:limit]}}]

def _blocks(state, updated=False):
    """Build the detailed CRM page body (Contact Info, Draft, Sources)."""
    contact = state.get("contact") or {}
    draft = state.get("draft_email") or {}
    blocks = []

    if any(contact.get(key) for key in ("contact_name", "title", "contact_email")):
        blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rt("Decision Maker")}})
        text = "\n".join(f"{label}: {contact[key]}" for key, label in (("contact_name", "Name"), ("title", "Role"), ("contact_email", "Email")) if contact.get(key))
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text)}})

    if draft:
        blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rt("Updated Draft Email" if updated else "Draft Email")}})
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(f"Subject: {draft.get('subject', '')}")}})
        body = draft.get("body", "")
        for i in range(0, len(body), 1800):
            blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(body[i:i + 1800])}})

    for url in (state.get("source_urls") or [])[:20]:
        if not url: continue
        blocks.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": url, "link": {"url": url}}}]}})

    return blocks

def crm_node(state):
    """Create or update the lead's Notion CRM record."""
    print(f"CRM Agent: Saving {state.get('target_company')} to database...")
    
    analysis = state.get("analysis") or {}
    draft = state.get("draft_email") or {}
    company_name = analysis.get("company_name") or state.get("target_company") or "Unknown"

    # Map lead fields to the Notion database properties.
    properties = {
        "Company": {"title": _rt(company_name)},
        "Fit Score": {"number": int(analysis.get("fit_score", 0))},
        "Signal": {"rich_text": _rt(analysis.get("signal", ""))},
        "Rationale": {"rich_text": _rt(analysis.get("rationale", ""))},
        "Status": {"select": {"name": "Added to CRM"}} 
    }

    if draft:
        preview_body = f"{draft.get('subject', '')}\n\n{draft.get('body', '')}"
        properties["Draft Email"] = {"rich_text": _rt(preview_body)}

    page_id = state.get("crm_page_id")

    try:
        if page_id:
            notion.pages.update(page_id=page_id, properties=properties)
            blocks = _blocks(state, updated=True)
            if blocks:
                notion.blocks.children.append(block_id=page_id, children=blocks)
        else:
            response = notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties=properties,
                children=_blocks(state)
            )
            page_id = response["id"]

        return {"crm_page_id": page_id, "final_status": "Saved to CRM"}

    except Exception as e:
        print(f"CRM error: {e}")
        return {"final_status": f"CRM save failed: {e}"}


def route_after_analysis(state):
    """Route qualified leads to contact research."""
    analysis = state.get("analysis") or {}
    score = analysis.get("fit_score", 0)

    if score >= QUALIFIED_SCORE:
        return "ContactResearcher"

    # Leads below the threshold stop here; nothing is written to the CRM.
    return END

def route_after_human(state):
    """Return to the copywriter when feedback exists; otherwise end the run."""
    feedback = state.get(
        "human_feedback",
        "",
    )

    if feedback.strip():
        return "Copywriter"

    # Stop here; the CRM write is a manual action in the UI after approval.
    return END


workflow = StateGraph(AgentState)

workflow.add_node(
    "Researcher",
    researcher_node,
)

workflow.add_node(
    "Analyst",
    analyst_node,
)

workflow.add_node(
    "ContactResearcher",
    contact_researcher_node,
)

workflow.add_node(
    "Copywriter",
    copywriter_node,
)

workflow.add_node(
    "Human_Approval",
    lambda state: state,
)

workflow.add_node(
    "CRM_Updater",
    crm_node,
)

workflow.set_entry_point("Researcher")

workflow.add_edge(
    "Researcher",
    "Analyst",
)

workflow.add_conditional_edges(
    "Analyst",
    route_after_analysis,
    {
        "ContactResearcher": "ContactResearcher",
        END: END,
    },
)

workflow.add_edge(
    "ContactResearcher",
    "Copywriter",
)

workflow.add_edge(
    "Copywriter",
    "Human_Approval",
)

workflow.add_conditional_edges(
    "Human_Approval",
    route_after_human,
    {
        "Copywriter": "Copywriter",
        END: END,
    },
)

workflow.add_edge(
    "CRM_Updater",
    END,
)


connection = sqlite3.connect(
    DB_FILE,
    check_same_thread=False,
)

checkpointer = SqliteSaver(connection)

# Interrupt before Human_Approval so nothing reaches the CRM or email until a
# human explicitly approves the lead in the UI.
app = workflow.compile(
    checkpointer=checkpointer,
    interrupt_before=["Human_Approval"],
)
