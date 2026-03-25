"""
Wiley Open Access case report fetcher.

Uses the Crossref API (free, no auth required) to search Wiley's
*Clinical Case Reports* journal (ISSN 2050-0904), which is published
under CC BY open access license.

For full-text access, Wiley's TDM endpoint requires a Crossref
click-through token. Abstract-only access works without auth.

Env vars
--------
CROSSREF_MAILTO : email for polite pool (faster rate limits)
CROSSREF_TDM_TOKEN : optional Crossref click-through token for full text
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .ai_generator import generate_case

logger = logging.getLogger(__name__)

# ── Crossref API ──────────────────────────────────────────────────────────────

CROSSREF_API = "https://api.crossref.org/works"

# Wiley Clinical Case Reports — open access, CC BY
WILEY_CCR_ISSN = "2050-0904"

# Additional Wiley medical journals (may require subscription for full text)
WILEY_JOURNALS = {
    "clinical_case_reports": "2050-0904",
    "health_science_reports": "2398-8835",
    "clinical_and_experimental_medicine": "1591-8890",
}


async def search_wiley_cases(
    issn: str = WILEY_CCR_ISSN,
    from_date: str = "2024-01-01",
    query: str = "",
    max_results: int = 20,
) -> list[dict]:
    """
    Search Crossref for Wiley case reports.

    Parameters
    ----------
    issn : str
        Journal ISSN (default: Clinical Case Reports)
    from_date : str
        Earliest publication date (YYYY-MM-DD)
    query : str
        Additional search terms (e.g. "myocardial infarction")
    max_results : int
        Max articles to return

    Returns
    -------
    list[dict]
        [{doi, title, abstract, published_date, url}, ...]
    """
    params = {
        "filter": f"issn:{issn},type:journal-article,from-pub-date:{from_date}",
        "rows": min(max_results, 100),
        "select": "DOI,title,abstract,published-print,URL",
        "sort": "published",
        "order": "desc",
    }

    if query:
        params["query"] = query

    # Crossref "polite pool" — faster rate limits with an identified email
    mailto = os.getenv("CROSSREF_MAILTO", "")
    if mailto:
        params["mailto"] = mailto

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(CROSSREF_API, params=params)
        r.raise_for_status()

    items = r.json().get("message", {}).get("items", [])

    results = []
    for item in items:
        title_parts = item.get("title", [])
        title = title_parts[0] if title_parts else "Untitled"

        # Abstract may contain JATS XML tags — strip basic tags
        abstract = item.get("abstract", "")
        abstract = _strip_jats_tags(abstract)

        # Published date
        pub_parts = item.get("published-print", {}).get("date-parts", [[]])
        pub_date = "-".join(str(p) for p in pub_parts[0]) if pub_parts[0] else ""

        results.append({
            "doi": item.get("DOI", ""),
            "title": title,
            "abstract": abstract,
            "published_date": pub_date,
            "url": item.get("URL", ""),
        })

    logger.info(
        "Wiley/Crossref search (ISSN=%s, query='%s'): found %d results",
        issn, query, len(results),
    )
    return results


async def fetch_wiley_fulltext(doi: str) -> str | None:
    """
    Fetch full article text via Wiley TDM endpoint.
    Requires CROSSREF_TDM_TOKEN env var.

    Returns None if token is not set or request fails.
    """
    token = os.getenv("CROSSREF_TDM_TOKEN", "")
    if not token:
        logger.info("No CROSSREF_TDM_TOKEN — using abstract only for DOI %s", doi)
        return None

    url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}"
    headers = {
        "CR-Clickthrough-Client-Token": token,
        "Accept": "text/plain",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.warning("Failed to fetch Wiley full text for %s: %s", doi, e)
        return None

# ── Image Extraction ──────────────────────────────────────────────────────────

async def extract_wiley_images(url: str) -> list[dict]:
    """Scrape image URLs and captions from a Wiley Open Access HTML page."""
    if not url or "onlinelibrary.wiley.com" not in url:
        return []
        
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 MedSimulation Bot"})
            if r.status_code != 200:
                return []
        except Exception as e:
            logger.warning("Failed to fetch Wiley page %s: %s", url, e)
            return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    
    images = []
    # Wiley figures are typically inside <figure>
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        if not img or not img.get("src"):
            continue
            
        src = img["src"]
        if src.startswith("/"):
            src = "https://onlinelibrary.wiley.com" + src
            
        # Ignore structural/UI images
        if "icon" in src.lower() or "logo" in src.lower() or "altmetric" in src.lower():
            continue
            
        # Get caption
        caption_tag = fig.find("figcaption")
        caption = caption_tag.get_text(separator=" ", strip=True) if caption_tag else "Clinical Image"
        
        # Determine modality
        mod = caption.lower()
        if any(x in mod for x in ["x-ray", "radiograph", "cxr"]):
            modality = "XR"
        elif any(x in mod for x in ["ct ", "computed tomography"]):
            modality = "CT"
        elif any(x in mod for x in ["mri", "magnetic resonance"]):
            modality = "MRI"
        elif any(x in mod for x in ["ecg", "electrocardiogram"]):
            modality = "ECG"
        elif any(x in mod for x in ["ultrasound", "sonogram", "uss", "pocus"]):
            modality = "US"
        else:
            modality = "PATH"
            
        images.append({
            "study_id": f"IMG-WILEY-{len(images)+1}",
            "modality": modality,
            "description": (caption[:60] + "...") if len(caption) > 60 else caption,
            "file_path": src,
            "findings": caption,
            "thumbnail": src
        })
        
    return images

# ── Full pipeline ─────────────────────────────────────────────────────────────

async def wiley_article_to_case(article: dict, vllm_client: Any) -> dict:
    """
    Transform a Wiley article (from search_wiley_cases) into a
    structured ClinicalCase dict.

    Tries full text first (if TDM token available), falls back to abstract.
    """
    # Try full text
    full_text = await fetch_wiley_fulltext(article["doi"])

    if full_text:
        content = full_text[:4000]  # Trim to fit context
        content_label = "Full Text (truncated)"
    else:
        content = article.get("abstract", "")
        content_label = "Abstract"

    if not content:
        raise ValueError(f"No content available for DOI {article['doi']}")

    source_text = (
        f"Published Case Report from Wiley Clinical Case Reports\n"
        f"DOI: {article['doi']}\n"
        f"Title: {article['title']}\n"
        f"Published: {article.get('published_date', 'Unknown')}\n"
        f"Content Type: {content_label}\n\n"
        f"{content}"
    )

    case_dict = await generate_case(
        vllm_client,
        source_text=source_text,
        source_type="wiley",
        source_ref=article["doi"],
    )
    
    # Phase G: Extract images if public URL is available
    if article.get("url"):
        try:
            images = await extract_wiley_images(article["url"])
            if images:
                if "imaging_studies" not in case_dict:
                    case_dict["imaging_studies"] = []
                case_dict["imaging_studies"].extend(images)
                logger.info("Extracted %d images from %s", len(images), article["url"])
        except Exception as e:
            logger.warning("Error extracting images for %s: %s", article["url"], e)
            
    return case_dict

# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_jats_tags(text: str) -> str:
    """Remove basic JATS XML tags from Crossref abstract text."""
    import re
    # Remove XML tags like <jats:p>, <jats:italic>, etc.
    text = re.sub(r"</?jats:[^>]+>", "", text)
    # Remove other HTML-like tags
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
    return text.strip()
