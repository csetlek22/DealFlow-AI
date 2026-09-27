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

# --- 1. API Ayarları (Ortam Değişkenlerinden Alınır) ---

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# GEMINI MODELİNİ BAŞLATIYORUZ
llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite", 
    api_key=GOOGLE_API_KEY,
    temperature=0.0 # Halüsinasyon sıfır
)

# --- 2. Pydantic Şemaları (Data Contracts / Guardrails) ---
# Bu kısım çok önemlidir. Ajanın JSON üretmesini garanti altına alır.
class LeadAnalysis(BaseModel):
    company_name: str = Field(description="Araştırılan şirketin tam adı")
    contact_email: str = Field(description="Şirketin iletişim e-posta adresi (İnternette bulunamazsa info@sirket.com veya ik@sirket.com şeklinde tahmin et)")
    fit_score: int = Field(description="İdeal müşteri profiline uyum skoru (1-10 arası)")
    signal: str = Field(description="Tespit edilen acı noktası veya büyüme sinyali (Örn: Yeni iş ilanı, yatırım, büyüme)")
    rationale: str = Field(description="Bu skorun neden verildiğini açıklayan kısa, 1 cümlelik gerekçe (Tell me why)")
    signal_category: str = Field(description="Şirketteki ana sinyal kategorisi.") # BU SATIRI EKLEDİK
class EmailDraft(BaseModel):
    subject: str = Field(description="E-posta konu başlığı")
    body: str = Field(description="E-posta içeriği (kısa, doğrudan sinyale atıfta bulunan)")

# --- 3. LangGraph State Tanımı ---
# Ajanların ortak hafızası. Her adımda bu sözlük (dictionary) güncellenecek.
class AgentState(TypedDict):
    target_company: str           # Kullanıcının girdiği şirket adı
    research_data: str            # İnternetten çekilen ham veri
    source_urls: List[str]
    analysis: Optional[dict]      # LeadAnalysis (Pydantic) modelinin dict hali
    draft_email: Optional[dict]   # EmailDraft (Pydantic) modelinin dict hali
    human_feedback: Optional[str] # İnsanın onay ekranında verdiği feedback (Reddedilirse)
    final_status: str             # "Pending", "Approved", "Saved to CRM"

# --- 4. Sistem Promptları (Memory / İdeal Müşteri Profili) ---
IDEAL_CUSTOMER_PROFILE = """
Sen B2B Operasyon Danışmanlığı satan bir sistemin Analist Ajanısın.
İdeal müşterilerimiz: 20-150 çalışanı olan, üretim veya lojistik şirketleri.
Aranan Sinyaller (Pain Signals): Yeni operasyon/tedarik zinciri iş ilanları, recent büyüme, yeni yöneticiler.
Görev: Şirket araştırmasını oku ve skorla. Sinyal varsa 8-10 arası puan ver.
"""

# --- 5. Ajanlar (Nodes) ---

def researcher_node(state: AgentState) -> dict:
    """DDGS ile şirketi araştırır. Hata durumunda sistemi çökertmez."""
    company = state['target_company']
    print(f"🕵️ Araştırmacı Ajan: {company} için veri topluyor...")
    
    # Saf arama niyetini koruyoruz
    query = f"{company} company news recent developments hiring"
    
    urls = []
    research_text = "" # EKSİK OLAN HAYATİ SATIR BURASI!
    
    try:
        results = DDGS().text(query, max_results=5)
        if results:
            for r in results:
                # Sadece gövde (body) metnini alarak LLM'i saf bilgiye odaklıyoruz
                research_text += f"{r.get('body', '')}\n\n"
                
                # Arayüz (Expander) için linkleri topluyoruz
                if r.get('href'):
                    urls.append(r.get('href'))
        else:
            research_text = "İnternette şirket hakkında yeterli veri bulunamadı."
            
    except Exception as e:
        print(f"⚠️ Arama Aracı Hatası (Tool Error): {e}")
        research_text = (
            f"İnternet araması şu hata nedeniyle yapılamadı: {e}. "
            f"Lütfen {company} şirketi hakkında sahip olduğun genel bilgiye dayanarak bir analiz yap "
            f"veya veri eksikliği nedeniyle fit_score'u düşük tutarak 'No Signal' olarak işaretle."
        )
    
    return {"research_data": research_text, "source_urls": urls}


def analyst_node(state: AgentState) -> dict:
    print("🧠 Analist Ajan: Veriyi inceliyor ve skorluyor...")
    
    # Araştırma verisi boş gelirse diye güvenlik önlemi
    research_data = state.get('research_data', '')
    if not research_data or len(research_data) < 10:
        research_data = "İnternette şirket hakkında yeterli güncel veri bulunamadı."

    prompt = f"""
    {IDEAL_CUSTOMER_PROFILE}
    Şirket Adı: {state['target_company']}
    Araştırma Verisi: {research_data}
    
    ÇOK ÖNEMLİ KURAL: Sen bir API'sin. Kesinlikle "Merhaba", "Tabii", "Teşekkürler" gibi sohbet kelimeleri KULLANMA. 
    Bana SADECE VE SADECE benden beklenen tool (fonksiyon) formatında yanıt ver. 
    """
    
    structured_llm = llm.with_structured_output(LeadAnalysis)
    result = structured_llm.invoke(prompt)
    
    return {"analysis": result.model_dump(), "final_status": "Pending Analysis"}

def copywriter_node(state: AgentState) -> dict:
    """Analiz sonucuna göre kısa ve net bir e-posta taslağı yazar."""
    print("✍️ Yazar Ajan: Cold email taslağı oluşturuyor...")
    
    feedback_context = f"\nİnsan Geri Bildirimi: {state['human_feedback']}" if state.get('human_feedback') else ""
    
    prompt = f"""
    Sen tecrübeli bir B2B danışmanısın. Aşağıdaki şirket analizini kullanarak bir tanışma e-postası (cold email) yaz.
    
    Şirket: {state['analysis']['company_name']}
    Tespit Edilen Sinyal: {state['analysis']['signal']}
    {feedback_context}
    
    Kurallar: 
    1. "Umarım iyisinizdir" gibi klişeler kullanma. Doğrudan konuya gir.
    2. Analizdeki 'Sinyal'e (Signal) doğrudan atıfta bulun.
    3. ÇOK ÖNEMLİ: Sadece e-posta içeriğini tool formatında döndür. "İşte mailiniz", "Tamamdır" gibi giriş cümleleri YAZMA.
    """
    
    structured_llm = llm.with_structured_output(EmailDraft)
    result = structured_llm.invoke(prompt)
    
    return {"draft_email": result.model_dump(), "human_feedback": None} # Feedback kullanıldı, sıfırlıyoruz


def crm_node(state: AgentState) -> dict:
    """Onaylanan veriyi CRM'e yazar.
    Database hücresine kısa email preview,
    Notion sayfasının içine ise tam email taslağını ekler.
    """
    print("💾 CRM Ajanı: Notion veritabanına kaydediliyor...")
    notion = Client(auth=NOTION_TOKEN)

    if not state.get("analysis"):
        return {"final_status": "Failed"}

    try:
        analysis = state["analysis"]

        # ---------------------------------------------------------
        # Email bilgileri
        # ---------------------------------------------------------
        draft_email = state.get("draft_email") or {}

        draft_body = draft_email.get("body", "")
        draft_subject = draft_email.get("subject", "")

        # ---------------------------------------------------------
        # 1. Database'de gösterilecek kısa preview
        # ---------------------------------------------------------
        preview_length = 120

        if draft_body:
            draft_preview = draft_body[:preview_length].strip()

            if len(draft_body) > preview_length:
                draft_preview += "..."
        else:
            draft_preview = ""

        # ---------------------------------------------------------
        # 2. Database properties
        # ---------------------------------------------------------
        new_page_props = {
            "Company": {
                "title": [
                    {
                        "text": {
                            "content": analysis.get(
                                "company_name",
                                "Bilinmiyor"
                            )
                        }
                    }
                ]
            },

            "Fit Score": {
                "number": int(
                    analysis.get("fit_score", 0)
                )
            },

            "Signal": {
                "rich_text": [
                    {
                        "text": {
                            "content": analysis.get(
                                "signal",
                                ""
                            )[:2000]
                        }
                    }
                ]
            },

            "Rationale": {
                "rich_text": [
                    {
                        "text": {
                            "content": analysis.get(
                                "rationale",
                                ""
                            )[:2000]
                        }
                    }
                ]
            },

            "Status": {
                "select": {
                    "name": "CRM'e Eklendi"
                }
            }
        }

        # Database hücresine sadece kısa preview
        if draft_preview:
            new_page_props["Draft Email"] = {"rich_text": [{"text": {"content": draft_preview}}]}

        # ---------------------------------------------------------
        # 3. Notion Database'e page oluştur
        # ---------------------------------------------------------
        response = notion.pages.create(parent={"database_id": NOTION_DATABASE_ID},properties=new_page_props)

        page_id = response["id"]

        # ---------------------------------------------------------
        # 4. Sayfanın içine TAM email'i ekle
        # ---------------------------------------------------------

        if draft_body:

            email_blocks = []

            # Başlık
            email_blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{
                            "type": "text",
                            "text": {
                                "content": "📧 Draft Email"
                            }}]}})

            # Subject
            if draft_subject:
                email_blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                                "type": "text",
                                "text": {
                                    "content": f"Subject: {draft_subject}"
                                }}]}})

            # Ayırıcı
            email_blocks.append({
                "object": "block",
                "type": "divider",
                "divider": {}
            })

            # -----------------------------------------------------
            # Email body'yi 2000 karakterlik parçalara böl
            # -----------------------------------------------------

            chunk_size = 1900

            for i in range(0, len(draft_body), chunk_size):

                chunk = draft_body[i:i + chunk_size]

                email_blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": chunk
                                }
                            }
                        ]
                    }
                })

            # -----------------------------------------------------
            # Notion'a body block'larını ekle
            # -----------------------------------------------------

            notion.blocks.children.append(
                block_id=page_id,
                children=email_blocks
            )

        print("✅ Notion'a başarıyla kaydedildi!")
        print(f"📄 Page ID: {page_id}")

        return {
            "final_status": "Saved to CRM"
        }

    except Exception as e:
        print(f"❌ Notion Hatası: {e}")

        return {
            "final_status": f"Error saving to CRM: {str(e)}"
        }

# --- 6. Routing (Yönlendirme) Mantığı ---

def route_after_analysis(state: AgentState) -> str:
    """Skora bakar. Yüksekse mail yazdırır, düşükse doğrudan CRM'e kaydeder."""
    score = state['analysis']['fit_score']
    print(f"🔀 Yönlendirici (Router): Skor {score}. Karar veriliyor...")
    
    if score >= 7:
        return "write_email" # Yüksek skor, mail taslağı hazırla
    else:
        return "save_to_crm" # Düşük skor, sadece veritabanına ekle

# --- 7. LangGraph'ın İnşa Edilmesi ---

workflow = StateGraph(AgentState)

# Düğümleri (Ajanları) Grafa ekliyoruz
workflow.add_node("Researcher", researcher_node)
workflow.add_node("Analyst", analyst_node)
workflow.add_node("Copywriter", copywriter_node)
workflow.add_node("CRM_Updater", crm_node)
# İnsan onayı için sanal bir düğüm ekliyoruz (İçi boş, sadece duraklatmak için)
workflow.add_node("Human_Approval", lambda state: state) 

# Akışı (Kenarları) bağlıyoruz
workflow.add_edge(START, "Researcher")
workflow.add_edge("Researcher", "Analyst")

# Analizden sonra şartlı dallanma (Conditional Edge)
workflow.add_conditional_edges(
    "Analyst",
    route_after_analysis,
    {
        "write_email": "Copywriter",
        "save_to_crm": "CRM_Updater"
    }
)

workflow.add_edge("Copywriter", "Human_Approval")

# Human_Approval'dan sonra CRM'e geç (Streamlit bu araya girecek)
def route_after_human(state: AgentState) -> str:
    """İnsan onayından sonra ne olacağına karar verir."""
    if state.get("human_feedback"):
        print("🔄 İnsan geri bildirimi alındı. Yazar ajana (Copywriter) geri dönülüyor.")
        return "rewrite"
    else:
        print("✅ İnsan onayladı. CRM'e gidiliyor.")
        return "proceed_to_crm"

workflow.add_conditional_edges(
    "Human_Approval",
    route_after_human,
    {
        "rewrite": "Copywriter",
        "proceed_to_crm": "CRM_Updater"
    }
)

workflow.add_edge("CRM_Updater", END)

# SQLite kullanarak Checkpointer (Hafıza) oluşturuyoruz
# 'checkpointer_connection' kapatılmaması gereken global bir bağlantı
conn = sqlite3.connect("state.db", check_same_thread=False)
memory = SqliteSaver(conn)

# Grafı derliyoruz. 
# DİKKAT: "Human_Approval" düğümünden önce grafı DURAKLATIYORUZ (interrupt_before)
app = workflow.compile(
    checkpointer=memory,
    interrupt_before=["Human_Approval"] 
)