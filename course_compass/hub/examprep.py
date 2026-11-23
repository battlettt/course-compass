"""Exam prep autopilot: a day-by-day countdown plan to an exam.

Given the exam date and the topics it covers (by default every notes topic of
that course), the plan has three phases:

  learn     (more than 7 days out)  each topic gets a "simplify + key terms" session,
                                    weakest topics first, spread across the days
  practice  (7 to 3 days out)       a practice quiz per topic each day, plus due review cards
  cram      (last 48 hours)         short cram sessions on the 2 weakest topics, a mixed
                                    practice exam, a final look at past misses, and sleep

Topic weakness comes from your quiz history (review.topic_stats), so the plan
moves as you practise.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from ..review import topic_stats


@dataclass
class Task:
    kind: str            # simplify, quiz, review, cram, practice-exam, misses, rest
    title: str
    minutes: int
    topics: list[str] = field(default_factory=list)


@dataclass
class Day:
    date: str
    phase: str
    tasks: list[Task]

    @property
    def minutes(self) -> int:
        return sum(task.minutes for task in self.tasks)


def exam_plan(exam_day: date, today: date, topics: list[str], attempts: list[dict], titles: dict[str, str],
              daily_minutes: int = 90) -> list[Day]:
    if exam_day <= today:
        return []
    stats = {s.topic: s for s in topic_stats(attempts, topics)}
    ranked = sorted(topics, key=lambda t: (-stats[t].weakness, t))
    name = lambda t: titles.get(t, t)   # noqa: E731
    days: list[Day] = []
    total = (exam_day - today).days
    learn_days = [today + timedelta(days=i) for i in range(total) if (exam_day - (today + timedelta(days=i))).days > 7]
    practice_days = [today + timedelta(days=i) for i in range(total)
                     if 3 <= (exam_day - (today + timedelta(days=i))).days <= 7]
    cram_days = [today + timedelta(days=i) for i in range(total) if (exam_day - (today + timedelta(days=i))).days < 3]

    # learn: spread topics across the learn days, weakest first, ~3 topics a day at most
    if learn_days and ranked:
        per_day = max(1, min(3, -(-len(ranked) // len(learn_days))))
        queue = list(ranked)
        for day in learn_days:
            todays = [queue.pop(0) for _ in range(min(per_day, len(queue)))]
            tasks = [Task("simplify", f"Re-learn {name(t)}: simplify, then quiz yourself on key terms", 30, [t])
                     for t in todays]
            tasks.append(Task("review", "Spaced-review cards due today", 15))
            if not todays:
                tasks = [Task("review", "Spaced-review cards due today", 20)]
            days.append(Day(day.isoformat(), "learn", tasks))
    # practice: rotate through topics, two per day, weakest topics come round more often
    if practice_days and ranked:
        weighted = ranked[:max(1, len(ranked) // 2)] * 2 + ranked[max(1, len(ranked) // 2):]
        cursor = 0
        for day in practice_days:
            todays = []
            for _ in range(min(2, len(ranked))):
                topic = weighted[cursor % len(weighted)]
                cursor += 1
                if topic not in todays:
                    todays.append(topic)
            tasks = [Task("quiz", f"Practice quiz: {name(t)} (8 questions)", 25, [t]) for t in todays]
            tasks.append(Task("review", "Spaced-review cards due today", 20))
            days.append(Day(day.isoformat(), "practice", tasks))
    for day in cram_days:
        left = (exam_day - day).days
        weakest = ranked[:2]
        if left == 2 or (left == 1 and len(cram_days) == 1):
            tasks = [Task("practice-exam", "Mixed practice exam across every topic (20 questions)", 45, ranked)]
            tasks += [Task("cram", f"Cram: {name(t)}", 15, [t]) for t in weakest]
        else:
            tasks = [Task("misses", "Go over every question you have missed, with its source", 20, ranked),
                     Task("cram", "Last cram on your two weakest topics", 15, weakest),
                     Task("rest", "Pack what you need, stop early, sleep 8 hours", 0)]
        days.append(Day(day.isoformat(), "cram", tasks))
    days.sort(key=lambda d: d.date)
    for day in days:   # keep every day inside the daily budget
        while day.minutes > daily_minutes and len(day.tasks) > 1:
            longest = max(day.tasks, key=lambda t: t.minutes)
            longest.minutes = max(10, longest.minutes - 5)
            if all(t.minutes <= 10 for t in day.tasks):
                break
    return days


def plan_to_dict(days: list[Day]) -> list[dict]:
    return [{**asdict(day), "minutes": day.minutes} for day in days]
