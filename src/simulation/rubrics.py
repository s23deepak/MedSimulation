"""Deterministic rubric evidence. Draft rubrics require human approval."""

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

DOMAINS = ("history", "exam", "investigations", "diagnosis", "management")


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    domain: Literal["history", "exam", "investigations", "diagnosis", "management"]
    label: str = Field(min_length=1, max_length=1000)
    kind: Literal["required", "optional", "contraindicated"] = "required"
    aliases: list[str] = Field(min_length=1, max_length=30)
    points: int = Field(default=1, ge=1, le=100)
    partial_aliases: list[str] = Field(default_factory=list)


class OrderRule(BaseModel):
    before: str
    after: str
    penalty: int = Field(ge=1, le=20)
    domain: Literal["history", "exam", "investigations", "diagnosis", "management"]


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=100)
    items: list[Item] = Field(min_length=5, max_length=300)
    ordering: list[OrderRule] = Field(default_factory=list)
    unlisted_investigation_penalty: int = Field(default=1, ge=0, le=5)

    @model_validator(mode="after")
    def valid(self):
        ids = {i.id for i in self.items}
        if len(ids) != len(self.items):
            raise ValueError("Rubric item IDs must be unique")
        if {i.domain for i in self.items if i.kind == "required"} != set(DOMAINS):
            raise ValueError("Each domain requires at least one required item")
        if any(
            not alias.strip()
            for item in self.items
            for alias in item.aliases + item.partial_aliases
        ):
            raise ValueError("Rubric aliases cannot be blank")
        if any(rule.before not in ids or rule.after not in ids for rule in self.ordering):
            raise ValueError("Ordering rules must reference existing items")
        return self


def validate_rubric(data):
    return Rubric.model_validate(data)


def draft_rubric(case):
    """A review starting point, explicitly not a validated clinical rubric."""
    items = []
    for domain, labels in [
        ("history", list(case.history_data)),
        ("exam", list(case.physical_exam)),
        ("investigations", list(case.investigations)),
        ("management", case.correct_management),
    ]:
        for n, label in enumerate(labels):
            items.append(
                dict(
                    id=f"{domain}-{n}",
                    domain=domain,
                    label=label,
                    aliases=[label],
                    kind="required" if n < 3 else "optional",
                    points=1,
                )
            )
    items.append(
        dict(
            id="diagnosis",
            domain="diagnosis",
            label=case.correct_diagnosis,
            aliases=[case.correct_diagnosis],
            partial_aliases=case.acceptable_diagnoses,
            points=1,
        )
    )
    return dict(
        version=f"{case.case_id}-v{case.version}-draft",
        items=items,
        ordering=[],
        unlisted_investigation_penalty=1,
    )


def normalize(value):
    return " ".join(re.findall(r"\w+", value.lower()))


def matches(text, aliases, negation=True):
    normalized = normalize(text)
    for alias in aliases:
        pattern = r"(?<!\w)" + re.escape(normalize(alias)) + r"(?!\w)"
        for match in re.finditer(pattern, normalized):
            prefix = normalized[: match.start()].split()[-4:]
            if not negation or not set(prefix).intersection(
                {"no", "not", "avoid", "without", "withhold", "stop", "never"}
            ):
                return True
    return False


def evaluate(session):
    rubric = validate_rubric(session.case.rubric)
    streams = {
        "history": [h["question"] for h in session.history_questions],
        "exam": session.exam_systems_viewed,
        "investigations": session.investigations_ordered,
        "diagnosis": [session.diagnosis_submitted],
        "management": session.management_submitted,
    }
    evidence = []
    for item in rubric.items:
        hits = [
            v
            for v in streams[item.domain]
            if matches(v, item.aliases, item.domain in {"diagnosis", "management"})
        ]
        partial = (
            [v for v in streams[item.domain] if matches(v, item.partial_aliases)]
            if not hits
            else []
        )
        credit = 1 if hits else 0.5 if partial else 0
        evidence.append(
            dict(
                item_id=item.id,
                domain=item.domain,
                kind=item.kind,
                label=item.label,
                credit=credit,
                points=item.points,
                evidence=hits or partial,
            )
        )
    scores, feedback = {}, {}
    for domain in DOMAINS:
        rows = [r for r in evidence if r["domain"] == domain]
        required = [r for r in rows if r["kind"] == "required"]
        ratio = sum(r["credit"] * r["points"] for r in required) / sum(
            r["points"] for r in required
        )
        penalty = sum(r["points"] for r in rows if r["kind"] == "contraindicated" and r["credit"])
        scores[domain] = max(0, round(session.case.score_weights[domain] * ratio) - penalty)
        feedback[domain] = (
            f"{sum(r['credit'] > 0 for r in required)}/{len(required)} required rubric items evidenced; {penalty} contraindication penalty points."
        )
    allowed = [
        a
        for item in rubric.items
        if item.domain == "investigations" and item.kind != "contraindicated"
        for a in item.aliases
    ]
    extra = [v for v in set(streams["investigations"]) if not matches(v, allowed, False)]
    extra_penalty = len(extra) * rubric.unlisted_investigation_penalty
    scores["investigations"] = max(0, scores["investigations"] - extra_penalty)
    if extra:
        evidence.append(
            dict(
                item_id="unlisted-investigations",
                domain="investigations",
                penalty=extra_penalty,
                evidence=extra,
            )
        )
    # Only case-specific prerequisites count. No generic "history before exam" penalty.
    sequence = [(e["type"], e["detail"]) for e in getattr(session, "action_log", [])]
    sequence += [("management", step) for step in session.management_submitted]
    types = {
        "history": "history",
        "exam": "exam",
        "investigations": "investigation",
        "diagnosis": "assessment",
        "management": "management",
    }
    by_id = {i.id: i for i in rubric.items}
    for rule in rubric.ordering:

        def position(item_id):
            item = by_id[item_id]
            return next(
                (
                    n
                    for n, (kind, value) in enumerate(sequence)
                    if kind == types[item.domain] and matches(value, item.aliases)
                ),
                None,
            )

        before, after = position(rule.before), position(rule.after)
        if after is not None and (before is None or before >= after):
            scores[rule.domain] = max(0, scores[rule.domain] - rule.penalty)
            evidence.append(
                dict(
                    item_id=f"order:{rule.before}:{rule.after}",
                    domain=rule.domain,
                    penalty=rule.penalty,
                    evidence=["Prerequisite missing or performed too late"],
                )
            )
    feedback["investigations"] += f" {extra_penalty} points for unlisted investigations."
    return scores, feedback, evidence, rubric.version
