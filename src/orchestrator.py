import os
import json
import sqlite3
from typing import TypedDict

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from notion_client import Client

from schemas import LeadList, CompanyResearch, LeadAnalysis, ContactResearch, EmailDraft
from research import search_target_companies, search_company, search_contact, format_search_results, get_source_urls
from prompts import MASTER_AGENT_PROMPT, RESEARCHER_PROMPT, ANALYST_PROMPT, CONTACT_RESEARCHER_PROMPT, COPYWRITER_PROMPT


load_dotenv()

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

QUALIFIED_SCORE = 7
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
    return json.dumps(data or {}, ensure_ascii=False, indent=2)


def _clean_companies(companies):
    """Remove duplicate or empty company names."""
    seen, result = set(), []

    for company in companies or []:
        company = company.strip() if company else ""
        key = company.lower()

        if company and len(company) > 1 and key not in seen:
            seen.add(key)
            result.append(company)

    return result[:MAX_COMPANIES]


def generate_leads(target_profile):
    """Discover companies matching the user's target profile."""
    profile = target_profile.strip() or DEFAULT_PROFILE
    results = search_target_companies(profile, max_results_per_query=5)
    context = format_search_results(results, max_chars=18000)

    if not context:
        return []

    try:
        result = llm.with_structured_output(LeadList).invoke(
            MASTER_AGENT_PROMPT.format(
                target_profile=profile,
                research_context=context,
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

    results = search_company(company, max_results_per_query=5)
    context = format_search_results(results, max_chars=22000)

    if not context:
        context = f"No reliable search results were found for {company}."

    try:
        research = llm.with_structured_output(CompanyResearch).invoke(
            RESEARCHER_PROMPT.format(
                company=company,
                target_profile=profile,
                research_context=context[:20000],
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
        result = llm.with_structured_output(LeadAnalysis).invoke(
            ANALYST_PROMPT.format(
                company=company,
                target_profile=profile,
                company_research=_json(state.get("company_research")),
                raw_evidence=state.get("research_data", "")[:12000],
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

    return {"analysis": result.model_dump()}


def contact_researcher_node(state):
    """Find a relevant decision-maker using public evidence."""
    company = state["target_company"]

    results = search_contact(
        company,
        analysis=state.get("analysis", {}),
        max_results_per_query=5,
    )
    context = format_search_results(results, max_chars=14000)

    if not context:
        context = "No reliable public contact information was found."

    try:
        result = llm.with_structured_output(ContactResearch).invoke(
            CONTACT_RESEARCHER_PROMPT.format(
                company=company,
                target_profile=state.get("target_profile") or DEFAULT_PROFILE,
                analysis=_json(state.get("analysis")),
                research_context=context,
            )
        )
    except Exception as e:
        print(f"Contact research error for {company}: {e}")
        result = ContactResearch(
            rationale="No verified contact was found.",
            confidence="low",
        )

    return {"contact": result.model_dump()}


def copywriter_node(state):
    """Generate or revise the personalized outbound email."""
    try:
        result = llm.with_structured_output(EmailDraft).invoke(
            COPYWRITER_PROMPT.format(
                company=state["target_company"],
                target_profile=state.get("target_profile") or DEFAULT_PROFILE,
                analysis=_json(state.get("analysis")),
                contact=_json(state.get("contact")),
                company_research=_json(state.get("company_research")),
                existing_draft=_json(state.get("draft_email")),
                human_feedback=state.get("human_feedback", ""),
            )
        )
    except Exception as e:
        print(f"Copywriter error: {e}")
        analysis = state.get("analysis", {})
        signal = analysis.get("signal", "")

        result = EmailDraft(
            subject=f"Operational opportunity at {state['target_company']}",
            body=(
                f"Hello,\n\n"
                f"I noticed that {state['target_company']} {signal.lower()}. "
                "This may create an opportunity to improve operational efficiency "
                "and scalability.\n\n"
                "I would be happy to share a few relevant observations.\n\n"
                "Best,"
            ),
        )

    return {"draft_email": result.model_dump(), "human_feedback": ""}


def _schema():
    """Retrieve the current Notion database schema."""
    try:
        return notion.databases.retrieve(
            database_id=NOTION_DATABASE_ID
        ).get("properties", {})
    except Exception as e:
        print(f"Notion schema error: {e}")
        return {}


def _rt(value, limit=1900):
    """Create a Notion-safe rich-text value."""
    return [{"type": "text", "text": {"content": str(value or "")[:limit]}}]


def _notion_properties(state):
    """Build only properties that exist in the connected database."""
    schema = _schema()
    analysis = state.get("analysis") or {}
    contact = state.get("contact") or {}
    draft = state.get("draft_email") or {}

    company = analysis.get("company_name") or state.get("target_company", "")
    email = f"{draft.get('subject', '')}\n\n{draft.get('body', '')}"
    properties = {}

    if schema.get("Company", {}).get("type") == "title":
        properties["Company"] = {"title": _rt(company)}

    if schema.get("Fit Score", {}).get("type") == "number":
        properties["Fit Score"] = {"number": analysis.get("fit_score", 0)}

    if schema.get("Signal", {}).get("type") == "rich_text":
        properties["Signal"] = {"rich_text": _rt(analysis.get("signal"))}

    if schema.get("Rationale", {}).get("type") == "rich_text":
        properties["Rationale"] = {"rich_text": _rt(analysis.get("rationale"))}

    if schema.get("Draft Email", {}).get("type") == "rich_text" and draft:
        properties["Draft Email"] = {"rich_text": _rt(email)}

    if schema.get("Status", {}).get("type") == "select":
        options = schema["Status"]["select"].get("options", [])
        names = {x["name"] for x in options}
        status = "Added to CRM" if "Added to CRM" in names else (
            "CRM'e Eklendi" if "CRM'e Eklendi" in names else None
        )
        if status:
            properties["Status"] = {"select": {"name": status}}

    if schema.get("Contact Name", {}).get("type") == "rich_text" and contact.get("contact_name"):
        properties["Contact Name"] = {"rich_text": _rt(contact["contact_name"])}

    if schema.get("Contact Email", {}).get("type") == "email" and contact.get("contact_email"):
        properties["Contact Email"] = {"email": contact["contact_email"]}

    return properties


def _blocks(state, updated=False):
    """Build the detailed CRM page body."""
    contact = state.get("contact") or {}
    draft = state.get("draft_email") or {}
    blocks = []

    if any(contact.get(k) for k in ("contact_name", "title", "contact_email")):
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rt("Decision Maker")},
        })

        text = "\n".join(
            f"{label}: {contact[key]}"
            for key, label in (
                ("contact_name", "Name"),
                ("title", "Role"),
                ("contact_email", "Email"),
            )
            if contact.get(key)
        )

        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rt(text)},
        })

    if draft:
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": _rt("Updated Draft Email" if updated else "Draft Email")
            },
        })

        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rt(f"Subject: {draft.get('subject', '')}")},
        })

        body = draft.get("body", "")
        for i in range(0, len(body), 1800):
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rt(body[i:i + 1800])},
            })

    for url in state.get("source_urls", [])[:25]:
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": url, "link": {"url": url}},
                }]
            },
        })

    return blocks


def crm_node(state):
    """Create or update the lead's Notion CRM record."""
    properties = _notion_properties(state)
    page_id = state.get("crm_page_id")

    if not properties:
        print("CRM warning: no matching Notion properties were found.")
        return {"final_status": "CRM save failed"}

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
                children=_blocks(state),
            )
            page_id = response["id"]

        return {"crm_page_id": page_id, "final_status": "Saved to CRM"}
    except Exception as e:
        print(f"CRM error: {e}")
        return {"final_status": "CRM save failed"}


def route_after_analysis(state):
    """Send qualified leads to contact research; save weaker leads directly."""
    score = state.get("analysis", {}).get("fit_score", 0)
    return "ContactResearcher" if score >= QUALIFIED_SCORE else "CRM_Updater"


def route_after_human(state):
    """Rewrite when feedback exists; otherwise save the approved email."""
    return "Copywriter" if state.get("human_feedback", "").strip() else "CRM_Updater"


workflow = StateGraph(AgentState)

workflow.add_node("Researcher", researcher_node)
workflow.add_node("Analyst", analyst_node)
workflow.add_node("ContactResearcher", contact_researcher_node)
workflow.add_node("Copywriter", copywriter_node)
workflow.add_node("Human_Approval", lambda state: state)
workflow.add_node("CRM_Updater", crm_node)

workflow.set_entry_point("Researcher")
workflow.add_edge("Researcher", "Analyst")

workflow.add_conditional_edges(
    "Analyst",
    route_after_analysis,
    {
        "ContactResearcher": "ContactResearcher",
        "CRM_Updater": "CRM_Updater",
    },
)

workflow.add_edge("ContactResearcher", "Copywriter")
workflow.add_edge("Copywriter", "Human_Approval")

workflow.add_conditional_edges(
    "Human_Approval",
    route_after_human,
    {
        "Copywriter": "Copywriter",
        "CRM_Updater": "CRM_Updater",
    },
)

workflow.add_edge("CRM_Updater", END)

connection = sqlite3.connect("state.db", check_same_thread=False)
checkpointer = SqliteSaver(connection)

app = workflow.compile(
    checkpointer=checkpointer,
    interrupt_before=["Human_Approval"],
)