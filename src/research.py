from urllib.parse import urlsplit, urlunsplit
from ddgs import DDGS


def _clean_url(url: str) -> str:
    """Normalize URLs for duplicate detection."""
    if not url:
        return ""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def _normalize(result: dict, search_type: str) -> dict | None:
    """Convert DDGS results into a common structure."""
    url = result.get("href") or result.get("url")
    if not url:
        return None

    return {
        "title": result.get("title", "").strip(),
        "body": (result.get("body") or result.get("snippet") or result.get("description") or "").strip(),
        "url": url,
        "date": result.get("date", ""),
        "source": result.get("source", ""),
        "type": search_type,
    }


def search_queries(queries: list[str], max_results: int = 5, include_news: bool = True) -> list[dict]:
    """Run web searches and optionally supplement them with news results."""
    results, seen = [], set()

    with DDGS() as ddgs:
        for query in queries:
            try:
                web_results = ddgs.text(query, max_results=max_results)

                for item in web_results:
                    result = _normalize(item, "web")
                    if not result:
                        continue

                    key = _clean_url(result["url"])
                    if key and key not in seen:
                        seen.add(key)
                        results.append(result)

            except Exception as e:
                print(f"Web search failed for '{query}': {e}")

            if include_news and hasattr(ddgs, "news"):
                try:
                    news_results = ddgs.news(query, max_results=max_results)

                    for item in news_results:
                        result = _normalize(item, "news")
                        if not result:
                            continue

                        key = _clean_url(result["url"])
                        if key and key not in seen:
                            seen.add(key)
                            results.append(result)

                except Exception:
                    # News is supplementary; web results remain sufficient.
                    pass

    return results


def search_target_companies(target_profile: str, max_results_per_query: int = 5) -> list[dict]:
    """Find companies matching the target customer profile."""
    queries = [
        target_profile,
        f"{target_profile} companies",
        f"{target_profile} hiring jobs",
        f"{target_profile} growth expansion",
        f"{target_profile} operations supply chain",
        f"{target_profile} recent news",
    ]
    return search_queries(queries, max_results_per_query, include_news=True)


def search_company(company: str, max_results_per_query: int = 5) -> list[dict]:
    """Research company identity, growth, hiring, leadership and operations."""
    queries = [
        f'"{company}" official company',
        f'"{company}" careers jobs hiring',
        f'"{company}" recent news growth expansion',
        f'"{company}" operations logistics supply chain',
        f'"{company}" leadership executive management',
        f'"{company}" technology digital transformation',
        f'"{company}" employees revenue funding',
    ]
    return search_queries(queries, max_results_per_query, include_news=True)


def search_contact(company: str, analysis: dict | None = None, max_results_per_query: int = 5) -> list[dict]:
    """Find publicly available information about relevant decision-makers."""
    signal = (analysis or {}).get("signal", "")

    queries = [
        f'"{company}" COO operations director',
        f'"{company}" "head of operations"',
        f'"{company}" "supply chain" director',
        f'"{company}" leadership management',
        f'"{company}" {signal} executive',
        f'"{company}" executive email contact',
    ]

    return search_queries(queries, max_results_per_query, include_news=False)


def format_search_results(results: list[dict], max_chars: int = 18000) -> str:
    """Convert search results into compact LLM-readable evidence."""
    sections, total = [], 0

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
    urls, seen = [], set()

    for result in results:
        url = result.get("url", "")
        key = _clean_url(url)

        if key and key not in seen:
            seen.add(key)
            urls.append(url)

    return urls