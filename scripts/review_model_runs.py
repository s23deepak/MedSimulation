#!/usr/bin/env python3
"""Inspect local patient-agent runs and record human quality evaluations."""

import argparse
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.simulation import database


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--component", choices=["patient_agent", "portrait"],
                         default="patient_agent")
    listing.add_argument("--limit", type=int, default=30)
    show = actions.add_parser("show")
    show.add_argument("run_id")
    summary = actions.add_parser("summary")
    summary.add_argument("--component", choices=["patient_agent", "portrait"],
                         default="patient_agent")
    score = actions.add_parser("score")
    score.add_argument("run_id")
    score.add_argument("--metric", required=True,
                       choices=["persona_accuracy", "in_character", "safety", "clinical_consistency"])
    score.add_argument("--value", required=True, choices=["pass", "fail"])
    score.add_argument("--reviewer", required=True)
    score.add_argument("--notes", default="")
    args = parser.parse_args(argv)
    if os.getenv("DATABASE_URL"):
        parser.error("This review command is local-only; DATABASE_URL must be unset")
    database.init_db()
    if args.action in {"list", "summary"}:
        with database._connect() as conn:
            if args.action == "list":
                rows = conn.execute(
                    """SELECT * FROM model_runs WHERE component=?
                    ORDER BY created_at DESC, run_id DESC LIMIT ?""",
                    (args.component, args.limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT status,duration_ms FROM model_runs WHERE component=?",
                    (args.component,),
                ).fetchall()
        if args.action == "list":
            for row in rows:
                print(f"{row['run_id']}  {row['case_id']}  {row['session_id'] or '-'}  "
                      f"v{row['prompt_version']}  {row['status']}  {row['duration_ms']} ms")
        else:
            durations = sorted(row["duration_ms"] for row in rows if row["status"] == "success")
            failures = sum(row["status"] != "success" for row in rows)
            if durations:
                p50 = durations[(len(durations) - 1) // 2]
                p95 = durations[math.ceil(len(durations) * 0.95) - 1]
                print(f"{args.component}: {len(durations)} successful, {failures} failed, "
                      f"p50={p50} ms, p95={p95} ms")
            else:
                print(f"{args.component}: 0 successful, {failures} failed")
    elif args.action == "show":
        with database._connect() as conn:
            run = conn.execute("SELECT * FROM model_runs WHERE run_id=?", (args.run_id,)).fetchone()
        if not run:
            parser.error("Model run not found")
        print(f"{run['component']}  {run['case_id']}  {run['status']}  "
              f"prompt v{run['prompt_version']}  {run['duration_ms']} ms")
        if run["component"] == "patient_agent" and run["session_id"]:
            session = database.load_session(run["session_id"])
            turn = next((item for item in (session or {}).get("history_questions", [])
                         if item.get("model_run_id") == args.run_id), None)
            if turn:
                print(f"Question: {turn['question']}\nResponse: {turn['response']}")
    else:
        database.record_model_evaluation(
            "patient_agent", args.metric, args.value, args.reviewer,
            run_id=args.run_id, notes=args.notes,
        )
        print(f"Recorded {args.metric}={args.value} for {args.run_id}")


if __name__ == "__main__":
    main()
