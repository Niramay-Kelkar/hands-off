"""Agent-facing capability catalog + invoke API — the one stretch goal
this project takes on. A catalog of callable capabilities an AI agent
could discover and invoke, backed directly by compiled artifacts.

Demonstration-scale only, deliberately: no auth, no queueing, one
synchronous browser run per invoke, no rate limiting. Building scaling
infrastructure isn't rewarded per the brief — see CLAUDE.md and
REPORT.md's Cuts section for why this stops here.

CAPABILITY_API_HEADED (default "true") controls whether that run is
headed or headless — see CLAUDE.md for why this is the seam a
production deployment would flip, not the browser dependency itself
going away.

Run: python -m agent.capability_api  (serves http://localhost:8200)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

from agent.approval import is_approved
from agent.engine import InputValidationError, run_capability
from agent.models import Capability

CAPABILITIES_DIR = Path("schema/capabilities")

app = Flask(__name__)


def _headed_default() -> bool:
    return os.environ.get("CAPABILITY_API_HEADED", "true").strip().lower() not in ("0", "false", "no")


def _scan_capabilities() -> dict[str, Capability]:
    """schema/capabilities/ only — schema/example_artifact.json stays
    schema documentation, excluded by directory boundary, not inferred."""
    catalog: dict[str, Capability] = {}
    for path in sorted(CAPABILITIES_DIR.glob("*.json")):
        try:
            cap = Capability.load(path)
        except Exception as exc:
            print(f"skipping {path}: {exc}", file=sys.stderr)
            continue
        catalog[cap.capability_id] = cap
    return catalog


@app.route("/capabilities")
def list_capabilities():
    catalog = _scan_capabilities()
    return jsonify(
        {
            "capabilities": [
                {
                    "capability_id": cap.capability_id,
                    "description": cap.description,
                    "version": cap.version,
                    "risk_class": cap.risk_class,
                    "inputs": [{"name": i.name, "type": i.type, "required": i.required} for i in cap.inputs],
                    "outputs": [{"name": o.name, "type": o.type} for o in cap.outputs],
                }
                for cap in catalog.values()
            ]
        }
    )


@app.route("/capabilities/<capability_id>/invoke", methods=["POST"])
def invoke_capability(capability_id: str):
    catalog = _scan_capabilities()
    capability = catalog.get(capability_id)
    if capability is None:
        return jsonify({"error": f"no capability {capability_id!r} found under {CAPABILITIES_DIR}/"}), 404

    if not is_approved(capability_id):
        return (
            jsonify(
                {
                    "error": (
                        f"capability {capability_id!r} is not approved for unattended invocation "
                        f"(status: draft). Approve it via schema/capabilities/{capability_id}.approval.json "
                        "before invoking through this API."
                    )
                }
            ),
            403,
        )

    params = request.get_json(silent=True)
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return jsonify({"error": "request body must be a JSON object of param name/value pairs"}), 400

    try:
        result = run_capability(capability, params, headed=_headed_default())
    except InputValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # safety net — run_capability already turns most failures into HardFailureResult
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    status_code = 500 if result.status == "hard_failure" else 200
    return jsonify(result.model_dump(mode="json")), status_code


if __name__ == "__main__":
    app.run(port=8200, debug=True)
