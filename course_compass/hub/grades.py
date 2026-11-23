"""Grade standing, what-if projection and "what do I need on the final?".

Two ways to know how much each graded item is worth:
  1. LEARN's weighted grade book: each grade value carries WeightedNumerator /
     WeightedDenominator (points out of the course's 100). Used when present.
  2. Otherwise the syllabus grading scheme: LEARN grade items are matched to
     syllabus components by name ("Assignment 3" -> "Assignments (5) 30%"),
     and each component's weight is split across its expected item count.

Weight that has no mark yet is "remaining". The calculator answers: what
average on the remaining weight gets you to your target, and (when a final
exam component exists) what you need on the final if you keep your current
average on everything else.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .syllabus import Component, match_component

FINAL_RE = re.compile(r"\bfinal\b", re.I)


@dataclass
class GradedItem:
    name: str
    percent: float | None      # 0-100, None if not graded yet
    weight: float | None       # share of the course grade (out of 100), if known
    component: str | None = None


@dataclass
class ComponentStanding:
    name: str
    weight: float
    graded_weight: float
    percent: float | None
    items: list[GradedItem] = field(default_factory=list)
    whatif: float | None = None
    is_final: bool = False


@dataclass
class Standing:
    method: str                          # weighted, syllabus, unweighted, none
    total_weight: float
    graded_weight: float
    earned: float                        # points out of total_weight
    components: list[ComponentStanding]
    unmatched: list[GradedItem]
    learn_final: str = ""
    target: float | None = None

    @property
    def current_percent(self) -> float | None:
        if self.graded_weight <= 0:
            return None
        return round(self.earned / self.graded_weight * 100, 2)

    @property
    def remaining_weight(self) -> float:
        return round(max(0.0, self.total_weight - self.graded_weight), 2)

    def projected(self) -> float | None:
        """Final grade if what-if scores are used where set and your current average everywhere else."""
        current = self.current_percent
        if current is None and not any(c.whatif is not None for c in self.components):
            return None
        points = self.earned
        for comp in self.components:
            left = comp.weight - comp.graded_weight
            if left <= 0:
                continue
            assumed = comp.whatif if comp.whatif is not None else current
            if assumed is None:
                return None
            points += left * assumed / 100
        if self.method == "weighted" and not self.components:
            points += self.remaining_weight * (current or 0) / 100
        return round(points / self.total_weight * 100, 2) if self.total_weight else None

    def needed(self, target: float | None = None) -> dict | None:
        target = target if target is not None else self.target
        if target is None:
            return None
        whatif_points, open_weight = 0.0, 0.0
        for comp in self.components:
            left = comp.weight - comp.graded_weight
            if left <= 0:
                continue
            if comp.whatif is not None:
                whatif_points += left * comp.whatif / 100
            else:
                open_weight += left
        if not self.components:
            open_weight = self.remaining_weight
        goal_points = target / 100 * self.total_weight
        missing = goal_points - self.earned - whatif_points
        if open_weight <= 0:
            status = "secured" if missing <= 0 else "out-of-reach"
            return {"target": target, "needed_percent": None, "status": status, "open_weight": 0.0}
        needed = missing / open_weight * 100
        status = "secured" if needed <= 0 else "out-of-reach" if needed > 100 else "reachable"
        result = {"target": target, "needed_percent": round(max(needed, 0.0), 1), "status": status,
                  "open_weight": round(open_weight, 2)}
        final = next((c for c in self.components if c.is_final and c.graded_weight < c.weight
                      and c.whatif is None), None)
        current = self.current_percent
        if final and current is not None:
            others_left = open_weight - (final.weight - final.graded_weight)
            final_left = final.weight - final.graded_weight
            need_final = (missing - others_left * current / 100) / final_left * 100
            result["final"] = {"name": final.name, "weight": final.weight,
                               "needed_percent": round(max(need_final, 0.0), 1),
                               "assumes_other_work_at": current}
        return result

    def to_dict(self) -> dict:
        data = asdict(self)
        data.update(current_percent=self.current_percent, remaining_weight=self.remaining_weight,
                    projected=self.projected(), needed=self.needed())
        return data


def expected_count(name: str) -> int | None:
    match = re.search(r"\((\d{1,2})\)|\b(\d{1,2})\s*x\b|\bx\s*(\d{1,2})\b|^(\d{1,2})\s+[a-z]", name, re.I)
    if not match:
        return None
    return int(next(g for g in match.groups() if g))


def _percent(row: dict) -> float | None:
    num, den = row.get("points_num"), row.get("points_den")
    if num is not None and den:
        return round(num / den * 100, 2)
    shown = row.get("displayed") or ""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", shown)
    return float(match.group(1)) if match else None


def standing(grade_rows: list[dict], components: list[Component], whatif: dict[str, float] | None = None,
             target: float | None = None) -> Standing:
    whatif = whatif or {}
    items = [row for row in grade_rows if row.get("gtype", "item") == "item"]
    final_row = next((row for row in grade_rows if row.get("gtype") == "final"), None)
    learn_final = (final_row or {}).get("displayed") or ""

    weighted = [row for row in items if row.get("weighted_den")]
    if weighted and not components:
        graded = [row for row in weighted if row.get("weighted_num") is not None]
        earned = sum(row["weighted_num"] for row in graded)
        graded_weight = sum(row["weighted_den"] for row in graded)
        listed = [GradedItem(row["name"], _percent(row), row["weighted_den"]) for row in weighted]
        total = max(100.0, sum(row["weighted_den"] for row in weighted))
        return Standing("weighted", total, round(graded_weight, 2), round(earned, 2), [], listed, learn_final,
                        target)

    if components:
        comps = [ComponentStanding(c.name, c.weight, 0.0, None, whatif=whatif.get(c.name),
                                   is_final=bool(FINAL_RE.search(c.name)) and "exam" in c.name.lower()
                                   or c.name.lower().startswith("final"))
                 for c in components]
        by_name = {c.name: c for c in comps}
        unmatched: list[GradedItem] = []
        buckets: dict[str, list[dict]] = {}
        for row in items:
            match = match_component(row["name"], components)
            if match is None:
                unmatched.append(GradedItem(row["name"], _percent(row), None))
            else:
                buckets.setdefault(match.name, []).append(row)
        earned = 0.0
        for comp in comps:
            rows = buckets.get(comp.name, [])
            graded = [(row, _percent(row)) for row in rows if _percent(row) is not None]
            count = expected_count(comp.name) or max(len(rows), 1)
            share = comp.weight / count
            for row in rows:
                comp.items.append(GradedItem(row["name"], _percent(row), round(share, 2), comp.name))
            if graded:
                comp.graded_weight = round(min(comp.weight, share * len(graded)), 2)
                comp.percent = round(sum(p for _, p in graded) / len(graded), 2)
                earned += comp.graded_weight * comp.percent / 100
        total = sum(c.weight for c in comps) or 100.0
        graded_weight = sum(c.graded_weight for c in comps)
        return Standing("syllabus", round(total, 2), round(graded_weight, 2), round(earned, 2), comps, unmatched,
                        learn_final, target)

    graded = [(row, _percent(row)) for row in items if _percent(row) is not None]
    listed = [GradedItem(row["name"], _percent(row), None) for row in items]
    if not graded:
        return Standing("none", 100.0, 0.0, 0.0, [], listed, learn_final, target)
    average = sum(p for _, p in graded) / len(graded)
    # Without weights the best honest number is the unweighted average of graded items.
    return Standing("unweighted", 100.0, 100.0, round(average, 2), [], listed, learn_final, target)
