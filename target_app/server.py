"""Deliberately hostile local "legacy bank" demo app.

Serves the member-lookup flow (search -> results -> detail) that
schema/example_artifact.json's member_balance_lookup capability is
discovered/replayed against.

Two genuinely different fault categories live in this file, kept
separate on purpose (see target_app/README.md):

- DB-driven business outcomes (found / not found / access denied) come
  from a real SELECT against members.db via target_app.db.get_member.
  These are legitimate, expected end states — they map to the
  artifact's `expected_outcomes`.
- App-layer fault injection (slow load, unexpected interstitial) is
  synthetic behavior hardcoded in the route handlers below, keyed off
  reserved IDs that are deliberately absent from members.db. These map
  to the artifact's recoverable/escalation conditions, not business
  outcomes.

Run: python -m target_app.server  (serves http://localhost:8000)
"""

import os
import time

from flask import Flask, abort, render_template, request, url_for

from target_app.db import get_member

app = Flask(__name__)

SLOW_LOAD_ID = "10003"
SLOW_LOAD_DELAY_SECONDS = 3.5

INTERSTITIAL_ID = "10004"
# Fabricated on purpose: this ID has no row in members.db. Its presence
# in search results and its detail content are entirely synthetic,
# generated here rather than read from the database, so that the
# fault-injection path never touches genuine business-outcome data.
INTERSTITIAL_FIXTURE = {
    "id": INTERSTITIAL_ID,
    "name": "Pat Whitfield",
    "savings_balance": 2310.00,
    "account_number": "6604215938",
}


@app.route("/members")
def members_search():
    member_id = request.args.get("member_id", "").strip()
    searched = bool(member_id)
    result = None
    outcome = None  # None | "not_found" | "access_denied"

    if searched:
        if member_id == SLOW_LOAD_ID:
            time.sleep(SLOW_LOAD_DELAY_SECONDS)

        if member_id == INTERSTITIAL_ID:
            result = INTERSTITIAL_FIXTURE
        else:
            row = get_member(member_id)
            if row is None:
                outcome = "not_found"
            elif row["access_denied"]:
                outcome = "access_denied"
            else:
                result = {
                    "id": row["id"],
                    "name": row["name"],
                    "savings_balance": row["savings_balance"],
                    "account_number": row["account_number"],
                }

    return render_template(
        "search.html",
        member_id=member_id,
        searched=searched,
        result=result,
        outcome=outcome,
    )


@app.route("/members/<member_id>")
def member_detail(member_id):
    if member_id == INTERSTITIAL_ID:
        return render_template(
            "detail.html", member=INTERSTITIAL_FIXTURE, interstitial=True
        )

    row = get_member(member_id)
    if row is None or row["access_denied"]:
        abort(404)

    member = {
        "id": row["id"],
        "name": row["name"],
        "savings_balance": row["savings_balance"],
        "account_number": row["account_number"],
    }
    return render_template("detail.html", member=member, interstitial=False)


@app.route("/branch-notice")
def branch_notice():
    return render_template("branch_notice.html")


if __name__ == "__main__":
    # FLASK_RUN_HOST defaults to loopback-only; docker-compose.yml sets it
    # to 0.0.0.0 so the container's published port is actually reachable.
    app.run(host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"), port=8000, debug=True)
