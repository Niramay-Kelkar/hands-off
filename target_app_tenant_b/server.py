"""Tenant B: a second, differently-branded "credit union" app serving the
same member search -> results -> detail flow as target_app.

This exists solely to answer one question (see root CLAUDE.md's
cross-tenant reuse addendum): does a compiled capability recorded
against tenant A replay unmodified against a different skin of the same
flow? Every interactive element keeps the exact same accessible role +
name as target_app (label "Member ID", button "Search", link text equal
to the member ID, cells "Name:" / "Savings Balance:") — that sameness is
the control. Everything else (branding, CSS, DOM shape, class names) is
deliberately different. See target_app_tenant_b/README.md.

App-layer fault injection: one interstitial-dialog ID (`20004`),
mirroring `target_app`'s `10004` pattern exactly — a fabricated search
result and detail record (not a DB row), same in-page modal markup
(`role="dialog"`, `aria-modal="true"`, `aria-label="Notice"`). Still no
slow-load ID — out of scope for this pass.

Binds to its own port, `TENANT_B_PORT` (default 8001) — separate from
target_app's 8000 — so both apps can run concurrently. Because a
compiled artifact's guardrail origin check is derived from the
artifact's own `target.entry_point`, replaying an artifact whose
`entry_point` says `localhost:8000` against this app running on 8001
requires a copy of that artifact with `target.entry_point` (and
`target.app`) repointed at `localhost:8001` — the mechanical
steps/locators/checkpoints are otherwise byte-identical. See
target_app_tenant_b/README.md and BUILD_LOG.md for how the cross-tenant
replay test does this.

Run: python -m target_app_tenant_b.server  (serves http://localhost:8001/members)
"""

import os

from flask import Flask, abort, render_template, request

from target_app_tenant_b.db import get_member

app = Flask(__name__)

INTERSTITIAL_ID = "20004"
# Fabricated on purpose, same as target_app's 10004: this ID has no row
# in members_b.db. Its presence in search results and its detail content
# are entirely synthetic, generated here rather than read from the
# database, so the fault-injection path never touches genuine
# business-outcome data.
INTERSTITIAL_FIXTURE = {
    "id": INTERSTITIAL_ID,
    "name": "Marcus Webb",
    "savings_balance": 3175.20,
    "account_number": "7723910485",
}


@app.route("/members")
def members_search():
    member_id = request.args.get("member_id", "").strip()
    searched = bool(member_id)
    result = None
    outcome = None  # None | "not_found" | "access_denied"

    if searched:
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
    port = int(os.environ.get("TENANT_B_PORT", "8001"))
    app.run(host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"), port=port, debug=True)
