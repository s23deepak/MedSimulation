#!/usr/bin/env uv run python
"""
Test image extraction locally before deployment.

Tests the hybrid extraction pipeline:
1. PMC extraction (preferred)
2. Direct publisher scraping
3. Firecrawl fallback

Usage:
    python scripts/test_image_extraction.py [PMID]

Examples:
    # Test with a Frontiers article (has PMCID)
    python scripts/test_image_extraction.py 41959588

    # Test with a PLOS article
    python scripts/test_image_extraction.py 38530852
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment from .env
from dotenv import load_dotenv
load_dotenv()

from src.simulation.case_sources.pubmed import (
    fetch_pubmed_abstract,
    extract_pmc_images,
    extract_publisher_images,
    abstract_to_case,
)
from src.simulation.vllm_client import VLLMClient


async def test_extraction(pmid: str = None, url: str = None):
    """Test image extraction for a given PMID or direct URL."""
    print(f"\n{'='*70}")
    if pmid:
        print(f"Testing image extraction for PMID: {pmid}")
    elif url:
        print(f"Testing image extraction for URL: {url[:60]}...")
    print(f"{'='*70}\n")

    # Check Firecrawl API key
    fc_key = os.getenv("FIRECRAWL_API_KEY")
    print(f"Firecrawl API key: {'Set' if fc_key else 'NOT SET'}")
    if fc_key:
        print(f"  Key prefix: {fc_key[:8]}...")
    print()

    # Direct URL test (no PMID needed)
    if url:
        print("Testing Firecrawl extraction directly...")
        from src.simulation.case_sources.pubmed import extract_with_firecrawl
        images = await extract_with_firecrawl(url, 'url-test')
        print(f"   Found {len(images)} images")
        for i, img in enumerate(images, 1):
            print(f"   {i}. {img['modality']}: {img['description'][:50]}...")
            print(f"      URL: {img['file_path'][:70]}...")

        print(f"\n{'='*70}")
        print(f"SUMMARY: {len(images)} images extracted via Firecrawl")
        print(f"{'='*70}\n")
        return len(images) > 0

    # PMID-based extraction
    print("1. Fetching PubMed abstract...")
    abstract = await fetch_pubmed_abstract(pmid)
    print(f"   Title: {abstract['title'][:70]}...")
    print(f"   Journal: {abstract['journal']} ({abstract['year']})")
    print(f"   PMCID: {abstract.get('pmcid', 'Not available')}")

    # Step 2: Try PMC extraction
    pmc_images = []
    if abstract.get("pmcid"):
        print(f"\n2. Extracting from PMC ({abstract['pmcid']})...")
        pmc_images = await extract_pmc_images(abstract["pmcid"])
        print(f"   Found {len(pmc_images)} images")
        for i, img in enumerate(pmc_images[:3], 1):
            print(f"   {i}. {img['modality']}: {img['description'][:50]}...")
    else:
        print("\n2. Skipped PMC (no PMCID)")

    # Step 3: Try publisher extraction (includes Firecrawl fallback)
    pub_images = []
    print("\n3. Extracting from publisher (with Firecrawl fallback)...")
    pub_images = await extract_publisher_images(pmid)
    print(f"   Found {len(pub_images)} images")
    for i, img in enumerate(pub_images[:3], 1):
        print(f"   {i}. {img['modality']}: {img['description'][:50]}...")
        print(f"      URL: {img['file_path'][:70]}...")

    # Summary
    total = len(pmc_images) + len(pub_images)
    print(f"\n{'='*70}")
    print(f"SUMMARY: {total} images extracted")
    print(f"  - PMC: {len(pmc_images)} images")
    print(f"  - Publisher: {len(pub_images)} images")
    print(f"{'='*70}\n")

    return total > 0


async def test_full_pipeline(pmid: str):
    """Test the full abstract_to_case pipeline including AI generation."""
    print(f"\n{'='*70}")
    print(f"Testing full pipeline (AI + images) for PMID: {pmid}")
    print(f"{'='*70}\n")

    # Check VLLM connection
    print("Checking VLLM connection...")
    try:
        vllm_client = VLLMClient()
        test_response = await vllm_client.chat(
            messages=[{"role": "user", "content": "Say hello in one word"}]
        )
        print(f"VLLM connected: {test_response.strip()[:20]}...")
    except Exception as e:
        print(f"VLLM not available: {e}")
        print("Skipping AI generation test - images only\n")
        return await test_extraction(pmid)

    # Fetch abstract
    print("\n1. Fetching abstract...")
    abstract = await fetch_pubmed_abstract(pmid)
    print(f"   Title: {abstract['title'][:60]}...")

    # Run full pipeline
    print("\n2. Running AI case generation + image extraction...")
    case = await abstract_to_case(abstract, vllm_client)

    print(f"\n{'='*70}")
    print(f"GENERATED CASE:")
    print(f"  Title: {case.get('title', 'N/A')[:60]}...")
    print(f"  Specialty: {case.get('specialty', 'N/A')}")
    print(f"  Imaging studies: {len(case.get('imaging_studies', []))} images")

    for img in case.get('imaging_studies', [])[:3]:
        print(f"    - {img['modality']}: {img['description'][:50]}...")

    print(f"{'='*70}\n")
    return len(case.get('imaging_studies', [])) > 0


async def main():
    # Check for URL argument
    if len(sys.argv) > 1 and (sys.argv[1].startswith('http') or sys.argv[1].startswith('www')):
        url = sys.argv[1]
        success = await test_extraction(url=url)
    elif len(sys.argv) > 1:
        pmid = sys.argv[1]
        success = await test_extraction(pmid=pmid)
    else:
        # Default test PMID (Frontiers article with images)
        pmid = "41959588"
        print(f"No PMID provided, using default: {pmid}\n")
        success = await test_extraction(pmid=pmid)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
