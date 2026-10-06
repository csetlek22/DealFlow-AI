# 🔮 DealFlow AI: Autonomous B2B Lead Finder

DealFlow AI is an autonomous, multi-agent SaaS application designed to revolutionize B2B lead generation and cold outreach. Built with a robust Map-Reduce architecture, it discovers target companies, analyzes their growth signals, scores their fit, and drafts hyper-personalized cold emails—while keeping the human in the loop.

## 🚀 Features

- **Autonomous Batch Sourcing:** Input a generic target profile (e.g., "actively growing logistics companies"), and the Master Agent will continuously scrape and queue target companies.
- **Multi-Agent Workflow (LangGraph):** Employs isolated agent threads for Research, Analysis, and Copywriting to ensure deterministic and hallucination-free outputs.
- **Human-in-the-Loop (HITL):** A sleek Streamlit UI halts the workflow before any execution, allowing you to review the agent's audit logs, edit the drafted email, or request a rewrite via natural language feedback.
- **Direct CRM Integration:** Seamlessly pushes enriched data and draft previews to your Notion Database.
- **Automated SMTP Outreach:** Sends the approved cold email directly to the decision-maker using SSL-secured SMTP.

## 🛠️ Tech Stack

- **Orchestration:** [LangGraph](https://python.langchain.com/docs/langgraph/) (StateGraph, Checkpointer)
- **LLM:** Google Gemini 3.1 Flash-Lite (via `langchain-google-genai`)
- **Frontend:** Streamlit
- **Data Validation:** Pydantic
- **Web Scraping:** DuckDuckGo Search (`ddgs`)
- **Package Management:** `uv`

## 📦 Prerequisites

Ensure you have the following installed before running the project:
- **Python:** `>=3.14`
- **Package Manager:** [uv](https://github.com/astral-sh/uv)

You will also need the following external credentials:
1. **Google Gemini API Key:** For agent reasoning.
2. **Notion Integration Token & Database ID:** For CRM tracking.
3. **Gmail App Password:** A 16-character app password for SMTP email delivery.

## ⚙️ Installation & Setup

**1. Clone the repository**
```bash
git clone [https://github.com/yourusername/dealflow-ai.git](https://github.com/yourusername/dealflow-ai.git)
cd dealflow-ai

```

**2. Set up the environment using `uv**`
Create a virtual environment and install the dependencies defined in `pyproject.toml`:**

```bash
uv venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
uv pip install -e .

```

**3. Configure Environment Variables**
Copy the template file to create your local `.env` file:

```bash
cp .env.example .env

```

Open the `.env` file and populate it with your actual API keys and credentials:

```env
GOOGLE_API_KEY="your_gemini_api_key_here"
NOTION_TOKEN="your_notion_integration_token_here"
NOTION_DATABASE_ID="your_notion_database_id_here"
sender_email="your_gmail_address@gmail.com"
sender_password="your_16_character_app_password"

```

## 🎯 Usage

Start the Streamlit interface:

```bash
streamlit run app_5.py

```

1. Enter your target audience prompt (e.g., *"fast-growing logistics companies in Turkey hiring executives"*).
2. Click **Start Batch Sourcing**.
3. Review the high-scoring leads in the UI. Open the **"Agent's Sources"** expander to audit the URLs and raw data the agent read.
4. **Take Action:**
* **Save to CRM:** Pushes the lead data to Notion.
* **Save & Send:** Pushes to Notion and securely sends the email via SMTP.
* **Rewrite:** Provide feedback to the agent (e.g., *"Make it shorter and more aggressive"*) and hit rewrite.



## 🏗️ Architecture Blueprint

1. **Master Agent (`generate_leads`):** Uses OSINT to parse target companies based on the user's prompt.
2. **Researcher Node:** Scrapes the web for recent news, developments, and hiring signals for each specific company.
3. **Analyst Node:** Evaluates the scraped data against an Ideal Customer Profile (ICP), determines a "Fit Score", and estimates the contact email.
4. **Router:** Filters out low-scoring companies (Score < 7) and drops them into a rejection pool.
5. **Copywriter Node:** Drafts a personalized B2B cold email based on the Analyst's specific "Pain Signal" identification.

## 🛡️ License

This project is licensed under the MIT License.

```

```
