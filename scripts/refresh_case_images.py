#!/usr/bin/env uv run python
"""
Refresh images for all existing cases in the database.

This script:
1. Loads all cases from the database
2. Re-fetches images using the updated extraction pipeline
3. Updates cases with new images

Usage:
    python scripts/refresh_case_images.py [--dry-run] [--limit N]

Options:
    --dry-run    Show what would be updated without modifying database
    --limit N    Only process first N cases
"""

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment from .env
from dotenv import load_dotenv
load_dotenv()

from src.simulation.database import list_db_cases, save_case, _connect
from src.simulation.case_sources.pubmed import (
    fetch_pubmed_abstract,
    extract_pmc_images,
    extract_publisher_images,
)


async def fetch_images_for_case(source: str, source_ref: str) -> list[dict]:
    """Fetch images for a case based on source type."""
    images = []

    if source == "pubmed" and source_ref.isdigit():
        # PMID - try PMC first, then publisher
        pmid = source_ref
        print(f"  Fetching for PMID {pmid}...")

        # Try PMC first
        try:
            abstract = await fetch_pubmed_abstract(pmid)
            if abstract.get("pmcid"):
                print(f"  Found PMCID: {abstract['pmcid']}")
                images = await extract_pmc_images(abstract["pmcid"])
                if images:
                    print(f"  Extracted {len(images)} images from PMC")
        except Exception as e:
            print(f"  PMC fetch failed: {e}")

        # Try publisher if no PMC images
        if not images:
            try:
                images = await extract_publisher_images(pmid)
                if images:
                    print(f"  Extracted {len(images)} images from publisher")
            except Exception as e:
                print(f"  Publisher fetch failed: {e}")

    elif source in ("wiley", "ai_generated") and source_ref.startswith("10."):
        # DOI - try to extract from DOI
        doi = source_ref
        print(f"  Fetching for DOI {doi}...")

        # Try PMC via DOI first
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                # Use NIH ID converter
                r = await client.get(
                    f"https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?ids={doi}&idtype=doi&format=json"
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("records") and len(data["records"]) > 0:
                        pmcid = data["records"][0].get("pmcid")
                        if pmcid:
                            print(f"  Found PMCID via DOI: {pmcid}")
                            images = await extract_pmc_images(pmcid)
                            if images:
                                print(f"  Extracted {len(images)} images from PMC")
                                return images
        except Exception as e:
            print(f"  DOI to PMC conversion failed: {e}")

        # Try publisher URL if no PMC images
        print(f"  No PMC - trying publisher URL...")
        try:
            # Construct publisher URL from DOI
            if doi.startswith("10.1002"):
                url = f"https://onlinelibrary.wiley.com/doi/{doi}"
            elif doi.startswith("10.1136"):
                url = f"https://casereports.bmj.com/lookup/doi/{doi}"
            elif doi.startswith("10.1371"):
                url = f"https://journals.plos.org/plosone/article?id={doi}"
            elif doi.startswith("10.1186"):
                url = f"https://bmcmededuc.biomedcentral.com/articles/{doi}"
            else:
                url = f"https://doi.org/{doi}"

            # Use extract_with_firecrawl directly for URL
            from src.simulation.case_sources.pubmed import extract_with_firecrawl
            images = await extract_with_firecrawl(url, doi.replace("/", "_"))
            if images:
                print(f"  Extracted {len(images)} images via Firecrawl")
        except Exception as e:
            print(f"  Publisher fetch failed: {e}")

    return images


async def refresh_all_cases(dry_run: bool = False, limit: int = None):
    """Refresh images for all cases in the database."""
    print("=" * 70)
    print("CASE IMAGE REFRESH TOOL")
    print("=" * 70)
    print()

    # Load all cases
    all_cases = list_db_cases()
    print(f"Total cases in database: {len(all_cases)}")

    # Filter to cases that might have images (pubmed, wiley, ai_generated with DOI)
    refreshable_cases = []
    for case in all_cases:
        source = case.get("source", "")
        source_ref = case.get("source_ref", "")
        if source == "pubmed" and source_ref.isdigit():
            refreshable_cases.append(case)
        elif source in ("wiley", "ai_generated") and source_ref.startswith("10."):
            refreshable_cases.append(case)

    print(f"Cases eligible for image refresh: {len(refreshable_cases)}")
    print()

    if limit:
        refreshable_cases = refreshable_cases[:limit]
        print(f"Processing first {limit} cases...")
        print()

    # Process each case
    updated = 0
    failed = 0
    no_images = 0

    for i, case in enumerate(refreshable_cases, 1):
        case_id = case["case_id"]
        title = case["title"][:50]
        source = case["source"]
        source_ref = case["source_ref"]

        print(f"[{i}/{len(refreshable_cases)}] {case_id}: {title}...")
        print(f"    Source: {source}, Ref: {source_ref}")

        # Fetch new images
        new_images = await fetch_images_for_case(source, source_ref)

        if new_images:
            print(f"    NEW IMAGES: {len(new_images)} images found")

            # Load full case data
            with _connect() as conn:
                row = conn.execute(
                    "SELECT case_data FROM cases WHERE case_id = ?", (case_id,)
                ).fetchone()

                if row:
                    case_data = json.loads(row["case_data"])

                    # Update images
                    old_images = case_data.get("imaging_studies", [])
                    case_data["imaging_studies"] = new_images

                    if dry_run:
                        print(f"    [DRY RUN] Would update: {len(old_images)} -> {len(new_images)} images")
                        updated += 1
                    else:
                        # Save updated case
                        case_data["case_id"] = case_id  # Ensure case_id is set
                        save_case(
                            case_data,
                            source=source,
                            source_ref=source_ref,
                            status="approved",
                        )
                        print(f"    UPDATED: {len(old_images)} -> {len(new_images)} images")
                        updated += 1
                else:
                    print(f"    ERROR: Case data not found")
                    failed += 1
        else:
            print(f"    NO IMAGES: Could not fetch images")
            no_images += 1

        print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Processed: {len(refreshable_cases)} cases")
    print(f"Updated:   {updated} cases")
    print(f"Failed:    {failed} cases")
    print(f"No images: {no_images} cases")
    print()

    if dry_run:
        print("[DRY RUN] No changes were made to the database")
        print("Run without --dry-run to apply changes")

    return updated


async def main():
    parser = argparse.ArgumentParser(description="Refresh images for all cases")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated")
    parser.add_argument("--limit", type=int, help="Only process first N cases")
    args = parser.parse_args()

    updated = await refresh_all_cases(dry_run=args.dry_run, limit=args.limit)

    sys.exit(0 if updated > 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
