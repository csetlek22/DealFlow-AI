import os
from dotenv import load_dotenv
from typing import TypedDict, Annotated, List, Optional
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from ddgs import DDGS
from notion_client import Client
import sqlite3

# --- 1. API Settings ---
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", api_key=GOOGLE_API_KEY, temperature=0.0)

# --- NEW: SCHEMA AND FUNCTION FOR MASTER AGENT ---
class LeadList(BaseModel):
    companies: List[str] = Field(description="List of discovered company names")

def generate_leads(prompt: str) -> List[str]:
    """Master Agent: Scrapes the internet based on the user's prompt and extracts a list of relevant companies."""
    print(f"🌍 Master Agent: Searching for companies matching '{prompt}'...")
    try:
        search_results = DDGS().text(prompt, max_results=5)
        context = "\n".join([r.get("body", "") for r in search_results]) if search_results else ""
        
        sys_prompt = f"You are a B2B researcher. Based on the search results below, extract ONLY THE NAMES of the companies that match the target profile as a list. If no companies match, return an empty list.\nSearch: {prompt}\nResults:\n{context}"
        
        structured_llm = llm.with_structured_output(LeadList)
        result = structured_llm.invoke(sys_prompt)
        return result.companies
    except Exception as e:
        print(f"Error: {e}")
        return []

# --- 2. Pydantic Schemas (Data Guardrails) ---
class LeadAnalysis(BaseModel):
    company_name: str = Field(description="Full name of the researched company")
    contact_email: str = Field(description="Contact email address of the company")
    fit_score: int = Field(description="Fit score matching the ideal customer profile (1-10)")
    signal: str = Field(description="Detected pain point or growth signal")
    rationale: str = Field(description="Rationale explaining why this score was assigned")
    signal_category: str = Field(description="Main signal category associated with the company") 

class EmailDraft(BaseModel):
    subject: str = Field(description="Email subject line")
    body: str = Field(description="Email body content")

class AgentState(TypedDict):
    target_company: str           
    research_data: str            
    source_urls: List[str]
    analysis: Optional[dict]      
    draft_email: Optional[dict]   
    human_feedback: Optional[str] 
    final_status: str             

IDEAL_CUSTOMER_PROFILE = """
You are an Analyst Agent for a system selling B2B Operations Consulting.
Ideal customers: Manufacturing or logistics companies with 20-150 employees.
Target Signals (Pain Signals): New operations/supply chain job postings, recent growth, new executives.
Task: Read the company research and score it. Assign 8-10 points if a signal is found.
"""

# --- 5. Agents (Nodes) ---
def researcher_node(state: AgentState) -> dict:
    company = state['target_company']
    print(f"🕵️ Researcher Agent: Collecting data for {company}...")
    query = f"{company} company news recent developments hiring"
    urls = []
    research_text = "" 
    
    try:
        results = DDGS().text(query, max_results=5)
        if results:
            for r in results:
                research_text += f"{r.get('body', '')}\n\n"
                if r.get('href'):
                    urls.append(r.get('href'))
        else:
            research_text = "Not enough data found about the company on the internet."
    except Exception as e:
        research_text = f"Error: {e}."
    return {"research_data": research_text, "source_urls": urls}

def analyst_node(state: AgentState) -> dict:
    print(f"🧠 Analyst Agent: Analyzing data for {state['target_company']}...")
    research_data = state.get('research_data', '')
    if not research_data or len(research_data) < 10:
        research_data = "Insufficient data found."

    prompt = f"{IDEAL_CUSTOMER_PROFILE}\nCompany: {state['target_company']}\nData: {research_data}\nYou are an API, do not use conversational text."
    result = llm.with_structured_output(LeadAnalysis).invoke(prompt)
    return {"analysis": result.model_dump(), "final_status": "Pending Analysis"}

def copywriter_node(state: AgentState) -> dict:
    print(f"✍️ Copywriter Agent: Drafting email for {state['target_company']}...")
    
    # 1. Retrieve the existing draft to provide context for revisions
    current_draft = ""
    if state.get("draft_email") and state["draft_email"].get("body"):
        current_draft = f"\n--- CURRENT EMAIL DRAFT ---\n{state['draft_email']['body']}\n------------------------------\n"
        
    # 2. Instruct the LLM to rewrite the existing draft based on feedback
    feedback_context = f"\nUSER FEEDBACK: '{state['human_feedback']}'\nTask: REWRITE the current email draft above according to this feedback." if state.get('human_feedback') else ""
    
    prompt = f"""
    You are an experienced B2B consultant. Write a cold email using the company analysis below.
    
    Company: {state['analysis']['company_name']}
    Detected Signal: {state['analysis']['signal']}
    {current_draft}
    {feedback_context}
    
    Rules: 
    1. Avoid clichés like "I hope this email finds you well". Get straight to the point.
    2. Directly reference the 'Signal' identified in the analysis.
    3. Return only the email content in the designated tool format. Do not use conversational filler.
    """
    
    result = llm.with_structured_output(EmailDraft).invoke(prompt)
    return {"draft_email": result.model_dump(), "human_feedback": None}

def crm_node(state: AgentState) -> dict:
    print(f"💾 CRM Agent: Saving {state['target_company']} to database...")
    notion = Client(auth=NOTION_TOKEN)
    if not state.get("analysis"): return {"final_status": "Failed"}

    try:
        analysis = state["analysis"]
        draft_body = state.get("draft_email", {}).get("body", "")
        new_page_props = {
            "Company": {"title": [{"text": {"content": analysis.get("company_name", "Unknown")}}]},
            "Fit Score": {"number": int(analysis.get("fit_score", 0))},
            "Signal": {"rich_text": [{"text": {"content": analysis.get("signal", "")[:2000]}}]},
            "Rationale": {"rich_text": [{"text": {"content": analysis.get("rationale", "")[:2000]}}]},
            "Status": {"select": {"name": "Added to CRM"}}
        }

        if draft_body:
            new_page_props["Draft Email"] = {"rich_text": [{"text": {"content": draft_body[:120].strip() + "..."}}]}

        response = notion.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=new_page_props)
        page_id = response["id"]

        if draft_body:
            email_blocks = [
                {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "📧 Draft Email"}}]}},
                {"object": "block", "type": "divider", "divider": {}}
            ]
            for i in range(0, len(draft_body), 1900):
                email_blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": draft_body[i:i+1900]}}]}})
            notion.blocks.children.append(block_id=page_id, children=email_blocks)

        return {"final_status": "Saved to CRM"}
    except Exception as e:
        return {"final_status": f"Error: {str(e)}"}

def route_after_analysis(state: AgentState) -> str:
    if state['analysis']['fit_score'] >= 7: return "write_email"
    return "save_to_crm"

def route_after_human(state: AgentState) -> str:
    if state.get("human_feedback"): return "rewrite"
    return "proceed_to_crm"

workflow = StateGraph(AgentState)
workflow.add_node("Researcher", researcher_node)
workflow.add_node("Analyst", analyst_node)
workflow.add_node("Copywriter", copywriter_node)
workflow.add_node("CRM_Updater", crm_node)
workflow.add_node("Human_Approval", lambda state: state) 

workflow.add_edge(START, "Researcher")
workflow.add_edge("Researcher", "Analyst")
workflow.add_conditional_edges("Analyst", route_after_analysis, {"write_email": "Copywriter", "save_to_crm": "CRM_Updater"})
workflow.add_edge("Copywriter", "Human_Approval")
workflow.add_conditional_edges("Human_Approval", route_after_human, {"rewrite": "Copywriter", "proceed_to_crm": "CRM_Updater"})
workflow.add_edge("CRM_Updater", END)

conn = sqlite3.connect("state.db", check_same_thread=False)
memory = SqliteSaver(conn)
app = workflow.compile(checkpointer=memory, interrupt_before=["Human_Approval"])