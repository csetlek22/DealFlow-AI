"""Regression test for the Rewrite state/UI synchronization bug.

Proves that after clicking Rewrite the keyed Streamlit subject/body widgets
display the NEW draft (not the stale session_state value), that the
authoritative graph state holds the NEW draft, and that Save/Send then use it.
"""

import sys
import types
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO = Path(__file__).resolve().parent.parent
APP_PATH = REPO / "src" / "app.py"

OLD_SUBJECT = "OLD SUBJECT"
OLD_BODY = "OLD BODY"
NEW_SUBJECT = "NEW SUBJECT"
NEW_BODY = "NEW BODY"
THREAD_ID = "acme_12345678"


class FakeGraphApp:
    """Minimal stand-in for the LangGraph-compiled app used by app.py."""

    def __init__(self):
        self.state = {
            "analysis": {
                "fit_score": 7,
                "icp_score": 3,
                "business_need_score": 2,
                "recency_score": 1,
                "signal_strength_score": 1,
                "signal": "hiring",
                "rationale": "growing",
                "signal_category": "hiring",
            },
            "contact": {
                "contact_name": "Jane Doe",
                "title": "COO",
                "contact_email": "jane@acme.com",
            },
            "company_research": {},
            "source_urls": [],
            "draft_email": {"subject": OLD_SUBJECT, "body": OLD_BODY},
            "human_feedback": "",
            "approved": False,
            "final_status": "",
        }

    def get_state(self, config):
        return types.SimpleNamespace(
            values=dict(self.state),
            next=(),
        )

    def update_state(self, config, values, as_node=None):
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(self.state.get(key), dict):
                self.state[key] = {**self.state[key], **value}
            else:
                self.state[key] = value

    def stream(self, input, config):
        # Simulate the Copywriter node producing a new draft when feedback exists.
        if self.state.get("human_feedback"):
            self.state["draft_email"] = {
                "subject": NEW_SUBJECT,
                "body": NEW_BODY,
            }
            self.state["human_feedback"] = ""
        return []


def main():
    crm_received = []

    orchestrator = types.ModuleType("orchestrator")
    orchestrator.app = FakeGraphApp()
    orchestrator.AgentState = dict
    orchestrator.generate_leads = lambda profile: ["Acme Co"]
    orchestrator.QUALIFIED_SCORE = 6

    def fake_crm_node(state):
        crm_received.append(state)
        return {"final_status": "Saved to CRM"}

    orchestrator.crm_node = fake_crm_node

    email_service = types.ModuleType("email_service")
    email_service.send_email = lambda to, subject, body: None

    sys.modules["orchestrator"] = orchestrator
    sys.modules["email_service"] = email_service

    at = AppTest.from_file(str(APP_PATH), default_timeout=60).run()

    # Seed the batch results so the render loop shows the company card.
    at.session_state["batch_companies"] = ["Acme Co"]
    at.session_state["threads"] = {"Acme Co": THREAD_ID}
    at.run()

    assert not at.exception, f"Unexpected exceptions: {[e.value for e in at.exception]}"

    subject = lambda: at.text_input(key=f"subject_{THREAD_ID}").value
    body = lambda: at.text_area(key=f"body_{THREAD_ID}").value

    # 1. Initial draft is shown.
    assert subject() == OLD_SUBJECT
    assert body() == OLD_BODY

    # 2. Human Approval is still required before Save/Send.
    assert at.button(key=f"save_{THREAD_ID}").disabled is True
    assert at.button(key=f"send_{THREAD_ID}").disabled is True

    # 3. Click Rewrite with feedback.
    at.text_area(key=f"feedback_{THREAD_ID}").set_value("Make it shorter")
    at.button(key=f"rewrite_{THREAD_ID}").click()
    at.run()

    assert not at.exception, (
        f"Unexpected exceptions after rewrite: {[e.value for e in at.exception]}"
    )

    # 4. The UI widgets now show the NEW draft.
    assert subject() == NEW_SUBJECT, f"subject still shows {subject()!r}"
    assert body() == NEW_BODY, f"body still shows {body()!r}"

    # 5. The authoritative graph state holds the NEW draft.
    assert orchestrator.app.state["draft_email"] == {
        "subject": NEW_SUBJECT,
        "body": NEW_BODY,
    }

    # 6. Rewrite did NOT trigger a CRM save.
    assert crm_received == []

    # 7. Approve, then Save to CRM uses the NEW draft.
    at.button(key=f"approve_{THREAD_ID}").click()
    at.run()

    assert at.button(key=f"save_{THREAD_ID}").disabled is False

    at.button(key=f"save_{THREAD_ID}").click()
    at.run()

    assert not at.exception, f"Unexpected exceptions after save: {[e.value for e in at.exception]}"
    assert crm_received, "crm_node was not called after Save"
    assert crm_received[-1]["draft_email"] == {
        "subject": NEW_SUBJECT,
        "body": NEW_BODY,
    }

    print("REWRITE_SYNC_TEST_OK")
    print(f"subject -> {subject()!r}")
    print(f"body    -> {body()!r}")


if __name__ == "__main__":
    main()
