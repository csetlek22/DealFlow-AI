import streamlit as st
import time
from orchestrator import app, AgentState, generate_leads, crm_node
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

st.set_page_config(page_title="DealFlow AI - Batch", page_icon="🚀", layout="wide")

st.markdown("""
<style>
/* Ana tema */
.stApp {
    background-color: #FFFFFF;
    color: #2B2B2B;
}

/* Tüm yazılar */
h1, h2, h3, h4, h5, h6, span, label, div {
    color: #FFFFFF;
}

/* Tüm yazılar */
p,span, label, div   {
    color: #6A1B9A;
}

/* Butonlar */
.stButton > button {
    background-color: #F3E5F5 !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
}

.stButton > button:hover {
    background-color: #8E24AA !important;
    color: #FFFFFF !important;
}

/* Text input + textarea */
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div {
    background-color: #8E24AA !important;
    border: 1px solid #6A1B9A !important;
    border-radius: 8px !important;
}

div[data-baseweb="input"] input,
div[data-baseweb="textarea"] textarea {
    background-color: #8E24AA !important;
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF !important;
}

input::placeholder,
textarea::placeholder {
    color: #D1C4E9 !important;
    -webkit-text-fill-color: #D1C4E9 !important;
}

/* Selectbox */
div[data-baseweb="select"] > div {
    background-color: #6A1B9A !important;
    color: #FFFFFF !important;
    border: 1px solid #6A1B9A !important;
    border-radius: 8px !important;
}

/* Checkbox */
div[data-testid="stCheckbox"] label span {
    color: #2B2B2B !important;
}

/* Slider */
div[data-baseweb="slider"] div {
    background-color: #6A1B9A !important;
}

/* Bilgi / başarı kutuları */
div[data-testid="stAlert"] {
    border-radius: 8px !important;
    border-left: 5px solid #6A1B9A !important;
}

/* Lead kartı */
.lead-card {
    background-color: #FFFFFF;
    border: 2px solid #6A1B9A;
    padding: 20px;
    border-radius: 10px;
    box-shadow: 0 4px 8px rgba(106,27,154,0.15);
    margin-bottom: 20px;
}

/* Agent log */
.agent-log {
    background-color: #F3E5F5;
    border-left: 5px solid #6A1B9A;
    padding: 15px;
    margin-bottom: 10px;
    border-radius: 5px;
    color: #2B2B2B;
}

/* Divider */
hr {
    border-color: #6A1B9A !important;
}

/* Expander */
div[data-testid="stExpander"] {
    border: 1px solid #6A1B9A !important;
    border-radius: 8px !important;
}

/* Spinner */
div[data-testid="stSpinner"] {
    color: #6A1B9A !important;
}

/* Linkler */
a {
    color: #6A1B9A !important;
}
</style>
""", unsafe_allow_html=True)

# --- Session State Yönetimi ---
if "batch_companies" not in st.session_state:
    st.session_state.batch_companies = []
if "threads" not in st.session_state:
    st.session_state.threads = {}

st.title("🚀 Otonom Toplu Keşif (Batch Sourcing)")
st.markdown("Hedef kitleni yaz, Master Ajan şirketleri bulsun, İşçi Ajanlar her birini ayrı ayrı analiz edip mailleri hazırlasın.")

# --- 1. Arama ve Master Ajan ---
target_prompt = st.text_input("Hedef Profiliniz:", placeholder="Örn: actively growing and hiring logistic companies in turkey")

if st.button("🔍 Toplu Keşfi Başlat", use_container_width=True):
    if target_prompt:
        with st.spinner("Master Ajan interneti tarıyor ve şirketleri buluyor..."):
            companies = generate_leads(target_prompt)
            if not companies:
                st.error("Hedefe uygun şirket bulunamadı.")
                st.stop()
                
            st.session_state.batch_companies = companies
            
            for company in companies:
                thread_id = f"t_{company.replace(' ', '')}_{int(time.time())}"
                st.session_state.threads[company] = thread_id
                
                initial_state = {"target_company": company, "draft_email": None, "analysis": None, "human_feedback": None}
                config = {"configurable": {"thread_id": thread_id}}
                
                for _ in app.stream(initial_state, config): pass
                time.sleep(1) 
                
            st.rerun()

# --- 2. Sonuçları Kategorize Edip Ekrana Basma ---
if st.session_state.batch_companies:
    low_fit_list = []
    
    st.markdown("---")
    st.subheader("📬 İşlenen ve Onay Bekleyen Yüksek Skorlu Şirketler")
    
    for company in st.session_state.batch_companies:
        thread_id = st.session_state.threads.get(company)
        if not thread_id: continue
        
        config = {"configurable": {"thread_id": thread_id}}
        state_info = app.get_state(config)
        state = state_info.values
        
        if not state or not state.get("analysis"): continue
        
        analysis = state["analysis"]
        
        if analysis["fit_score"] < 7:
            low_fit_list.append(analysis)
            continue
            
        # YENİ MANTIK: Şirket "Onay Bekliyor" da olsa "İşlemi Bitmiş" de olsa ekranda çizilir
        is_pending = "Human_Approval" in state_info.next
        is_finished = len(state_info.next) == 0
        
        if is_pending or is_finished:
            with st.expander(f"✨ {company} - Skor: {analysis['fit_score']}/10", expanded=True):
                st.markdown(f"**📡 Kategori:** {analysis.get('signal_category', '')} | **🔍 Sinyal:** {analysis.get('signal', '')}")
                st.markdown(f"**🧠 Gerekçe:** {analysis.get('rationale', '')}")
                
                # --- 1. EKLENEN KISIM: Ajanın Kaynakları ve Ham Verisi ---
                with st.expander("🔍 Ajanın Kaynakları ve Ham Verisi"):
                    st.write("**Ziyaret Edilen Linkler:**")
                    for url in state.get('source_urls', []): st.markdown(f"- [{url}]({url})")
                    st.write("**Okunan Ham Veri:**")
                    st.info(state.get('research_data', 'Veri yok.'))
                
                if is_pending:
                    # --- İŞLEM BEKLEYEN (AKTİF) DURUM ---
                    # Streamlit'in inatçı UI önbelleğini kırmak için Dinamik Key kullanıyoruz
                    email_body_value = state.get('draft_email', {}).get('body', '')
                    dynamic_key = f"body_{company}_{len(email_body_value)}"
                    
                    edited_email_body = st.text_area("Mail İçeriği", value=email_body_value, height=150, key=dynamic_key)

                    c1, c2 = st.columns(2)
                    with c1: target_email = st.text_input("Alıcı", value=analysis.get('contact_email', 'info@sirket.com'), key=f"email_{company}")
                    with c2: email_subject = st.text_input("Konu", value=state.get('draft_email', {}).get('subject', 'Tanışma'), key=f"subj_{company}")
                    
                    feedback = st.text_input("Geri Bildirim:", key=f"fb_{company}")
                    
                    b1, b2, b3 = st.columns(3)
                    
                    # --- 2. GÜNCELLENEN KISIM: Grafı bitirmeden sadece manuel Notion'a kaydeder ---
                    if b1.button("💾 Sadece CRM'e Kaydet", key=f"btn_save_{company}", use_container_width=True):
                        # State'i LangGraph'a güncelliyoruz ama grafı (app.stream) İLERLETMİYORUZ
                        app.update_state(config, {"draft_email": {"subject": email_subject, "body": edited_email_body}, "human_feedback": None}, as_node="Human_Approval")
                        
                        with st.spinner("Notion'a kaydediliyor..."):
                            # Güncel state'i alıp CRM ajanını graf dışında manuel çalıştırıyoruz
                            current_state = app.get_state(config).values
                            crm_node(current_state)
                            
                        st.success("✅ Notion CRM'e kaydedildi. Form hala aktif; düzenleyebilir, yeniden yazdırabilir veya gönderebilirsiniz.")
                        
                    # (Diğer butonlar aynı mantıkla kalıyor)
                    if b2.button("📧 Kaydet & Gönder", key=f"btn_send_{company}", use_container_width=True):
                        app.update_state(config, {"draft_email": {"subject": email_subject, "body": edited_email_body}, "human_feedback": None}, as_node="Human_Approval")
                        for _ in app.stream(None, config): pass
                        
                        try:
                            # BURAYA KENDİ GMAIL BİLGİLERİNİ GİR
                            sender_email = "canersetlek68@gmail.com" 
                            sender_password = "bahjgkhnioyowrzx" 
                            
                            msg = MIMEMultipart()
                            msg['From'] = sender_email
                            msg['To'] = target_email
                            msg['Subject'] = email_subject
                            msg.attach(MIMEText(edited_email_body, 'plain'))
                            
                            server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=5)
                            server.login(sender_email, sender_password)
                            server.send_message(msg)
                            server.quit()
                            
                            st.session_state[f"status_{company}"] = f"✅ CRM'e kaydedildi ve Mail Başarıyla {target_email} adresine Gönderildi!"
                        except Exception as e:
                            st.session_state[f"status_{company}"] = f"⚠️ CRM'e kaydedildi ancak Mail Gönderim Hatası: {e}"
                        st.rerun()
                        
                    if b3.button("🔄 Yeniden Yaz", key=f"btn_rew_{company}", use_container_width=True):
                        if feedback:
                            app.update_state(config, {"human_feedback": feedback}, as_node="Human_Approval")
                            with st.spinner("Ajan yeniden yazıyor..."):
                                for _ in app.stream(None, config): pass
                            
                            for k in [f"body_{company}", f"subj_{company}", f"email_{company}", f"fb_{company}"]:
                                if k in st.session_state:
                                    del st.session_state[k]
                            st.rerun()
                        else:
                            st.error("Lütfen ajan için bir geri bildirim yazınız.")
                
                else:
                    # --- İŞLEMİ BİTMİŞ (SALT OKUNUR) DURUM ---
                    status_message = st.session_state.get(f"status_{company}", "✅ Bu şirket için işlemler tamamlandı ve CRM'e aktarıldı.")
                    
                    if "⚠️" in status_message or "❌" in status_message:
                        st.warning(status_message)
                    else:
                        st.success(status_message)
                        
                    # Ekran kaybolmasın diye maili 'disabled' (değiştirilemez) olarak çiziyoruz
                    st.text_area("Kaydedilen / Gönderilen Mail İçeriği", value=state.get('draft_email', {}).get('body', ''), height=150, disabled=True, key=f"body_done_{company}")

    # --- 3. Düşük Skorlular Alanı ---
    if low_fit_list:
        st.markdown("---")
        st.subheader("🗑️ Uyumsuz Şirketler (Fit Skoru < 7)")
        for low_comp in low_fit_list:
            st.info(f"**{low_comp['company_name']}** (Skor: {low_comp['fit_score']}) - *{low_comp['rationale']}*")

    # --- 4. Her Şeyi Temizle Butonu ---
    st.markdown("---")
    if st.button("🗑️ Tüm Sonuçları Temizle ve Ekrani Sıfırla", use_container_width=True):
        st.session_state.batch_companies = []
        st.session_state.threads = {}
        st.rerun()