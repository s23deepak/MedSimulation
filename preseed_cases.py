#!/usr/bin/env python3
"""
Pre-seed Clinical Cases for MedSimulation

Generates and caches clinical cases ahead of time to avoid on-the-fly generation
during user sessions. This significantly improves response times.

Usage:
    python preseed_cases.py --topics "chest pain,abdominal pain,knee injury" --count 5
    python preseed_cases.py --file topics.txt --count 10
    python preseed_cases.py --list  # List existing cases
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.simulation.cases import ClinicalCase, save_case_to_db
from src.simulation.case_sources.ai_generator import generate_case
from src.simulation.vllm_client import VLLMClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Default topics to generate if none specified
DEFAULT_TOPICS = [
    "acute coronary syndrome",
    "pulmonary embolism",
    "pneumonia",
    "acute appendicitis",
    "cholecystitis",
    "diabetic ketoacidosis",
    "meningitis",
    "stroke",
    "sepsis",
    "asthma exacerbation",
    "COPD exacerbation",
    "heart failure",
    "atrial fibrillation",
    "myocardial infarction",
    "deep vein thrombosis",
    "cellulitis",
    "urinary tract infection",
    "pyelonephritis",
    "gastroenteritis",
    "bowel obstruction",
    "pancreatitis",
    "hepatitis",
    "peptic ulcer disease",
    "migraine",
    "tension headache",
    "cluster headache",
    "seizure disorder",
    "transient ischemic attack",
    "subarachnoid hemorrhage",
    "intracerebral hemorrhage",
    "knee injury",
    "ankle sprain",
    "fracture",
    "ligament tear",
    "rotator cuff injury",
    "carpal tunnel syndrome",
    "low back pain",
    "neck pain",
    "shoulder impingement",
    "depression",
    "anxiety disorder",
    "bipolar disorder",
    "schizophrenia",
    "panic attack",
    "PTSD",
    "substance abuse",
    "alcohol withdrawal",
    "opioid overdose",
    "suicidal ideation",
]


def get_vllm_client() -> Any:
    """Initialize vLLM client based on environment configuration."""
    vllm_mode = os.getenv("VLLM_MODE", "modal")

    if vllm_mode == "modal":
        # Modal deployment - use injected client
        from src.simulation.vllm_client import ModalVLLMClient

        logger.info("Using ModalVLLMClient (Modal deployment)")
        return ModalVLLMClient()
    elif vllm_mode == "local":
        # Local vLLM server
        logger.info("Using local vLLM server")
        return VLLMClient.from_env()
    else:
        # Cloud provider
        logger.info("Using cloud vLLM endpoint: %s", os.getenv("VLLM_CLOUD_URL", "unknown"))
        return VLLMClient.from_env()


async def generate_single_case(
    vllm_client: Any,
    topic: str,
    source_type: str = "custom",
) -> ClinicalCase | None:
    """Generate a single clinical case for the given topic."""
    logger.info("Generating case for topic: %s", topic)

    try:
        # Create source text from topic
        source_text = f"""
        Clinical Case Topic: {topic}

        Generate a realistic clinical case presentation for this condition.
        Include typical patient demographics, presenting symptoms, relevant history,
        physical examination findings, and appropriate investigations.

        IMPORTANT: This case will be pre-seeded and cached for future use.
        Ensure all fields are complete and clinically accurate.
        """

        # Generate case structure via AI
        case_data = await generate_case(
            vllm_client=vllm_client,
            source_text=source_text,
            source_type=source_type,
            source_ref=f"preseed-{topic.replace(' ', '-').lower()}",
        )

        # Create ClinicalCase object
        case = ClinicalCase(**case_data)
        case.source = source_type
        case.source_ref = f"preseed-{topic.replace(' ', '-').lower()}"

        logger.info("Generated case: %s (ID: %s)", case.title, case.case_id)
        return case

    except Exception as e:
        logger.error("Failed to generate case for '%s': %s", topic, e)
        return None


async def preseed_topics(
    topics: list[str],
    count_per_topic: int = 1,
    output_dir: Path | None = None,
) -> dict:
    """
    Pre-seed multiple topics with clinical cases.

    Returns statistics about generation success/failure.
    """
    logger.info("Starting pre-seeding for %d topics", len(topics))

    # Initialize vLLM client
    try:
        vllm_client = get_vllm_client()
    except Exception as e:
        logger.error("Failed to initialize vLLM client: %s", e)
        return {"success": 0, "failed": len(topics), "error": str(e)}

    stats = {
        "success": 0,
        "failed": 0,
        "cases": [],
        "errors": [],
    }

    for topic in topics:
        for i in range(count_per_topic):
            topic_label = f"{topic}" if count_per_topic == 1 else f"{topic} (variant {i+1})"
            logger.info("Processing: %s", topic_label)

            case = await generate_single_case(vllm_client, topic, source_type="preseed")

            if case:
                # Save to database
                try:
                    save_case_to_db(case)
                    logger.info("Saved case to database: %s", case.case_id)
                except Exception as e:
                    logger.warning("Failed to save case to DB: %s", e)

                # Also save JSON file if output_dir specified
                if output_dir:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    case_file = output_dir / f"{case.case_id}.json"
                    with open(case_file, "w") as f:
                        json.dump(case.to_dict(), f, indent=2)
                    logger.info("Saved case JSON: %s", case_file)

                stats["success"] += 1
                stats["cases"].append(
                    {
                        "case_id": case.case_id,
                        "title": case.title,
                        "specialty": case.specialty,
                        "topic": topic,
                    }
                )
            else:
                stats["failed"] += 1
                stats["errors"].append({"topic": topic, "error": "Generation failed"})

    return stats


def list_existing_cases():
    """List all cases currently in the database."""
    from src.simulation.cases import list_cases

    cases = list_cases()
    logger.info("Found %d existing cases", len(cases))

    print("\n" + "=" * 80)
    print("EXISTING CLINICAL CASES")
    print("=" * 80)

    for case in cases:
        print(f"\n  ID: {case.case_id}")
        print(f"  Title: {case.title}")
        print(f"  Specialty: {case.specialty}")
        print(f"  Difficulty: {case.difficulty}")
        print(f"  Source: {getattr(case, 'source', 'unknown')}")

    print("\n" + "=" * 80)
    return cases


def main():
    parser = argparse.ArgumentParser(
        description="Pre-seed clinical cases for MedSimulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --topics "chest pain,abdominal pain" --count 3
  %(prog)s --file topics.txt --count 5
  %(prog)s --list  # List existing cases
  %(prog)s --default --count 2  # Generate default topics
        """,
    )

    parser.add_argument(
        "--topics", "-t",
        type=str,
        help="Comma-separated list of topics to generate",
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="File containing topics (one per line)",
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=1,
        help="Number of case variants per topic (default: 1)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output directory for JSON files (optional)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List existing cases and exit",
    )
    parser.add_argument(
        "--default", "-d",
        action="store_true",
        help="Use default topic list",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of topics to process (for testing)",
    )

    args = parser.parse_args()

    # Handle --list
    if args.list:
        list_existing_cases()
        return 0

    # Gather topics
    topics = []

    if args.topics:
        topics.extend([t.strip() for t in args.topics.split(",")])

    if args.file:
        try:
            with open(args.file) as f:
                topics.extend([line.strip() for line in f if line.strip() and not line.startswith("#")])
        except FileNotFoundError:
            logger.error("Topics file not found: %s", args.file)
            return 1

    if args.default or not topics:
        topics.extend(DEFAULT_TOPICS)
        logger.info("Using %d default topics", len(DEFAULT_TOPICS))

    # Apply limit
    if args.limit:
        topics = topics[: args.limit]
        logger.info("Limited to %d topics", len(topics))

    if not topics:
        logger.error("No topics specified. Use --topics, --file, or --default")
        return 1

    # Run pre-seeding
    output_dir = Path(args.output) if args.output else None

    import asyncio

    stats = asyncio.run(preseed_topics(topics, count_per_topic=args.count, output_dir=output_dir))

    # Print summary
    print("\n" + "=" * 80)
    print("PRE-SEEDING SUMMARY")
    print("=" * 80)
    print(f"  Successful: {stats['success']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Total: {stats['success'] + stats['failed']}")

    if stats["cases"]:
        print("\nGenerated Cases:")
        for c in stats["cases"]:
            print(f"  - [{c['specialty']}] {c['title']} (ID: {c['case_id']})")

    if stats["errors"]:
        print("\nErrors:")
        for e in stats["errors"]:
            print(f"  - {e['topic']}: {e.get('error', 'Unknown')}")

    print("=" * 80)

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
