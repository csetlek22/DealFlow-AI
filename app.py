import os
import streamlit as st
import time
from orchestrator import app, AgentState, generate_leads, crm_node
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="DealFlow AI - Batch", page_icon="🚀", layout="wide")

st.markdown("""
<style>
/* Main theme */
.stApp {
    background-color: #FFFFFF;
    color: #2B2B2B;
}

/* All headings */
h1, h2, h3, h4, h5, h6, span, label, div {
    color: #FFFFFF;
}

/* Text elements */
p, span, label, div {
    color: #6A1B9A;
}

/* Buttons */
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

/* Info / Success alerts */
div[data-testid="stAlert"] {
    border-radius: 8px !important;
    border-left: 5px solid #6A1B9A !important;
}

/* Lead card */
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

/* Links */
a {
    color: #6A1B9A !important;
}
</style>
""", unsafe_allow_html=True)

# --- Session State Management ---
if "batch_companies" not in st.session_state:
    st.session_state.batch_companies = []
if "threads" not in st.session_state:
    st.session_state.threads = {}

st.title("🚀 Autonomous Batch Sourcing")
st.markdown("Enter your target audience, let the Master Agent find companies, and allow Worker Agents to independently analyze them and draft tailored emails.")

# --- 1. Search and Master Agent ---
target_prompt = st.text_input("Target Profile:", placeholder="e.g., actively growing and hiring logistics companies in Turkey")

if st.button("🔍 Start Batch Sourcing", use_container_width=True):
    if target_prompt:
        with st.spinner("Master Agent is scanning the web and identifying target companies..."):
            companies = generate_leads(target_prompt)
            if not companies:
                st.error("No suitable companies found for the specified target profile.")
                st.stop()
                
            st.session_state.batch_companies = companies
            
            for company in companies:
                thread_id = f"t_{company.replace(' ', '')}_{int(time.time())}"
                st.session_state.threads[company] = thread_id
                
                initial_state = {"target_company": company, "draft_email": None, "analysis": None, "human_feedback": None}
                config = {"configurable": {"thread_id": thread_id}}
                
                for _ in app.stream(initial_state, config): pass
                time.sleep(4) 
                
            st.rerun()

# --- 2. Categorize and Display Results ---
if st.session_state.batch_companies:
    low_fit_list = []
    
    st.markdown("---")
    st.subheader("📬 Processed and Pending High-Score Companies")
    
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
            
        # LOGIC: Render the company block whether it is "Pending Approval" or "Finished"
        is_pending = "Human_Approval" in state_info.next
        is_finished = len(state_info.next) == 0
        
        if is_pending or is_finished:
            with st.expander(f"✨ {company} - Score: {analysis['fit_score']}/10", expanded=True):
                st.markdown(f"**📡 Category:** {analysis.get('signal_category', '')} | **🔍 Signal:** {analysis.get('signal', '')}")
                st.markdown(f"**🧠 Rationale:** {analysis.get('rationale', '')}")
                
                # --- 1. Agent Sources and Raw Data ---
                with st.expander("🔍 Agent's Sources and Raw Data"):
                    st.write("**Visited Links:**")
                    for url in state.get('source_urls', []): st.markdown(f"- [{url}]({url})")
                    st.write("**Raw Data Read:**")
                    st.info(state.get('research_data', 'No data available.'))
                
                if is_pending:
                    # --- PENDING (ACTIVE) STATE ---
                    # Using a Dynamic Key to bypass Streamlit's aggressive UI caching
                    email_body_value = state.get('draft_email', {}).get('body', '')
                    dynamic_key = f"body_{company}_{len(email_body_value)}"
                    
                    edited_email_body = st.text_area("Email Content", value=email_body_value, height=150, key=dynamic_key)

                    c1, c2 = st.columns(2)
                    with c1: target_email = st.text_input("Recipient", value=analysis.get('contact_email', 'info@company.com'), key=f"email_{company}")
                    with c2: email_subject = st.text_input("Subject", value=state.get('draft_email', {}).get('subject', 'Introduction'), key=f"subj_{company}")
                    
                    feedback = st.text_input("Feedback:", key=f"fb_{company}")

                    if f"status_{company}" in st.session_state:
                        status_message = st.session_state[f"status_{company}"]

                        if "⚠️" in status_message or "❌" in status_message:
                            st.warning(status_message)
                        else:
                            st.success(status_message)
                    
                    b1, b2, b3 = st.columns(3)
                    
                    # --- 2. Manual CRM save without advancing the graph ---
                    if b1.button("💾 Save to CRM Only", key=f"btn_save_{company}", use_container_width=True):
                        app.update_state(
                            config,
                            {
                                "draft_email": {
                                    "subject": email_subject,
                                    "body": edited_email_body
                                },
                                "human_feedback": None
                            }
                        )

                        with st.spinner("Saving to Notion..."):
                            current_state = app.get_state(config).values
                            crm_node(current_state)

                        st.session_state[f"status_{company}"] = "✅ Saved to Notion CRM. Form remains active."
                        st.rerun()
                                            
                    if b2.button("📧 Save & Send", key=f"btn_send_{company}", use_container_width=True):
                        app.update_state(
                            config,
                            {
                                "draft_email": {
                                    "subject": email_subject,
                                    "body": edited_email_body
                                },
                                "human_feedback": None
                            }
                        )

                        with st.spinner("Saving to Notion and sending email..."):
                            current_state = app.get_state(config).values

                            # Save to CRM
                            crm_node(current_state)

                            try:
                                sender_email = os.getenv("sender_email")
                                sender_password = os.getenv("sender_password")

                                msg = MIMEMultipart()
                                msg["From"] = sender_email
                                msg["To"] = target_email
                                msg["Subject"] = email_subject
                                msg.attach(MIMEText(edited_email_body, "plain"))

                                server = smtplib.SMTP_SSL(
                                    "smtp.gmail.com",
                                    465,
                                    timeout=5
                                )
                                server.login(sender_email, sender_password)
                                server.send_message(msg)
                                server.quit()

                                st.session_state[f"status_{company}"] = (
                                    f"✅ Saved to CRM and email successfully sent to {target_email}!"
                                )

                            except Exception as e:
                                st.session_state[f"status_{company}"] = (
                                    f"⚠️ Saved to CRM but email transmission failed: {e}"
                                )

                        st.rerun()                        
                    if b3.button("🔄 Rewrite", key=f"btn_rew_{company}", use_container_width=True):
                        if feedback:
                            app.update_state(config, {"human_feedback": feedback}, as_node="Human_Approval")
                            with st.spinner("Agent is rewriting the draft..."):
                                for _ in app.stream(None, config): pass
                            
                            for k in [f"body_{company}", f"subj_{company}", f"email_{company}", f"fb_{company}"]:
                                if k in st.session_state:
                                    del st.session_state[k]
                            st.rerun()
                        else:
                            st.error("Please provide feedback for the agent.")
                
                else:
                    # --- FINISHED (READ-ONLY) STATE ---
                    status_message = st.session_state.get(f"status_{company}", "✅ Processing completed for this company and saved to CRM.")
                    
                    if "⚠️" in status_message or "❌" in status_message:
                        st.warning(status_message)
                    else:
                        st.success(status_message)
                        
                    # Render the email as disabled to prevent further edits after execution
                    st.text_area("Saved / Sent Email Content", value=state.get('draft_email', {}).get('body', ''), height=150, disabled=True, key=f"body_done_{company}")

    # --- 3. Low-Score Area ---
    if low_fit_list:
        st.markdown("---")
        st.subheader("🗑️ Poor Fit Companies (Fit Score < 7)")
        for low_comp in low_fit_list:
            st.info(f"**{low_comp['company_name']}** (Score: {low_comp['fit_score']}) - *{low_comp['rationale']}*")

    # --- 4. Clear All Button ---
    st.markdown("---")
    if st.button("🗑️ Clear All Results and Reset Data", use_container_width=True):
        st.session_state.batch_companies = []
        st.session_state.threads = {}
        st.rerun()