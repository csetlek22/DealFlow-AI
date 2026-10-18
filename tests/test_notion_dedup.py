"""Regression tests for Notion duplicate-company filtering.

These tests verify the notion-client 3.x data-source read path, fail-safe
behavior when the CRM is unreachable, and exclusion of companies already
present in the CRM (including the final post-discovery safety check).

They are hermetic: no network access and no Streamlit import. They exercise the
real ``orchestrator`` module with a fake Notion client and a fake discovery
runner.
"""

import io
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

import orchestrator as o
from schemas import CompanyCandidate


def _page(company, email=None):
    properties = {
        "Company": {
            "type": "title",
            "title": [{"type": "text", "plain_text": company}],
        },
    }

    if email:
        properties["Contact Email"] = {"type": "email", "email": email}

    return {"object": "page", "properties": properties}


class FakeDataSources:
    """Mimics notion-client 3.x ``data_sources.query`` (no ``databases.query``)."""

    def __init__(self, batches):
        self.batches = batches
        self.cursors = []
        self.call = 0

    def query(self, **kwargs):
        self.cursors.append(kwargs.get("start_cursor"))
        results, has_more, next_cursor = self.batches[self.call]
        self.call += 1
        return {"results": results, "has_more": has_more, "next_cursor": next_cursor}


class FakeDatabases:
    """Mimics notion-client 3.x databases endpoint (retrieve only, no query)."""

    def __init__(self, data_source_id):
        self.data_source_id = data_source_id

    def retrieve(self, **kwargs):
        return {"data_sources": [{"id": self.data_source_id}]}


class FakeNotion:
    def __init__(self, data_source_id, batches):
        self.databases = FakeDatabases(data_source_id)
        self.data_sources = FakeDataSources(batches)


def test_get_existing_crm_companies_uses_v3_data_sources():
    batches = [
        ([_page("Acme Corp"), _page("Globex", email="jane@globex.com")], True, "c2"),
        ([_page("Beta LLC", email="ops@beta.io")], False, None),
    ]

    original = o.notion
    fake = FakeNotion("ds-123", batches)
    o.notion = fake

    try:
        names, domains = o.get_existing_crm_companies()
    finally:
        o.notion = original

    assert names == {"Acme Corp", "Globex", "Beta LLC"}, names
    assert domains == {"globex.com", "beta.io"}, domains
    # Pagination passed the first next_cursor back as start_cursor.
    assert fake.data_sources.cursors == [None, "c2"]


def test_generate_leads_fail_safe_on_notion_error():
    original = (o.get_existing_crm_companies, o.run_discovery)

    def boom():
        raise RuntimeError("Notion 401 Unauthorized")

    o.get_existing_crm_companies = boom
    o.run_discovery = lambda *a, **k: []

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("dealflow")
    logger.addHandler(handler)

    try:
        result = o.generate_leads("some profile")
    finally:
        o.get_existing_crm_companies, o.run_discovery = original
        logger.removeHandler(handler)

    assert result == [], result
    log = stream.getvalue()
    assert "Notion CRM lookup failed" in log, log
    assert "RuntimeError" in log, log
    assert "Notion 401 Unauthorized" in log, log


def test_generate_leads_excludes_existing_and_returns_new():
    original = (o.get_existing_crm_companies, o.run_discovery)

    def fake_existing():
        return {"Acme Corp"}, {"globex.com"}

    def fake_discovery(llm, profile, **kwargs):
        return [
            CompanyCandidate(company_name="Acme Corp"),
            CompanyCandidate(company_name="Globex Co", website="https://globex.com"),
            CompanyCandidate(company_name="Brand New Co"),
        ]

    o.get_existing_crm_companies = fake_existing
    o.run_discovery = fake_discovery

    try:
        result = o.generate_leads("some profile")
    finally:
        o.get_existing_crm_companies, o.run_discovery = original

    assert [c.company_name for c in result] == ["Brand New Co"], result


def test_unrelated_companies_are_not_matched():
    # Exact (normalized) matching only: a partial name must not be treated as
    # a duplicate of an existing CRM company.
    original = (o.get_existing_crm_companies, o.run_discovery)

    def fake_existing():
        return {"Motion"}, set()

    def fake_discovery(llm, profile, **kwargs):
        return [
            CompanyCandidate(company_name="Motion Industries"),
            CompanyCandidate(company_name="Motion 2"),
            CompanyCandidate(company_name="Motion"),
        ]

    o.get_existing_crm_companies = fake_existing
    o.run_discovery = fake_discovery

    try:
        result = o.generate_leads("some profile")
    finally:
        o.get_existing_crm_companies, o.run_discovery = original

    assert [c.company_name for c in result] == ["Motion Industries", "Motion 2"]


def main():
    test_get_existing_crm_companies_uses_v3_data_sources()
    test_generate_leads_fail_safe_on_notion_error()
    test_generate_leads_excludes_existing_and_returns_new()
    test_unrelated_companies_are_not_matched()
    print("NOTION_DEDUP_TESTS_OK")


if __name__ == "__main__":
    main()
