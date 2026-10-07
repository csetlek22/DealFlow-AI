import re
import uuid
import streamlit as st
from dotenv import load_dotenv
from orchestrator import app, AgentState, generate_leads, QUALIFIED_SCORE, crm_node
from email_service import send_email


load_dotenv()

st.set_page_config(
    page_title="DealFlow AI",
    page_icon="🚀",
    layout="wide",
)

st.markdown("""
<style>
.stApp { background:#fff; }
h1,h2,h3,p,span,label { color:#2B2B2B; }
.stButton > button {
    background:#6A1B9A !important;
    color:#fff !important;
    border:0;
    border-radius:8px;
}
.stButton > button:hover { background:#8E24AA !important; }
div[data-baseweb="input"] input, textarea { border-color:#6A1B9A !important; }
.lead-card {
    border:1px solid #ddd;
    border-radius:12px;
    padding:18px;
    margin-bottom:16px;
}
</style>
""", unsafe_allow_html=True)


if "batch_companies" not in st.session_state:
    st.session_state.batch_companies = []

if "threads" not in st.session_state:
    st.session_state.threads = {}


def slug(value):
    """Create a stable value for Streamlit widget keys."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")[:50]


st.title("🚀 DealFlow AI")
st.caption("AI-powered B2B lead sourcing, qualification and personalized outreach.")

target_profile = st.text_area(
    "Target Customer Profile",
    value=(
        "Find manufacturing and logistics companies with 20–150 employees "
        "that are currently growing, hiring in operations/supply chain, "
        "expanding capacity, or undergoing operational transformation."
    ),
    height=100,
)


if st.button("🔎 Start Batch Sourcing", use_container_width=True):
    if not target_profile.strip():
        st.error("Please enter a target customer profile.")
    else:
        with st.spinner("Finding companies..."):
            companies = generate_leads(target_profile)

        if not companies:
            st.warning("No suitable companies were found.")
        else:
            st.session_state.batch_companies = companies
            st.session_state.threads = {}

            progress = st.progress(0)

            for i, company in enumerate(companies):
                thread_id = f"{slug(company)}_{uuid.uuid4().hex[:8]}"

                initial_state: AgentState = {
                    "target_profile": target_profile,
                    "target_company": company,
                    "research_data": "",
                    "source_urls": [],
                    "company_research": {},
                    "analysis": {},
                    "contact": {},
                    "draft_email": {},
                    "human_feedback": "",
                    "approved": False,
                    "crm_page_id": "",
                    "final_status": "",
                }

                config = {"configurable": {"thread_id": thread_id}}

                try:
                    with st.status(f"Researching {company}...", expanded=False):
                        for _ in app.stream(initial_state, config):
                            pass

                    st.session_state.threads[company] = thread_id

                except Exception as e:
                    st.session_state.threads[company] = None
                    st.error(f"{company}: processing failed — {e}")

                progress.progress((i + 1) / len(companies))

            st.rerun()


st.divider()

low_fit = []

if st.session_state.batch_companies:
    st.subheader(f"Results ({len(st.session_state.batch_companies)})")

    for company in st.session_state.batch_companies:
        thread_id = st.session_state.threads.get(company)

        if not thread_id:
            continue

        config = {"configurable": {"thread_id": thread_id}}

        try:
            state_info = app.get_state(config)
            state = state_info.values
        except Exception as e:
            st.error(f"Could not load {company}: {e}")
            continue

        analysis = state.get("analysis") or {}

        if not analysis:
            st.warning(f"No analysis available for {company}.")
            continue

        score = analysis.get("fit_score", 0)

        if score < QUALIFIED_SCORE:
            low_fit.append((company, state))
            continue

        contact = state.get("contact") or {}
        draft = state.get("draft_email") or {}
        next_nodes = tuple(state_info.next or ())

        # Qualified leads should reach the email approval stage.
        if not draft and next_nodes and "Human_Approval" not in next_nodes:
            try:
                with st.spinner(f"Completing research for {company}..."):
                    for _ in app.stream(None, config):
                        pass

                state_info = app.get_state(config)
                state = state_info.values
                contact = state.get("contact") or {}
                draft = state.get("draft_email") or {}

            except Exception as e:
                st.error(f"Could not complete email generation for {company}: {e}")

        with st.container():
            st.markdown('<div class="lead-card">', unsafe_allow_html=True)
            st.subheader(company)

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("Fit Score", f"{score}/10")

            with col2:
                st.metric("ICP", f"{analysis.get('icp_score', 0)}/3")

            with col3:
                st.metric(
                    "Signal",
                    analysis.get("signal_category", "Unknown"),
                )

            st.write(f"**Signal:** {analysis.get('signal', '-')}")
            st.write(f"**Rationale:** {analysis.get('rationale', '-')}")

            with st.expander("Research Details"):
                st.json(state.get("company_research") or {})

                urls = state.get("source_urls") or []

                if urls:
                    st.write("**Sources**")

                    for url in urls:
                        st.write(url)

            if contact.get("contact_name") or contact.get("title"):
                st.write("### Decision Maker")

                if contact.get("contact_name"):
                    st.write(f"**Name:** {contact['contact_name']}")

                if contact.get("title"):
                    st.write(f"**Role:** {contact['title']}")

                if contact.get("contact_email"):
                    st.write(f"**Verified Email:** {contact['contact_email']}")

                st.caption(
                    f"Contact confidence: {contact.get('confidence', 'low')}"
                )

            st.write("### Email")

            email_body = st.text_area(
                "Email Body",
                value=draft.get("body", ""),
                height=220,
                key=f"body_{thread_id}",
            )

            email_subject = st.text_input(
                "Subject",
                value=draft.get("subject", ""),
                key=f"subject_{thread_id}",
            )

            recipient = st.text_input(
                "Recipient",
                value=contact.get("contact_email", "") or "",
                key=f"recipient_{thread_id}",
            )

            feedback = st.text_area(
                "Feedback for AI Rewrite",
                placeholder=(
                    "Example: Make it shorter and focus more on their "
                    "recent hiring activity."
                ),
                key=f"feedback_{thread_id}",
                height=90,
            )

            approved = bool(state.get("approved"))

            if not approved:
                if st.button(
                    "✅ Approve Lead",
                    key=f"approve_{thread_id}",
                    use_container_width=True,
                ):
                    try:
                        app.update_state(
                            config,
                            {"approved": True},
                            as_node="Human_Approval",
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"Approval failed: {e}")

            c1, c2, c3 = st.columns(3)

            if c1.button(
                "💾 Save to CRM",
                key=f"save_{thread_id}",
                use_container_width=True,
                disabled=not approved,
            ):
                if not email_subject.strip() or not email_body.strip():
                    st.warning("Subject and email body are required.")
                else:
                    try:
                        app.update_state(
                            config,
                            {
                                "draft_email": {
                                    "subject": email_subject,
                                    "body": email_body,
                                },
                                "human_feedback": "",
                            },
                            as_node="Human_Approval",
                        )

                        # V7'DEKİ KESİN ÇALIŞAN MANTIK: Grafiği ilerletmeden manuel CRM kaydı
                        current_state = app.get_state(config).values
                        crm_node(current_state)

                        st.success("✅ Saved to CRM. Form remains active.")
                        st.rerun()

                    except Exception as e:
                        st.error(f"CRM save failed: {e}")

            if c2.button(
                "📨 Save & Send",
                key=f"send_{thread_id}",
                use_container_width=True,
                disabled=not approved,
            ):
                if not recipient.strip():
                    st.error("Enter a recipient email first.")
                elif not email_subject.strip() or not email_body.strip():
                    st.warning("Subject and email body are required.")
                else:
                    try:
                        send_email(
                            recipient,
                            email_subject,
                            email_body,
                        )

                        app.update_state(
                            config,
                            {
                                "draft_email": {
                                    "subject": email_subject,
                                    "body": email_body,
                                },
                                "human_feedback": "",
                                "final_status": "Email sent",
                            },
                            as_node="Human_Approval",
                        )

                        # Manuel CRM Kaydı
                        current_state = app.get_state(config).values
                        crm_node(current_state)

                        st.success(f"✅ Email sent to {recipient} and saved to CRM.")
                        st.rerun()

                    except Exception as e:
                        st.error(f"Email failed: {e}")

            if c3.button(
                "✏️ Rewrite",
                key=f"rewrite_{thread_id}",
                use_container_width=True,
            ):
                if not feedback.strip():
                    st.warning("Enter feedback first.")
                else:
                    try:
                        app.update_state(
                            config,
                            {
                                "draft_email": {
                                    "subject": email_subject,
                                    "body": email_body,
                                },
                                "human_feedback": feedback,
                            },
                            as_node="Human_Approval",
                        )

                        for _ in app.stream(None, config):
                            pass

                        # Refresh the widgets so the rewritten subject/body
                        # appear immediately instead of the previous draft.
                        new_state = app.get_state(config).values
                        new_draft = new_state.get("draft_email") or {}

                        st.session_state[f"subject_{thread_id}"] = new_draft.get("subject", "")
                        st.session_state[f"body_{thread_id}"] = new_draft.get("body", "")
                        st.session_state[f"feedback_{thread_id}"] = ""

                        st.rerun()

                    except Exception as e:
                        st.error(f"Rewrite failed: {e}")

            status = state.get("final_status", "")

            if status:
                st.success(status)

            st.markdown("</div>", unsafe_allow_html=True)


if low_fit:
    st.divider()
    st.subheader(f"Lower-Fit Leads ({len(low_fit)})")

    for company, state in low_fit:
        analysis = state.get("analysis") or {}

        with st.expander(
            f"{company} — {analysis.get('fit_score', 0)}/10"
        ):
            st.write(f"**Signal:** {analysis.get('signal', '-')}")
            st.write(f"**Rationale:** {analysis.get('rationale', '-')}")
            st.write(f"**ICP:** {analysis.get('icp_score', 0)}/3")
            st.write(
                f"**Business Need:** "
                f"{analysis.get('business_need_score', 0)}/3"
            )
            st.write(
                f"**Recency:** "
                f"{analysis.get('recency_score', 0)}/2"
            )
            st.write(
                f"**Signal Strength:** "
                f"{analysis.get('signal_strength_score', 0)}/2"
            )


st.divider()

if st.button("🗑️ Clear Results", use_container_width=True):
    st.session_state.batch_companies = []
    st.session_state.threads = {}
    st.rerun()
