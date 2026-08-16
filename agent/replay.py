"""CLI: python -m agent.replay --capability <path> --input key=value [...]"""

from __future__ import annotations

import argparse
import json
import sys

from agent.engine import InputValidationError, run_capability
from agent.models import Capability


def _parse_input(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--input must be key=value, got {raw!r}")
    key, _, value = raw.partition("=")
    return key, value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a capability artifact against its live target.")
    parser.add_argument("--capability", required=True, help="path to a capability artifact JSON file")
    parser.add_argument(
        "--input", action="append", default=[], type=_parse_input, dest="inputs", help="key=value, repeatable"
    )
    parser.add_argument("--headless", action="store_true", help="run the browser headless instead of headed")
    args = parser.parse_args(argv)

    artifact = Capability.load(args.capability)
    params = dict(args.inputs)

    try:
        result = run_capability(artifact, params, headed=not args.headless)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result.model_dump(), indent=2, default=str))
    return {"success": 0, "business_outcome": 0, "hard_failure": 1}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
