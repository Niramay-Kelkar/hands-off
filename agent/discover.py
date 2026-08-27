"""CLI: python -m agent.discover --goal "..." --target <url> --out <path>

Writes the raw discovery trajectory to --out (see agent.trajectory) —
NOT a compiled capability artifact. The compiler that turns a successful
trajectory into a schema/example_artifact.json-shaped file is a separate,
later piece.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from agent.discovery import DEFAULT_MAX_STEPS, DEFAULT_MODEL, run_discovery


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run the discovery agent against a live target.")
    parser.add_argument("--goal", required=True, help="natural-language goal, e.g. 'look up member 12345 and read their savings balance'")
    parser.add_argument("--target", required=True, help="entry point URL to start from")
    parser.add_argument("--out", required=True, help="path to write the raw trajectory JSON to")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--headless", action="store_true", help="run the browser headless instead of headed")
    parser.add_argument(
        "--allow-route",
        action="append",
        dest="allowlist_routes",
        default=None,
        help="glob route pattern to allow (repeatable); defaults to '/*' on the target's origin",
    )
    parser.add_argument(
        "--redact-fields",
        action="append",
        dest="redact_fields",
        default=None,
        help=(
            "extra label/header text to redact (repeatable), e.g. --redact-fields 'E-mail' "
            "--redact-fields 'Share ID'; merged with the built-in baseline "
            "(password/ssn/account_number/token/secret), which always applies regardless"
        ),
    )
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (checked .env and the environment).", file=sys.stderr)
        return 2

    trajectory = run_discovery(
        args.goal,
        args.target,
        max_steps=args.max_steps,
        model=args.model,
        headed=not args.headless,
        allowlist_routes=args.allowlist_routes,
        redact_fields=args.redact_fields,
        out_path=args.out,
    )

    print(f"status: {trajectory.status}")
    print(f"trajectory written to {args.out}")
    return {"done": 0, "max_steps_reached": 1, "hard_failure": 2}.get(trajectory.status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
