"""CLI: python -m agent.replay --capability <path> --input key=value [...]

--repeat N reruns the same capability + params N times in a row and
reports a stability summary instead of a single result — evidence for
the brief's own claim that record-once/replay-many is only viable
because the target UI is stable across runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter

from agent.engine import InputValidationError, run_capability
from agent.models import Capability


def _parse_input(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--input must be key=value, got {raw!r}")
    key, _, value = raw.partition("=")
    return key, value


def _run_once(artifact: Capability, params: dict[str, str], headed: bool):
    start = time.perf_counter()
    result = run_capability(artifact, params, headed=headed)
    duration = time.perf_counter() - start
    return result, duration


def _run_stability(artifact: Capability, params: dict[str, str], headed: bool, repeat: int) -> int:
    status_counts: Counter = Counter()
    outcome_counts: Counter = Counter()
    durations: list[float] = []

    for i in range(1, repeat + 1):
        result, duration = _run_once(artifact, params, headed)
        durations.append(duration)
        status_counts[result.status] += 1
        outcome_code = getattr(result, "outcome_code", None)
        if outcome_code:
            outcome_counts[outcome_code] += 1

        detail = f" outcome={outcome_code}" if outcome_code else ""
        print(f"run {i}/{repeat}: status={result.status}{detail} duration={duration:.2f}s")

    print()
    print(f"--- stability summary ({repeat} runs, {artifact.capability_id}, {params}) ---")
    print(f"success (non-hard-failure): {repeat - status_counts['hard_failure']}/{repeat}")
    print("by status:")
    for status in ("success", "business_outcome", "hard_failure"):
        if status_counts[status]:
            print(f"  {status}: {status_counts[status]}")
    if outcome_counts:
        print("by outcome_code:")
        for code, count in outcome_counts.most_common():
            print(f"  {code}: {count}")
    print(
        f"duration: min={min(durations):.2f}s max={max(durations):.2f}s "
        f"avg={sum(durations) / len(durations):.2f}s"
    )

    return 0 if status_counts["hard_failure"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a capability artifact against its live target.")
    parser.add_argument("--capability", required=True, help="path to a capability artifact JSON file")
    parser.add_argument(
        "--input", action="append", default=[], type=_parse_input, dest="inputs", help="key=value, repeatable"
    )
    parser.add_argument("--headless", action="store_true", help="run the browser headless instead of headed")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run the same capability + params N times and report a stability summary (default 1, i.e. current behavior)",
    )
    args = parser.parse_args(argv)

    if args.repeat < 1:
        print("--repeat must be >= 1", file=sys.stderr)
        return 2

    artifact = Capability.load(args.capability)
    params = dict(args.inputs)

    try:
        if args.repeat == 1:
            result, _ = _run_once(artifact, params, headed=not args.headless)
            print(json.dumps(result.model_dump(), indent=2, default=str))
            return {"success": 0, "business_outcome": 0, "hard_failure": 1}[result.status]

        return _run_stability(artifact, params, headed=not args.headless, repeat=args.repeat)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
