import json
import os
import sqlite3
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from notion_client import Client

from schemas import (
    LeadList,
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
from prompts import (
    MASTER_AGENT_PROMPT,
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

QUALIFIED_SCORE = 2
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


def generate_leads(target_profile):
    """Discover companies matching the user's target profile using LLM's internal knowledge."""
    profile = target_profile.strip() or DEFAULT_PROFILE

    print(f"🌍 Master Agent: Brainstorming companies matching '{profile}' from internal knowledge...")

    try:
        # DDGS araması ve format_search_results tamamen kaldırıldı.
        # Sadece Gemini'nin kendi bilgisi kullanılarak liste isteniyor.
        result = llm.with_structured_output(
            LeadList
        ).invoke(
            MASTER_AGENT_PROMPT.format(
                target_profile=profile
            )
        )

        return _clean_companies(result.companies)

    except Exception as e:
        print(f"Lead generation error: {e}")
        return []


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

    return {
        "contact": result.model_dump()
    }


def copywriter_node(state):
    """Generate or revise the personalized outbound email."""
    print(f"✍️ Copywriter Agent: Drafting email for {state.get('target_company', 'Unknown')}...")
    
    existing_draft = state.get("draft_email") or {}
    feedback = state.get("human_feedback", "").strip()

    # LLM'in boş "{}" objesi görüp halüsinasyon yapmasını engellemek için net yönlendirmeler:
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
                existing_draft=draft_text,      # Boş JSON yerine net metin
                human_feedback=feedback_text,   # Boş JSON yerine net metin
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
    """Create a Notion-safe rich-text value."""
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
    """Create or update the lead's Notion CRM record using the solid V7 logic."""
    print(f"💾 CRM Agent: Saving {state.get('target_company')} to database...")
    
    analysis = state.get("analysis") or {}
    draft = state.get("draft_email") or {}
    company_name = analysis.get("company_name") or state.get("target_company") or "Unknown"

    # V7'deki tıkır tıkır çalışan statik eşleştirme
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

    # Düşük puanlılar artık otomatik CRM'e KAYDEDİLMEYECEK, grafik bitecek.
    return END

def route_after_human(state):
    """Rewrite when feedback exists; otherwise save to CRM."""
    feedback = state.get(
        "human_feedback",
        "",
    )

    if feedback.strip():
        return "Copywriter"

    # Grafik burada duracak. CRM kaydı sadece app_8.py'den manuel yapılacak.
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

app = workflow.compile(
    checkpointer=checkpointer,
    interrupt_before=["Human_Approval"],
)
