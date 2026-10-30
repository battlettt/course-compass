#!/usr/bin/env python3
"""Run the Course Compass LLM evaluation against a REAL provider.

This makes real model calls (it is deliberately not part of the unit tests).
Run it from the repository root:

    python3 scripts/eval_llm.py                       # Claude CLI provider, default model
    COURSE_COMPASS_CLAUDE_MODEL=haiku python3 scripts/eval_llm.py --tag haiku
    python3 scripts/eval_llm.py --provider openai     # needs COURSE_COMPASS_API_KEY
    python3 scripts/eval_llm.py --rescore .course-compass/eval/run-results.json   # no model calls

It prints a summary and writes `<out>/<tag>-results.json` with every answer,
quiz item and score. Raw outputs go under `.course-compass/` (git-ignored) by
default. See docs/EVAL_LLM.md for the method and the recorded results.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from course_compass.ingest import ingest_paths  # noqa: E402
from course_compass.llm import (ClaudeCLIClient, ClaudeCLIConfig, LLMClient, LLMConfig,  # noqa: E402
                                LLMError)
from course_compass.llm_eval import format_summary, load_spec, rescore, run_eval  # noqa: E402


def build_client(provider: str, model: str | None):
    import os
    env = dict(os.environ)
    if provider == "claude":
        if model:
            env["COURSE_COMPASS_CLAUDE_MODEL"] = model
        return ClaudeCLIClient(ClaudeCLIConfig.from_env(env))
    if model:
        env["COURSE_COMPASS_MODEL"] = model
    return LLMClient(LLMConfig.from_env(env))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", choices=["claude", "openai"], default="claude")
    parser.add_argument("--model", help="override the provider's model for this run")
    parser.add_argument("--spec", default=str(ROOT / "examples" / "eval" / "llm_eval.json"))
    parser.add_argument("--judge", choices=["same", "none"], default="same",
                        help="second-opinion LLM judge for grounding and quiz keys (default: same provider)")
    parser.add_argument("--judge-model", help="model for the judge (default: same as --model)")
    parser.add_argument("--workers", type=int, default=3, help="parallel model calls (default 3)")
    parser.add_argument("--only", help="comma-separated question/quiz ids to run")
    parser.add_argument("--out", default=str(ROOT / ".course-compass" / "eval"))
    parser.add_argument("--tag", default="run", help="name for the results file")
    parser.add_argument("--no-repair-probes", action="store_true",
                        help="skip the forced-repair probes (saves a few model calls)")
    parser.add_argument("--rescore", metavar="RESULTS_JSON",
                        help="recompute scores of a saved run with the current scorers; no model calls")
    args = parser.parse_args(argv)

    spec = load_spec(args.spec)
    if args.only:
        wanted = set(args.only.split(","))
        spec["questions"] = [q for q in spec["questions"] if q["id"] in wanted]
        spec["quizzes"] = [q for q in spec["quizzes"] if q["id"] in wanted]
    chunks = ingest_paths([str(ROOT / p) for p in spec["notes"]], [ROOT])
    if args.no_repair_probes:
        spec["repair_probes"] = []
    if args.rescore:
        saved = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        results = rescore(saved, spec, chunks)
        print(f"Rescored {args.rescore} (provider {saved.get('provider')}, judge {saved.get('judge')})\n")
        print(format_summary(results["summary"]))
        saved["summary"] = results["summary"]
        saved["answers"] = [asdict(a) for a in results["answers"]]
        saved["quizzes"] = [asdict(q) for q in results["quizzes"]]
        out = Path(args.rescore).with_name(Path(args.rescore).stem + "-rescored.json")
        out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        print(f"\nWrote {out}")
        return 0
    try:
        client = build_client(args.provider, args.model)
        judge = None if args.judge == "none" else build_client(args.provider, args.judge_model or args.model)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Provider: {getattr(client, 'label', 'LLM')}; {len(chunks)} chunks; "
          f"{len(spec['questions'])} questions; {len(spec['quizzes'])} quizzes", flush=True)
    start = time.monotonic()
    results = run_eval(spec, chunks, client, judge=judge, workers=args.workers,
                       log=lambda message: print(message, flush=True))
    elapsed = time.monotonic() - start
    print(f"\nFinished in {elapsed:.0f}s\n")
    print(format_summary(results["summary"]))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.tag}-results.json"
    path.write_text(json.dumps({
        "provider": getattr(client, "label", "LLM"),
        "judge": None if judge is None else getattr(judge, "label", "LLM"),
        "elapsed_seconds": round(elapsed),
        "summary": results["summary"],
        "answers": [asdict(a) for a in results["answers"]],
        "quizzes": [asdict(q) for q in results["quizzes"]],
        "repair_probes": [asdict(p) for p in results["repair_probes"]],
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {path}")
    errors = [a for a in results["answers"] if a.status == "error"] + \
             [q for q in results["quizzes"] if q.status == "error"]
    for item in errors:
        print(f"provider error on {item.id}: {item.error}", file=sys.stderr)
    return 1 if errors and len(errors) == len(results["answers"]) + len(results["quizzes"]) else 0


if __name__ == "__main__":
    sys.exit(main())
