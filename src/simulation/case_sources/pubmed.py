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
    # Search with MeSH AND title/abstract so non-standard terms still hit
    term = f"case reports[pt] AND ({specialty}[mesh] OR {specialty}[tiab]) AND {years}[dp]"
    params = {
        "db": "pubmed",
        "term": term,
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

            # Extract PMCID if available
            pmcid = ""
            for aid in article_el.findall(".//ArticleId"):
                if aid.get("IdType") == "pmc":
                    pmcid = aid.text
                    break

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
                "pmcid": pmcid,
                "title": title_el.text if title_el is not None else "",
                "abstract": "\n".join(abstract_parts),
                "mesh_terms": mesh_terms,
                "journal": journal_el.text if journal_el is not None else "",
                "year": year_el.text if year_el is not None else "",
            })
    except ET.ParseError as e:
        logger.error("Failed to parse PubMed XML: %s", e)

    return results


# ── Image Extraction ──────────────────────────────────────────────────────────

async def extract_publisher_images(pmid: str, doi: str = None) -> list[dict]:
    """
    Scrape clinical figures from publisher websites when PMC isn't available.
    Supports: Frontiers, BMC, PLOS, Elsevier, Wiley, Nature.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 MedSimulation/1.0 (medical education bot)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Try to fetch from PubMed page which may have links to full text
    pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    html = None
    publisher_url = None

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            r = await client.get(pubmed_url, headers=headers)
            if r.status_code == 200:
                html = r.text
                # Look for full-text links
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if any(x in href.lower() for x in ["frontiers", "doi.org", "biomedcentral", "plos"]):
                        publisher_url = href if href.startswith("http") else f"https://pubmed.ncbi.nlm.nih.gov{href}"
                        break
        except Exception as e:
            logger.warning("PubMed page fetch failed for %s: %s", pmid, e)

    # If we found a publisher link, try it
    if publisher_url:
        try:
            r = await client.get(publisher_url, headers=headers)
            if r.status_code == 200:
                html = r.text
                logger.info("Fetched publisher page: %s", publisher_url)
        except Exception as e:
            logger.warning("Publisher page fetch failed: %s", e)

    if not html:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    images = []

    # Frontiers-specific: figures are in <figure class="fig"> with <img class="img">
    for figure in soup.find_all("figure", class_=["fig", "figure", "Fig"]):
        img = figure.find("img")
        if not img:
            continue
        src = img.get("src", "") or img.get("data-src", "")
        if not src or src.startswith("data:"):
            continue

        # Resolve relative URLs for common publishers
        if src.startswith("/"):
            if "frontiers" in publisher_url:
                src = "https://www.frontiersin.org" + src
            elif "biomedcentral" in publisher_url:
                src = "https://bmcmededuc.biomedcentral.com" + src

        # Find caption
        caption = img.get("alt", "")
        figcaption = figure.find("figcaption")
        if figcaption:
            caption = figcaption.get_text(separator=" ", strip=True)

        # Also check for figure description in sibling/parent elements
        if not caption or len(caption) < 20:
            for p in figure.find_parents(["div", "section"]):
                desc = p.get_text(separator=" ", strip=True)
                if "figure" in desc.lower() or "fig" in desc.lower():
                    caption = desc[:500]
                    break

        if not caption or len(caption) < 10:
            caption = "Clinical figure"

        images.append(_make_image_entry(pmid, src, caption, len(images)))

    # Fallback: scan all images with clinical-looking URLs or captions
    if not images:
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if not src or src.startswith("data:"):
                continue
            # Skip tiny icons, ads, etc.
            if any(x in src.lower() for x in ["icon", "logo", "avatar", "ads", "pixel"]):
                continue

            caption = img.get("alt", "")
            # Check parent for caption
            for ancestor in img.parents:
                if ancestor.name in ["figure", "div", "td"]:
                    for cls in ancestor.get("class", []):
                        if "fig" in str(cls).lower() or "caption" in str(cls).lower():
                            cap = ancestor.find(text=True, recursive=False)
                            if cap:
                                caption = str(cap).strip()
                            break

            if caption and len(caption) > 20:
                images.append(_make_image_entry(pmid, src, caption, len(images)))
                if len(images) >= 5:
                    break

    logger.info("Extracted %d images from publisher (PMID: %s)", len(images), pmid)
    return images


async def extract_pmc_images(pmcid: str) -> list[dict]:
    """Scrape image URLs and captions from a PMC article HTML page."""
    headers = {"User-Agent": "Mozilla/5.0 MedSimulation/1.0 (medical education)"}
    html = None

    # PMC moved to pmc.ncbi.nlm.nih.gov; try both with redirect following
    for url in [
        f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
        f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
    ]:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    html = r.text
                    break
            except Exception as e:
                logger.warning("PMC fetch failed for %s at %s: %s", pmcid, url, e)

    if not html:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    images = []

    # Clinical figures on PMC always come from the PMC CDN blob path.
    # Use this as a whitelist — scan every <img> on the page and accept only
    # images whose URL matches a known clinical-image CDN pattern.
    # This avoids false-positives from UI icons that happen to sit inside
    # <figure> tags on the new PMC site design.
    CLINICAL_CDN = ("cdn.ncbi.nlm.nih.gov/pmc/blobs/", "/pmc/articles/")

    for img in soup.find_all("img"):
        src = img.get("src", "") or img.get("data-src", "")
        if not any(pat in src for pat in CLINICAL_CDN):
            continue

        # Resolve relative URLs
        if src.startswith("/"):
            src = "https://www.ncbi.nlm.nih.gov" + src

        # Walk up the DOM to find the nearest caption
        caption = img.get("alt", "")
        for ancestor in img.parents:
            # <figure> with <figcaption>
            fc = ancestor.find("figcaption") if hasattr(ancestor, "find") else None
            if fc:
                caption = fc.get_text(separator=" ", strip=True)
                break
            # Old PMC div.fig with div.caption
            if ancestor.get("class") and "fig" in ancestor.get("class", []):
                cap_div = ancestor.find("div", class_="caption")
                if cap_div:
                    caption = cap_div.get_text(separator=" ", strip=True)
                break

        if not caption:
            caption = "Clinical image"

        images.append(_make_image_entry(pmcid, src, caption, len(images)))

    logger.info("Extracted %d images from PMC %s", len(images), pmcid)
    return images


def _make_image_entry(pmcid: str, src: str, caption: str, idx: int) -> dict:
    """Build an imaging_study dict from a scraped PMC figure."""
    mod = caption.lower()
    if any(x in mod for x in ["x-ray", "radiograph", "cxr", "plain film"]):
        modality = "XR"
    elif any(x in mod for x in ["ct ", "computed tomography", "fluoroscop"]):
        modality = "CT"
    elif any(x in mod for x in ["mri", "magnetic resonance"]):
        modality = "MRI"
    elif any(x in mod for x in ["ecg", "electrocardiogram", "ekg"]):
        modality = "ECG"
    elif any(x in mod for x in ["ultrasound", "sonogram", "uss", "pocus"]):
        modality = "US"
    else:
        modality = "PATH"

    short_desc = (caption[:80] + "…") if len(caption) > 80 else caption
    return {
        "study_id": f"IMG-{pmcid}-{idx + 1}",
        "modality": modality,
        "description": short_desc,
        "file_path": src,
        "findings": caption,
        "thumbnail": src,
    }


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def abstract_to_case(abstract: dict, vllm_client: Any) -> dict:
    """
    Transform a PubMed abstract into a structured ClinicalCase dict
    using the AI case generator.
    """
    abstract_text = abstract['abstract'][:1500]  # cap to stay within 4096 token context
    source_text = (
        f"Published Case Report (PMID: {abstract['pmid']})\n"
        f"Title: {abstract['title']}\n"
        f"Journal: {abstract['journal']} ({abstract['year']})\n"
        f"MeSH Terms: {', '.join(abstract['mesh_terms'])}\n\n"
        f"Abstract:\n{abstract_text}"
    )

    case_dict = await generate_case(
        vllm_client,
        source_text=source_text,
        source_type="pubmed",
        source_ref=abstract["pmid"],
    )

    # Phase G: Extract images
    images = []

    # Try PMC first (preferred - higher quality images)
    if abstract.get("pmcid"):
        try:
            images = await extract_pmc_images(abstract["pmcid"])
            if images:
                logger.info("Extracted %d images from PMC %s", len(images), abstract["pmcid"])
        except Exception as e:
            logger.warning("Error extracting PMC images for %s: %s", abstract["pmcid"], e)

    # If no PMC images, try publisher website scraping
    if not images:
        try:
            images = await extract_publisher_images(abstract["pmid"])
            if images:
                logger.info("Extracted %d images from publisher for PMID %s", len(images), abstract["pmid"])
        except Exception as e:
            logger.warning("Error extracting publisher images for %s: %s", abstract["pmid"], e)

    # Add images to case if found
    if images:
        if "imaging_studies" not in case_dict:
            case_dict["imaging_studies"] = []
        case_dict["imaging_studies"].extend(images)

    return case_dict
