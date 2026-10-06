"""Basic text checks, not a substitute for clinical review."""

import json
import re

DISCLAIMER = (
    "Educational practice feedback only. Not an official competency assessment or medical advice."
)


def check_text(text: str) -> None:
    if len(text) > 200_000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        raise ValueError("Content contains invalid text or exceeds the size limit")
    if re.search(
        r"<\s*(script|iframe|object)|javascript:|ignore (all |previous |system )+instructions",
        text,
        re.I,
    ):
        raise ValueError("Content contains unsafe markup or instruction text")
    if re.search(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\b\d{3}-\d{2}-\d{4}\b", text, re.I):
        raise ValueError("Content may contain personal identifiers")


def check_case(data: dict) -> None:
    check_text(json.dumps(data, ensure_ascii=False))
    for key in ("case_id", "title", "presentation", "correct_diagnosis"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Case requires {key}")


def safe_coaching(text: str) -> str:
    try:
        check_text(text)
        if len(text) < 30 or len(text) > 20_000:
            return ""
        return text
    except ValueError:
        return ""
