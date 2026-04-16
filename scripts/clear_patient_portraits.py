#!/usr/bin/env python3
"""
Clear patient_image_url from all existing cases in the database.

This removes DALL-E generated patient portraits that were stored
before the feature was disabled.

Usage:
    python scripts/clear_patient_portraits.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "medsim.db"

def clear_patient_portraits():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if cases table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cases'")
    if not cursor.fetchone():
        print("Cases table not found")
        conn.close()
        return

    # Count cases with patient_image_url set
    cursor.execute("SELECT COUNT(*) FROM cases WHERE patient_image_url IS NOT NULL AND patient_image_url != ''")
    count_before = cursor.fetchone()[0]

    # Clear patient_image_url
    cursor.execute("UPDATE cases SET patient_image_url = '' WHERE patient_image_url IS NOT NULL AND patient_image_url != ''")
    conn.commit()

    print(f"Cleared patient_image_url from {count_before} cases")
    print("Patient portraits will no longer be displayed in the Physical Exam tab")

    conn.close()

if __name__ == "__main__":
    clear_patient_portraits()
