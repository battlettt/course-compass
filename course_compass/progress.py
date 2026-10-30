"""Progress over time from the attempts history, and weakest-first topic picking for cram mode.

Pure functions, no I/O. Trends come only from recorded attempts: a topic with
too few attempts gets "not enough history", never an invented trend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .review import Card, TopicStat, topic_stats, weak_topics

MIN_TREND_ATTEMPTS = 4      # fewer than this and an early-vs-recent split is just noise
TREND_MARGIN = 0.10         # accuracy must move by more than 10 points to count as a change


@dataclass
class TopicProgress:
    topic: str
    stat: TopicStat
    sessions: list[tuple[str, int, int]] = field(default_factory=list)  # (date, correct, attempts) by day
    early: float | None = None      # accuracy over the first half of attempts
    recent: float | None = None     # accuracy over the second half
    due: int = 0                    # cards due today or earlier
    due_missed: int = 0             # of those, cards whose last answer was wrong
    cram_attempts: int = 0

    @property
    def movement(self) -> str:
        if self.stat.attempts == 0:
            return "no history"
        if self.early is None or self.recent is None:
            return "not enough history"
        delta = self.recent - self.early
        if delta > TREND_MARGIN:
            return "improving"
        if delta < -TREND_MARGIN:
            return "slipping"
        return "steady"

    @property
    def trend_text(self) -> str:
        if self.early is None or self.recent is None:
            if self.stat.attempts == 0:
                return "not quizzed yet"
            return f"{self.stat.attempts} attempt(s), need {MIN_TREND_ATTEMPTS} for a trend"
        return f"{self.movement} {self.early:.0%} -> {self.recent:.0%}"

    @property
    def advice(self) -> str:
        """One plain sentence linking the trend to the due cards."""
        movement = self.movement
        if movement == "no history":
            return "Not quizzed yet, so there is no history to judge. Take a quiz on it."
        due = f"{self.due} card(s) due" + (f", {self.due_missed} missed last time" if self.due_missed else "")
        if movement == "slipping":
            return (f"Slipping and {due}: review these first." if self.due
                    else "Slipping but nothing due yet: a quick cram would catch it early.")
        if movement == "improving":
            return (f"Improving; {due}, and reviewing them on time locks the gain in." if self.due
                    else "Improving, and nothing is due: the spacing is working.")
        if movement == "steady":
            return (f"Holding steady; {due}." if self.due else "Holding steady, nothing due today.")
        return (f"Too little history for a trend yet; {due}." if self.due
                else "Too little history for a trend yet; quiz it a few more times.")


def accuracy(attempts: list[dict]) -> float:
    return sum(1 for a in attempts if a["correct"]) / len(attempts)


def topic_progress(db: dict, topics: list[str], today: date) -> list[TopicProgress]:
    """Per-topic history, weakest topic first (same order as `weak_topics`)."""
    attempts = sorted(enumerate(db["attempts"]), key=lambda pair: (pair[1].get("date", ""), pair[0]))
    ordered = [attempt for _, attempt in attempts]
    stats = {stat.topic: stat for stat in topic_stats(ordered, topics)}
    last_result: dict[str, bool] = {}
    for attempt in ordered:
        last_result[attempt["item_id"]] = bool(attempt["correct"])
    due_by_topic: dict[str, list[str]] = {}
    for item_id, raw in db["cards"].items():
        item = db["quiz_items"].get(item_id)
        if item and Card.from_dict(raw).due <= today.isoformat():
            due_by_topic.setdefault(item["topic"], []).append(item_id)
    result = []
    for stat in weak_topics(list(stats.values())):
        mine = [a for a in ordered if a["topic"] == stat.topic]
        progress = TopicProgress(stat.topic, stat)
        by_day: dict[str, list[dict]] = {}
        for attempt in mine:
            by_day.setdefault(attempt.get("date", "?"), []).append(attempt)
        progress.sessions = [(day, sum(1 for a in day_attempts if a["correct"]), len(day_attempts))
                             for day, day_attempts in by_day.items()]
        if len(mine) >= MIN_TREND_ATTEMPTS:
            half = len(mine) // 2
            progress.early, progress.recent = accuracy(mine[:half]), accuracy(mine[half:])
        due_ids = due_by_topic.get(stat.topic, [])
        progress.due = len(due_ids)
        progress.due_missed = sum(1 for item_id in due_ids if last_result.get(item_id) is False)
        progress.cram_attempts = sum(1 for a in mine if a.get("session") == "cram")
        result.append(progress)
    return result


def cram_topics(stats: list[TopicStat], quizzable: set[str] | None, count: int) -> list[str]:
    """The `count` weakest topics that can actually be quizzed, weakest first."""
    if count < 1:
        raise ValueError("--topics must be at least 1.")
    ranked = [stat.topic for stat in weak_topics(stats) if quizzable is None or stat.topic in quizzable]
    return ranked[:count]


def split_questions(topics: list[str], n: int) -> dict[str, int]:
    """Deal n questions round-robin from the weakest topic, so weaker topics never get fewer."""
    counts = {topic: 0 for topic in topics}
    for i in range(n):
        counts[topics[i % len(topics)]] += 1
    return {topic: c for topic, c in counts.items() if c}
