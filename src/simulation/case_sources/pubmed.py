"""
PubMed case report fetcher.

Uses NCBI E-utilities (free, rate-limited to 3 req/s without API key,
10 req/s with API key) to search for published clinical case reports
and fetch their abstracts.

The abstracts are then passed to the AI case generator to produce
structured ClinicalCase objects.

Env vars
--------
NCBI_API_KEY : optional API key for higher rate limits
  Register at: https://www.ncbi.nlm.nih.gov/account/settings/
"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from .ai_generator import generate_case

logger = logging.getLogger(__name__)

# ── NCBI E-utilities endpoints ────────────────────────────────────────────────

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


# ── Search ────────────────────────────────────────────────────────────────────

async def search_pubmed_cases(
    specialty: str,
    years: str = "2022:2026",
    max_results: int = 20,
) -> list[str]:
    """
    Search PubMed for case reports in a given specialty.

    Parameters
    ----------
    specialty : str
        MeSH term or free text, e.g. "Emergency Medicine", "cardiology"
    years : str
        Publication date range, e.g. "2022:2026"
    max_results : int
        Maximum PMIDs to return (up to 100)

    Returns
    -------
    list[str]
        List of PubMed IDs (PMIDs).
    """
    params = {
        "db": "pubmed",
        "term": f"case reports[pt] AND {specialty}[mesh] AND {years}[dp]",
        "retmax": min(max_results, 100),
        "retmode": "json",
    }
    api_key = os.getenv("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(ESEARCH, params=params)
        r.raise_for_status()
        data = r.json()

    pmids = data.get("esearchresult", {}).get("idlist", [])
    logger.info(
        "PubMed search for '%s' (%s): found %d results",
        specialty, years, len(pmids),
    )
    return pmids


# ── Fetch abstract ────────────────────────────────────────────────────────────

async def fetch_pubmed_abstract(pmid: str) -> dict:
    """
    Fetch the abstract and metadata for a single PMID.

    Returns
    -------
    dict
        {
            "pmid": str,
            "title": str,
            "abstract": str,
            "mesh_terms": list[str],
            "journal": str,
            "year": str,
        }
    """
    params = {
        "db": "pubmed",
        "id": pmid,
        "rettype": "xml",
        "retmode": "xml",
    }
    api_key = os.getenv("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(EFETCH, params=params)
        r.raise_for_status()

    return _parse_pubmed_xml(r.text, pmid)


async def fetch_pubmed_abstracts(pmids: list[str]) -> list[dict]:
    """Fetch abstracts for multiple PMIDs in a single request."""
    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "xml",
        "retmode": "xml",
    }
    api_key = os.getenv("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(EFETCH, params=params)
        r.raise_for_status()

    return _parse_pubmed_xml_multiple(r.text)


def _parse_pubmed_xml(xml_text: str, pmid: str) -> dict:
    """Parse a single PubMed XML response."""
    results = _parse_pubmed_xml_multiple(xml_text)
    if results:
        return results[0]
    return {"pmid": pmid, "title": "", "abstract": "", "mesh_terms": [], "journal": "", "year": ""}


def _parse_pubmed_xml_multiple(xml_text: str) -> list[dict]:
    """Parse PubMed XML containing one or more articles."""
    results = []
    try:
        root = ET.fromstring(xml_text)
        for article_el in root.findall(".//PubmedArticle"):
            pmid_el = article_el.find(".//PMID")
            title_el = article_el.find(".//ArticleTitle")
            journal_el = article_el.find(".//Journal/Title")
            year_el = article_el.find(".//PubDate/Year")

            # Abstract may have multiple sections
            abstract_parts = []
            for abs_el in article_el.findall(".//AbstractText"):
                label = abs_el.get("Label", "")
                text = abs_el.text or ""
                if label:
                    abstract_parts.append(f"{label}: {text}")
                else:
                    abstract_parts.append(text)

            # MeSH headings
            mesh_terms = []
            for mesh_el in article_el.findall(".//MeshHeading/DescriptorName"):
                if mesh_el.text:
                    mesh_terms.append(mesh_el.text)

            results.append({
                "pmid": pmid_el.text if pmid_el is not None else "",
                "title": title_el.text if title_el is not None else "",
                "abstract": "\n".join(abstract_parts),
                "mesh_terms": mesh_terms,
                "journal": journal_el.text if journal_el is not None else "",
                "year": year_el.text if year_el is not None else "",
            })
    except ET.ParseError as e:
        logger.error("Failed to parse PubMed XML: %s", e)

    return results


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def abstract_to_case(abstract: dict, vllm_client: Any) -> dict:
    """
    Transform a PubMed abstract into a structured ClinicalCase dict
    using the AI case generator.
    """
    source_text = (
        f"Published Case Report (PMID: {abstract['pmid']})\n"
        f"Title: {abstract['title']}\n"
        f"Journal: {abstract['journal']} ({abstract['year']})\n"
        f"MeSH Terms: {', '.join(abstract['mesh_terms'])}\n\n"
        f"Abstract:\n{abstract['abstract']}"
    )
    return await generate_case(
        vllm_client,
        source_text=source_text,
        source_type="pubmed",
        source_ref=abstract["pmid"],
    )
