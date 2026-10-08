import os
import time
from urllib.parse import urlsplit, urlunsplit

import requests
from ddgs import DDGS

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _clean_url(url: str) -> str:
    """Normalize URLs for duplicate detection."""
    if not url:
        return ""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _normalize(result: dict, search_type: str) -> dict | None:
    """Convert DDGS results into a common structure."""
    url = result.get("href") or result.get("url")
    if not url:
        return None

    return {
        "title": result.get("title", "").strip(),
        "body": (
            result.get("body")
            or result.get("snippet")
            or result.get("description")
            or ""
        ).strip(),
        "url": url,
        "date": result.get("date", ""),
        "source": result.get("source", ""),
        "type": search_type,
    }


def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Run a single Tavily search and normalize results into the common structure."""
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        response = requests.post(
            TAVILY_ENDPOINT,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Tavily search failed for '{query}': {e}")
        return []

    results = []
    for item in data.get("results", []):
        url = item.get("url", "")
        if not url:
            continue

        results.append({
            "title": (item.get("title") or "").strip(),
            "body": (item.get("content") or "").strip(),
            "url": url,
            "date": "",
            "source": urlsplit(url).netloc,
            "type": "web",
        })

    return results


def _tavily_search_queries(queries: list[str], max_results: int = 5) -> list[dict]:
    """Run Tavily across multiple queries and deduplicate by URL."""
    results = []
    seen = set()

    for query in queries:
        for item in _tavily_search(query, max_results=max_results):
            key = _clean_url(item["url"])
            if key and key not in seen:
                seen.add(key)
                results.append(item)

    return results


def _ddgs_search_queries(
    queries: list[str],
    max_results: int = 10,
    include_news: bool = True,
    news_queries: list[str] | None = None,
) -> list[dict]:
    """Run DuckDuckGo searches across queries and deduplicate by URL.

    Used as the fallback provider when Tavily is unavailable or returns no
    results. When ``include_news`` is set, a separate news-only pass runs over
    ``news_queries``.
    """
    results = []
    seen = set()
    news_queries = news_queries or []

    with DDGS() as ddgs:
        for query in queries:
            try:
                web_results = ddgs.text(query, max_results=max_results, backend="html")
                if web_results:
                    for item in web_results:
                        result = _normalize(item, "web")
                        if result:
                            key = _clean_url(result["url"])
                            if key and key not in seen:
                                seen.add(key)
                                results.append(result)
            except Exception as e:
                print(f"Web search failed for '{query}': {e}")

            time.sleep(1)

        if include_news and hasattr(ddgs, "news"):
            for query in news_queries:
                try:
                    news_results = ddgs.news(query, max_results=3)
                    if news_results:
                        for item in news_results:
                            result = _normalize(item, "news")
                            if result:
                                key = _clean_url(result["url"])
                                if key and key not in seen:
                                    seen.add(key)
                                    results.append(result)
                except Exception as e:
                    print(f"News search failed for '{query}': {e}")

                time.sleep(2)

    return results


def search_queries(
    queries: list[str],
    max_results: int = 10,
    include_news: bool = True,
    news_queries: list[str] | None = None,
) -> list[dict]:
    """Run web searches using Tavily (primary) with DDGS fallback (optional)."""
    news_queries = news_queries or []

    # Tavily is the primary provider. Fall back to DDGS when the key is
    # missing or Tavily returns no useful results.
    if os.getenv("TAVILY_API_KEY", "").strip():
        combined = list(queries)
        if include_news:
            combined.extend(news_queries)

        results = _tavily_search_queries(combined, max_results=max_results)
        if results:
            return results

    return _ddgs_search_queries(
        queries,
        max_results=max_results,
        include_news=include_news,
        news_queries=news_queries,
    )


def _discovery_queries(profile: str) -> list[str]:
    """Build focused discovery queries from the target profile."""
    compact = " ".join(profile.split())
    icp = compact

    # If the profile opens with an "ideal customers are ..." lead-in, search
    # only from that point onward so the query describes the target rather
    # than the surrounding instructions.
    lowered = compact.lower()
    for marker in ("ideal customers are", "target companies", "companies that"):
        idx = lowered.find(marker)
        if idx != -1:
            icp = compact[idx:]
            break

    base = icp if len(icp) <= 220 else icp[:220]

    return [
        base,
        f"{base} companies",
        f"{base} hiring OR expansion OR growth",
    ]


def search_target_companies(
    target_profile: str,
    max_results_per_query: int = 6,
) -> list[dict]:
    """Find real candidate companies matching the target profile via web search."""
    profile = target_profile.strip()
    if not profile:
        return []

    queries = _discovery_queries(profile)

    return search_queries(
        queries,
        max_results=max_results_per_query,
        include_news=False,
    )


def search_company(
    company: str,
    max_results_per_query: int = 5,
) -> list[dict]:
    """Research company identity, growth, hiring, leadership and operations."""
    queries = [
        f"{company} company profile overview",
        f"{company} recent news",
        f"{company} hiring careers jobs",
        f"{company} leadership team executives",
        f"{company} operations expansion facilities supply chain",
    ]

    news_queries = [
        f"{company} news",
    ]

    return search_queries(
        queries,
        max_results=max_results_per_query,
        include_news=True,
        news_queries=news_queries,
    )


def search_contact(
    company: str,
    analysis: dict | None = None,
    max_results_per_query: int = 4,
) -> list[dict]:
    """Find publicly available decision-maker and email information."""
    analysis = analysis or {}
    signal = analysis.get("signal", "")

    queries = [
        f"{company} leadership team executives",
        f"{company} contact email address",
        f"{company} COO OR operations director OR head of operations",
    ]

    if signal:
        # Keep the signal concise so it stays a clean search term.
        short_signal = " ".join(signal.replace('"', '').split()[:5])
        if short_signal:
            queries.append(
                f"{company} {short_signal} director OR head OR executive"
            )

    return search_queries(
        queries,
        max_results=max_results_per_query,
        include_news=False,
    )


def format_search_results(
    results: list[dict],
    max_chars: int = 18000,
) -> str:
    """Convert search results into compact LLM-readable evidence."""
    sections = []
    total = 0

    for i, result in enumerate(results, 1):
        section = (
            f"[SOURCE {i} | {result.get('type', 'web')}]\n"
            f"Title: {result.get('title', '')}\n"
            f"Date: {result.get('date', '')}\n"
            f"Source: {result.get('source', '')}\n"
            f"URL: {result.get('url', '')}\n"
            f"Content: {result.get('body', '')}\n"
        )

        if total + len(section) > max_chars:
            break

        sections.append(section)
        total += len(section)

    return "\n".join(sections)


def get_source_urls(results: list[dict]) -> list[str]:
    """Return unique source URLs."""
    urls = []
    seen = set()

    for result in results:
        url = result.get("url", "")
        key = _clean_url(url)

        if key and key not in seen:
            seen.add(key)
            urls.append(url)

    return urls
