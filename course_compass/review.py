"""Spaced review (SM-2 style), weak-topic ranking and a study plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

MIN_EASE = 1.3
START_EASE = 2.5
QUALITY_CORRECT = 4
QUALITY_WRONG = 1


@dataclass
class Card:
    ease: float = START_EASE
    interval: int = 0
    repetitions: int = 0
    due: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Card":
        return cls(**{name: data[name] for name in cls.__dataclass_fields__})


def new_card(today: date) -> Card:
    return Card(due=today.isoformat())


def schedule(card: Card, quality: int, today: date) -> Card:
    """SM-2: grow the interval after a pass (q >= 3), reset to 1 day after a fail.

    Ease moves up for easy recalls and down for hard ones, never below 1.3,
    so cards you keep missing come back more often even after you pass them.
    """
    if not 0 <= quality <= 5:
        raise ValueError("quality must be between 0 and 5")
    ease = card.ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease = max(MIN_EASE, round(ease, 2))
    if quality >= 3:
        repetitions = card.repetitions + 1
        if repetitions == 1:
            interval = 1
        elif repetitions == 2:
            interval = 6
        else:
            interval = max(card.interval + 1, round(card.interval * card.ease))
    else:
        repetitions = 0
        interval = 1
    return Card(ease=ease, interval=interval, repetitions=repetitions,
                due=(today + timedelta(days=interval)).isoformat())


def quality_from_correct(correct: bool) -> int:
    return QUALITY_CORRECT if correct else QUALITY_WRONG


def due_queue(cards: dict[str, Card], today: date) -> list[str]:
    """Ids of cards due today or earlier: most overdue first, then hardest (lowest ease)."""
    due = [(card.due, card.ease, card_id) for card_id, card in cards.items()
           if card.due <= today.isoformat()]
    return [card_id for _, _, card_id in sorted(due)]


@dataclass
class TopicStat:
    topic: str
    attempts: int
    correct: int

    @property
    def weakness(self) -> float:
        """Laplace-smoothed error rate: an unseen topic scores 0.5."""
        return (self.attempts - self.correct + 1) / (self.attempts + 2)


def topic_stats(attempts: list[dict], topics: list[str]) -> list[TopicStat]:
    stats = {topic: TopicStat(topic, 0, 0) for topic in topics}
    for attempt in attempts:
        stat = stats.setdefault(attempt["topic"], TopicStat(attempt["topic"], 0, 0))
        stat.attempts += 1
        stat.correct += int(bool(attempt["correct"]))
    return list(stats.values())


def weak_topics(stats: list[TopicStat]) -> list[TopicStat]:
    """Weakest first; ties broken by fewer attempts (less evidence), then name."""
    return sorted(stats, key=lambda s: (-s.weakness, s.attempts, s.topic))


@dataclass
class PlanBlock:
    topic: str
    minutes: int
    weakness: float
    due_cards: int


def study_plan(stats: list[TopicStat], due_by_topic: dict[str, int], minutes: int = 60) -> list[PlanBlock]:
    """Split study time across topics in proportion to weakness, weakest first."""
    if minutes < 5:
        raise ValueError("A study plan needs at least 5 minutes.")
    ranked = weak_topics(stats)
    if not ranked:
        return []
    total = sum(stat.weakness for stat in ranked)
    shares = [minutes * stat.weakness / total for stat in ranked]
    allotted = [int(share) for share in shares]
    # Largest-remainder rounding: the blocks always add up to exactly `minutes`.
    by_remainder = sorted(range(len(ranked)), key=lambda i: (-(shares[i] - allotted[i]), i))
    for i in by_remainder[:minutes - sum(allotted)]:
        allotted[i] += 1
    return [PlanBlock(stat.topic, mins, round(stat.weakness, 2), due_by_topic.get(stat.topic, 0))
            for stat, mins in zip(ranked, allotted) if mins > 0]
