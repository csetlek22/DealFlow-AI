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

# --- 1. API Ayarları ---
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", api_key=GOOGLE_API_KEY, temperature=0.0)

# --- YENİ: MASTER AJAN İÇİN ŞEMA VE FONKSİYON ---
class LeadList(BaseModel):
    companies: List[str] = Field(description="Bulunan şirketlerin isimleri")

def generate_leads(prompt: str) -> List[str]:
    """Master Ajan: Kullanıcının promptuna göre interneti tarayıp şirket listesi çıkarır."""
    print(f"🌍 Master Ajan: '{prompt}' için şirket arıyor...")
    try:
        search_results = DDGS().text(prompt, max_results=5)
        context = "\n".join([r.get("body", "") for r in search_results]) if search_results else ""
        
        sys_prompt = f"Sen bir B2B araştırmacısısın. Aşağıdaki arama sonuçlarından hedef profile uyan şirketlerin SADECE İSİMLERİNİ liste olarak çıkar. Eğer şirket yoksa boş liste döndür.\nArama: {prompt}\nSonuçlar:\n{context}"
        
        structured_llm = llm.with_structured_output(LeadList)
        result = structured_llm.invoke(sys_prompt)
        return result.companies
    except Exception as e:
        print(f"Hata: {e}")
        return []

# --- 2. Pydantic Şemaları (Eski Hali) ---
class LeadAnalysis(BaseModel):
    company_name: str = Field(description="Araştırılan şirketin tam adı")
    contact_email: str = Field(description="Şirketin iletişim e-posta adresi")
    fit_score: int = Field(description="İdeal müşteri profiline uyum skoru (1-10)")
    signal: str = Field(description="Tespit edilen acı noktası veya büyüme sinyali")
    rationale: str = Field(description="Bu skorun neden verildiğini açıklayan gerekçe")
    signal_category: str = Field(description="Şirketteki ana sinyal kategorisi.") 

class EmailDraft(BaseModel):
    subject: str = Field(description="E-posta konu başlığı")
    body: str = Field(description="E-posta içeriği")

class AgentState(TypedDict):
    target_company: str           
    research_data: str            
    source_urls: List[str]
    analysis: Optional[dict]      
    draft_email: Optional[dict]   
    human_feedback: Optional[str] 
    final_status: str             

IDEAL_CUSTOMER_PROFILE = """
Sen B2B Operasyon Danışmanlığı satan bir sistemin Analist Ajanısın.
İdeal müşterilerimiz: 20-150 çalışanı olan, üretim veya lojistik şirketleri.
Aranan Sinyaller: Yeni operasyon ilanları, recent büyüme, yeni yöneticiler.
Görev: Şirket araştırmasını oku ve skorla. Sinyal varsa 8-10 arası puan ver.
"""

# --- 5. Ajanlar (Nodes) ---
def researcher_node(state: AgentState) -> dict:
    company = state['target_company']
    print(f"🕵️ Araştırmacı Ajan: {company} için veri topluyor...")
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
            research_text = "İnternette şirket hakkında yeterli veri bulunamadı."
    except Exception as e:
        research_text = f"Hata: {e}."
    return {"research_data": research_text, "source_urls": urls}

def analyst_node(state: AgentState) -> dict:
    print(f"🧠 Analist Ajan: {state['target_company']} verisini inceliyor...")
    research_data = state.get('research_data', '')
    if not research_data or len(research_data) < 10:
        research_data = "Yeterli veri bulunamadı."

    prompt = f"{IDEAL_CUSTOMER_PROFILE}\nŞirket: {state['target_company']}\nVeri: {research_data}\nSen bir API'sin, sohbete girme."
    result = llm.with_structured_output(LeadAnalysis).invoke(prompt)
    return {"analysis": result.model_dump(), "final_status": "Pending Analysis"}

def copywriter_node(state: AgentState) -> dict:
    print(f"✍️ Yazar Ajan: {state['target_company']} için taslak oluşturuyor...")
    
    # 1. Eski taslağı alıyoruz ki ajan "neyi" düzelteceğini bilsin
    current_draft = ""
    if state.get("draft_email") and state["draft_email"].get("body"):
        current_draft = f"\n--- MEVCUT E-POSTA TASLAĞI ---\n{state['draft_email']['body']}\n------------------------------\n"
        
    # 2. Geri bildirim varsa, ajana "mevcut taslağı düzelt" diyoruz
    feedback_context = f"\nKULLANICI GERİ BİLDİRİMİ: '{state['human_feedback']}'\nGörev: Yukarıdaki mevcut e-posta taslağını bu geri bildirime göre YENİDEN YAZ." if state.get('human_feedback') else ""
    
    prompt = f"""
    Sen tecrübeli bir B2B danışmanısın. Aşağıdaki şirket analizini kullanarak bir tanışma e-postası (cold email) yaz.
    
    Şirket: {state['analysis']['company_name']}
    Tespit Edilen Sinyal: {state['analysis']['signal']}
    {current_draft}
    {feedback_context}
    
    Kurallar: 
    1. "Umarım iyisinizdir" gibi klişeler kullanma. Doğrudan konuya gir.
    2. Analizdeki 'Sinyal'e doğrudan atıfta bulun.
    3. Sadece e-posta içeriğini tool formatında döndür.
    """
    
    result = llm.with_structured_output(EmailDraft).invoke(prompt)
    return {"draft_email": result.model_dump(), "human_feedback": None}

def crm_node(state: AgentState) -> dict:
    print(f"💾 CRM Ajanı: {state['target_company']} kaydediliyor...")
    notion = Client(auth=NOTION_TOKEN)
    if not state.get("analysis"): return {"final_status": "Failed"}

    try:
        analysis = state["analysis"]
        draft_body = state.get("draft_email", {}).get("body", "")
        new_page_props = {
            "Company": {"title": [{"text": {"content": analysis.get("company_name", "Bilinmiyor")}}]},
            "Fit Score": {"number": int(analysis.get("fit_score", 0))},
            "Signal": {"rich_text": [{"text": {"content": analysis.get("signal", "")[:2000]}}]},
            "Rationale": {"rich_text": [{"text": {"content": analysis.get("rationale", "")[:2000]}}]},
            "Status": {"select": {"name": "CRM'e Eklendi"}}
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