"""CLI: python -m agent.compile --trajectory <path> --policy <path> \
    --param name=value [...] --out <path>

Merges a raw discovery Trajectory (agent.trajectory) with an authored
PolicySpec (agent.policy) into a final Capability artifact
(schema/example_artifact.json-shaped). See agent/compiler.py for the
merge rules.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.compiler import CompilationError, compile_trajectory
from agent.policy import PolicySpec
from agent.trajectory import Trajectory


def _parse_param(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--param must be key=value, got {raw!r}")
    key, _, value = raw.partition("=")
    return key, value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile a discovery trajectory + policy file into a capability artifact.")
    parser.add_argument("--trajectory", required=True, help="path to a raw trajectory JSON file (agent.discover --out)")
    parser.add_argument("--policy", required=True, help="path to a policy file (may be a full Capability JSON; extra fields ignored)")
    parser.add_argument("--param", action="append", default=[], type=_parse_param, dest="params", help="key=value, repeatable")
    parser.add_argument("--out", required=True, help="path to write the compiled capability artifact to")
    parser.add_argument("--app", default=None, help="target.app label; defaults to the entry point's host")
    parser.add_argument("--surface-type", default="web_legacy", help="target.surface_type; defaults to 'web_legacy'")
    args = parser.parse_args(argv)

    trajectory = Trajectory.model_validate_json(Path(args.trajectory).read_text())
    policy = PolicySpec.load(args.policy)
    params = dict(args.params)

    try:
        capability = compile_trajectory(trajectory, policy, params, app=args.app, surface_type=args.surface_type)
    except CompilationError as exc:
        print(f"compilation failed: {exc}", file=sys.stderr)
        return 2

    Path(args.out).write_text(capability.model_dump_json(indent=2))
    print(f"compiled capability written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
