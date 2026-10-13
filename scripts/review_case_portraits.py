#!/usr/bin/env python3
"""Local-only portrait review. Approval requires three explicit visual checks."""

import argparse
import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.simulation import database
from src.simulation.portraits import portrait_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="List portraits awaiting visual review")
    listing.add_argument("--limit", type=int, default=100)
    gallery = actions.add_parser("gallery", help="Create a local, read-only review gallery")
    gallery.add_argument("--output", type=Path, default=ROOT / "data" / "portrait_review.html")
    gallery.add_argument("--limit", type=int, default=200)
    approve = actions.add_parser("approve", help="Approve one portrait after inspecting it")
    approve.add_argument("asset_id")
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--age-match", action="store_true", required=True)
    approve.add_argument("--appearance-match", action="store_true", required=True)
    approve.add_argument("--image-quality", action="store_true", required=True)
    approve.add_argument("--notes", default="")
    reject = actions.add_parser("reject", help="Reject one portrait")
    reject.add_argument("asset_id")
    reject.add_argument("--reviewer", required=True)
    reject.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if os.getenv("DATABASE_URL"):
        parser.error("This review command is local-only; DATABASE_URL must be unset")
    database.init_db()
    if args.action in {"list", "gallery"}:
        rows = database.pending_portraits(args.limit)
        if args.action == "list":
            for row in rows:
                path = portrait_dir() / row["file_path"]
                print(f"{row['asset_id']}  {row['case_id']} v{row['case_version']}  "
                      f"age={row['target_age']}  {row['title']}  {path}")
            print(f"{len(rows)} awaiting review")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            cards = []
            for row in rows:
                image = (portrait_dir() / row["file_path"]).resolve()
                relative = os.path.relpath(image, args.output.parent)
                cards.append(
                    "<figure><img src=\"" + html.escape(relative, quote=True) + "\" alt=\"Synthetic portrait\">"
                    "<figcaption><strong>" + html.escape(row["case_id"]) + "</strong> · "
                    + html.escape(row["title"]) + "<br>Expected age: "
                    + html.escape(str(row["target_age"])) + " · Appearance: "
                    + html.escape(row["appearance_profile"] or "unspecified") + "<br>Asset: <code>"
                    + html.escape(row["asset_id"]) + "</code></figcaption></figure>"
                )
            args.output.write_text(
                "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                "<title>Portrait review</title><style>body{font:15px system-ui;margin:24px;color:#17212b}"
                "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:20px}"
                "figure{margin:0}img{width:100%;aspect-ratio:1;object-fit:cover}"
                "figcaption{line-height:1.5;overflow-wrap:anywhere}code{font-size:12px}</style>"
                "<h1>Portrait review</h1><main>" + "".join(cards) + "</main></html>",
                encoding="utf-8",
            )
            print(args.output.resolve())
    elif args.action == "approve":
        asset = database.portrait_asset(args.asset_id)
        base = portrait_dir().resolve()
        image = (base / asset["file_path"]).resolve() if asset else None
        if not image or not image.is_relative_to(base) or not image.is_file():
            parser.error("Portrait file is missing or outside the portrait directory")
        database.review_portrait(
            args.asset_id, args.reviewer, True,
            {"age_match": args.age_match, "appearance_match": args.appearance_match,
             "image_quality": args.image_quality}, args.notes,
        )
        print(f"Approved {args.asset_id}")
    else:
        database.review_portrait(args.asset_id, args.reviewer, False, notes=args.reason)
        print(f"Rejected {args.asset_id}")


if __name__ == "__main__":
    main()
