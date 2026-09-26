import streamlit as st
import time
# Bir önceki adımda yazdığımız orchestrator dosyasından gerekli fonksiyonları çekiyoruz
from orchestrator import app, AgentState 

# --- 1. Arayüz Renk ve Tasarım Ayarları (Beyaz & Mor Tema) ---
st.set_page_config(
    page_title="DealFlow AI", 
    page_icon="🔮", 
    layout="wide"
)

# Streamlit'in standart renklerini ezmek için basit CSS
# Streamlit'in standart renklerini ezmek için gelişmiş CSS
st.markdown("""
    <style>
    /* Ana arka plan beyaz, genel metinler koyu gri */
    .stApp { background-color: #FFFFFF; }
    h1, h2, h3, p, span, label { color: #2B2B2B; }
    
    /* Butonlar: Mor arkaplan, beyaz yazı (Tüm iç elementleri zorla) */
    .stButton > button, .stButton > button p, .stButton > button span {
        background-color: #6A1B9A !important;
        color: #FFFFFF !important;
        border-radius: 8px;
        border: none;
    }
    .stButton > button:hover {
        background-color: #8E24AA !important;
        color: #FFFFFF !important;
    }
    
    /* Girdi Kutuları (Text Inputs): Mor arkaplan, beyaz yazı */
    div[data-baseweb="input"] > div, div[data-baseweb="input"] input {
        background-color: #6A1B9A !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important; /* Tarayıcı zorlaması */
    }
    /* Girdi kutularının içindeki placeholder (örnek) metinleri hafif şeffaf beyaz */
    div[data-baseweb="input"] input::placeholder {
        color: #D1C4E9 !important; 
        -webkit-text-fill-color: #D1C4E9 !important;
    }
    
    /* Mail Taslağı (Pre etiketi): Mor arkaplan, beyaz yazı */
    pre {
        background-color: #6A1B9A !important;
        color: #FFFFFF !important;
        padding: 15px;
        border-radius: 8px;
        white-space: pre-wrap; /* Uzun e-posta satırlarını ekrana sığdırır */
        font-family: 'Courier New', Courier, monospace;
        border: none;
    }
    
    /* Onay Bekleyen Lead Kartı */
    .lead-card {
        background-color: #FFFFFF;
        border: 2px solid #6A1B9A;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    
    /* Ajandan gelen mesaj kutusu (Ajan Logları) */
    .agent-log {
        background-color: #F3E5F5;
        border-left: 5px solid #6A1B9A;
        padding: 15px;
        margin-bottom: 10px;
        border-radius: 5px;
        font-family: 'Courier New', monospace;
        color: #2B2B2B;
    }
    </style>
""", unsafe_allow_html=True)


# --- 2. Session State (Sayfa yenilendiğinde verilerin kaybolmaması için) ---
if "thread_id" not in st.session_state:
    st.session_state.thread_id = "demo_thread_2" # LangGraph bu ID ile state'i veritabanında (SQLite) hatırlar.
if "running" not in st.session_state:
    st.session_state.running = False


# --- 3. Ana Arayüz (Header) ---
st.title("🔮 B2B Lead Orchestrator (Multi-Agent)")
st.write("Ajanlar şirketi araştırır, puanlar, taslak yazar. Sen sadece onaylarsın.")
st.markdown("---")

# --- 4. Girdi Alanı ---
col1, col2 = st.columns([3, 1])
with col1:
    target_company = st.text_input("Hedef Şirket Adı:", placeholder="Örn: Tarla.io")
with col2:
    st.write("") # Boşluk hizalaması
    st.write("")
    if st.button("🚀 Ajanları Başlat", use_container_width=True):
        if target_company:
            st.session_state.running = True
            st.session_state.target_company = target_company
            
            # LangGraph'a ilk girdiyi (initial state) veriyoruz
            initial_state = {"target_company": target_company}
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            
            # Başlat!
            with st.spinner(f"Ajanlar {target_company} için çalışıyor..."):
                # stream() metodu, düğümler (node) değiştikçe bize çıktı verir.
                for output in app.stream(initial_state, config):
                    for key, value in output.items():
                        st.markdown(f"<div class='agent-log'>⚙️ <b>{key}</b> düğümü çalıştı.</div>", unsafe_allow_html=True)
                        time.sleep(1) # Demo etkisi için yarım saniye bekletme
            st.rerun() # Döngü bitince sayfayı yenile (Onay ekranını göstermek için)


# --- 5. Human-in-the-Loop (Onay/Kesinti Ekranı) ---
if st.session_state.running:
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    
    # LangGraph'ın şu anki durumunu alıyoruz
    current_state_info = app.get_state(config)
    state = current_state_info.values # Bu, orchestrator'daki 'AgentState' sözlüğümüzdür.
    
    # Eğer graf, "Human_Approval" düğümünde bekliyorsa (next listesinde o varsa)
    if "Human_Approval" in current_state_info.next:
        st.subheader("⚠️ Onay Bekleniyor (Human-in-the-Loop)")
        
        # Ajanların bulduğu verileri şık bir 'Kart' içinde gösteriyoruz
        if state.get("analysis"):
            st.markdown(f"""
            <div class='lead-card'>
                <h3 style='color: #6A1B9A;'>🏢 {state['analysis']['company_name']}</h3>
                <p><b>🏆 Fit Skoru:</b> {state['analysis']['fit_score']}/10</p>
                <p><b>📡 Kategori:</b> {state['analysis']['signal_category']}</p>
                <p><b>🔍 Tespit (Signal):</b> {state['analysis']['signal']}</p>
                <p><b>🧠 Gerekçe:</b> {state['analysis']['rationale']}</p>
                <hr>
                <p><b>📝 Hazırlanan Cold Email Taslağı:</b></p>
                <pre>{state['draft_email']['body']}</pre>
            </div>
            """, unsafe_allow_html=True)
        
        st.write("")
        st.write("Ajanlar taslağı hazırladı. Notion CRM'e kaydetmeden önce onayınızı veya yönlendirmenizi bekliyor.")
        
        # Kullanıcının Geri Bildirim veya Onay vereceği kontroller
        feedback = st.text_input("Geri Bildiriminiz (Opsiyonel):", placeholder="Örn: Mail çok uzun, daha samimi yap.")
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("✅ Onayla ve Notion'a Kaydet", use_container_width=True):
                # Hiçbir feedback vermeden, sadece mevcut durumu güncelleyip grafı devam ettiriyoruz.
                app.update_state(config, {"human_feedback": None}, as_node="Human_Approval")
                
                with st.spinner("Notion'a kaydediliyor..."):
                    # Kalan düğümleri (CRM_Updater) çalıştır
                    for output in app.stream(None, config):
                         pass
                st.session_state.running = False
                st.success("İşlem tamamlandı! Lead başarıyla Notion CRM'e kaydedildi.")
                st.balloons()
                
        with col_btn2:
            if st.button("🔄 Geri Bildirimle Yeniden Yazdır", use_container_width=True):
                if feedback:
                    # state içine feedback'i koyuyoruz ki Copywriter okuyabilsin.
                    # as_node="Human_Approval" kısmı önemli: "Bu veriyi Human_Approval düğümünden gelmiş gibi State'e yaz" demek.
                    app.update_state(config, {"human_feedback": feedback}, as_node="Human_Approval")
                    
                    with st.spinner("Ajan geri bildiriminize göre yeniden çalışıyor..."):
                         # None diyerek grafın kaldığı yerden devam etmesini söylüyoruz.
                         # Ancak burada bir trik var: Yönlendirme (Routing) mantığımızı orchestrator'da 
                         # biraz güncelleyip, feedback varsa geri dönmesini sağlamalıyız (bunu aşağıda açıklayacağım).
                         for output in app.stream(None, config):
                             pass
                    st.rerun()
                else:
                    st.warning("Lütfen bir geri bildirim metni giriniz.")

    # Eğer graf tamamen bitmişse (next boşsa) ve bekleyen işlem yoksa
    elif len(current_state_info.next) == 0 and state.get("final_status") == "Saved to CRM":
         st.success("Tüm ajan işlemleri tamamlandı ve sistem CRM'e kaydetti.")
         if st.button("Yeni Bir Lead Araştır"):
             # State'i temizlemek için thread_id'yi değiştiriyoruz
             st.session_state.thread_id = f"thread_{int(time.time())}"
             st.session_state.running = False
             st.rerun()