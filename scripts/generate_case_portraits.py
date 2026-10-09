#!/usr/bin/env python3
"""Generate synthetic portraits for case versions through a local ComfyUI server."""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.simulation import database
from src.simulation.comfyui_client import ComfyUIClient
from src.simulation.portraits import generate_case_portrait


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id", nargs="?", help="Case ID; omit with --all")
    parser.add_argument(
        "--all", action="store_true", help="Generate for all cases without a portrait"
    )
    parser.add_argument("--regenerate", action="store_true", help="Replace the active portrait")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument(
        "--modal", action="store_true", help="Use the deployed Modal portrait worker"
    )
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    if args.all == bool(args.case_id):
        parser.error("Provide one case ID or --all")
    if args.modal:
        if args.all:
            parser.error("--modal requires a case ID")
        import modal

        worker = modal.Cls.from_name("medsimulation-portraits", "PortraitGenerator")
        result = worker().generate.remote(args.case_id, -1 if args.seed is None else args.seed)
        print(f"{args.case_id}: {result['file_path']}")
        return
    database.init_db()
    case_ids = [row["case_id"] for row in database.list_db_cases()] if args.all else [args.case_id]
    client = ComfyUIClient(args.comfy_url)
    for case_id in case_ids:
        case = database.case_record(case_id)
        if not args.regenerate and database.active_portrait(case_id, case["version"]):
            print(f"{case_id}: already has a portrait")
            continue
        result = generate_case_portrait(case_id, client, seed=args.seed)
        print(f"{case_id}: {result['file_path']}")


if __name__ == "__main__":
    main()
