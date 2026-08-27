"""Standalone, throwaway recon script against the Meridian sample bank app
(https://web-sample.interface-hiring.com). NOT part of agent/ — no LLM calls,
no artifact/replay machinery. Exploration only: dump accessibility snapshots,
raw HTML, and injected-failure responses to stdout for a human to read.

Run: python scripts/recon_meridian.py
"""

from __future__ import annotations

import re
import sys

from playwright.sync_api import Page, sync_playwright

BASE_URL = "https://web-sample.interface-hiring.com"
USERNAME = "teller1"
PASSWORD = "password"
BRANCH = "MAIN-001"
MEMBER_NO = "100234"

HIDDEN_INPUT_RE = re.compile(r'<input[^>]*type=["\']hidden["\'][^>]*>', re.IGNORECASE)
TOKEN_NAME_RE = re.compile(r'name=["\']([^"\']*(token|csrf|nonce|state|_key|__)[^"\']*)["\']', re.IGNORECASE)


def hr(title: str) -> None:
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)


def snap(page: Page, label: str) -> str:
    s = page.locator("body").aria_snapshot()
    print(f"\n--- aria_snapshot: {label} ---")
    print(s)
    return s


def find_and_fill(page: Page, candidates: list[str], value: str) -> bool:
    """Try a list of accessible-name / label candidates for a text field.

    This app renders label and input as separate <td> cells in the same row
    with no for=/aria-label association, so get_by_label finds nothing --
    a real usability data point in its own right (see section 2's note).
    The working strategy is structural: find the row whose accessible name
    contains the label text, then grab the textbox/combobox within it.
    """
    for name in candidates:
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        attempts = [
            page.get_by_label(name, exact=False),
            page.get_by_role("textbox", name=pattern),
            # exact=True on the row name matters: this app's outer wrapping
            # row concatenates every descendant's text into its own
            # accessible name, so a substring match on "Operator ID" also
            # matches the whole-panel row (which contains BOTH fields) and
            # .first would silently grab the wrong (first) textbox for every
            # candidate -- exact row names like "Operator ID:" avoid that.
            page.get_by_role("row", name=f"{name}:", exact=True).get_by_role("textbox"),
            page.get_by_role("row", name=f"{name}:", exact=True).get_by_role("combobox"),
        ]
        for locator in attempts:
            try:
                if locator.first.is_visible(timeout=1000):
                    locator.first.fill(value)
                    return True
            except Exception:
                continue
    return False


def find_and_click(page: Page, candidates: list[str]) -> bool:
    for name in candidates:
        for role in ("button", "link", "menuitem", "tab"):
            try:
                loc = page.get_by_role(role, name=re.compile(re.escape(name), re.IGNORECASE))
                if loc.first.is_visible(timeout=1000):
                    loc.first.click()
                    return True
            except Exception:
                continue
    return False


def section_1_login(page: Page) -> None:
    hr("1. SIGN-ON")
    page.goto(BASE_URL, wait_until="networkidle")
    snap(page, "sign-on page BEFORE login")

    filled_user = find_and_fill(page, ["Operator ID", "Username", "User ID", "User Name", "Teller ID", "Login"], USERNAME)
    filled_pass = find_and_fill(page, ["Password"], PASSWORD)
    filled_branch = find_and_fill(page, ["Branch", "Branch Code", "Branch ID"], BRANCH)
    print(f"\nfield fill attempts -> username: {filled_user}, password: {filled_pass}, branch: {filled_branch}")

    if not find_and_click(page, ["Sign On", "Sign In", "Log In", "Login", "Submit"]):
        print("WARNING: could not find a sign-on submit control by role/name; inspect snapshot above.")
    page.wait_for_load_state("networkidle")

    snap(page, "main menu AFTER login")


def section_2_member_inquiry(page: Page) -> tuple[str | None, str | None]:
    hr("2. MEMBER INQUIRY")

    # Discovered route from the main menu snapshot (link "Member Inquiry"
    # / "Member Inquiry / Selection" -> /members) -- navigate directly
    # rather than hunting for the nav link, since this app's top nav bar
    # doesn't carry a "Funds Transfer" link (section 3 needs the same fix).
    page.goto(f"{BASE_URL}/members", wait_until="networkidle")
    snap(page, "member inquiry search screen")

    # --- search by member number ---
    # The value textbox's own accessible name is just "Value:" -- what
    # you're searching by is set separately via the "Search by:" combobox
    # (already defaulted to "Member Number").
    # The value textbox's row accessible name is "Value: Search" (label +
    # submit button share a row/cell), so accessible-name matching can't
    # isolate it cleanly -- target the real field name from the REVIEW-step
    # HTML dump instead (input name="q", select name="by").
    page.locator('input[name="q"]').fill(MEMBER_NO)
    find_and_click(page, ["Search", "Find", "Go"])
    page.wait_for_load_state("networkidle")
    results_snapshot = snap(page, f"results screen (search by member number {MEMBER_NO})")

    if not find_and_click(page, [MEMBER_NO, "View", "Select", "Detail"]):
        print(f"WARNING: could not find a result row/link for member {MEMBER_NO}; "
              "the results screen above may already BE the detail screen "
              "(single exact match), or the row's accessible name may not "
              "contain the member number -- inspect the snapshot.")
    page.wait_for_load_state("networkidle")
    detail_snapshot = snap(page, "member detail/balance screen (via member-number search)")

    last_name = None
    m = re.search(r"Name:\s*([A-Za-z'\-]+),", detail_snapshot)
    if m:
        last_name = m.group(1)
        print(f"\nExtracted last name from detail screen: {last_name!r}")
    else:
        print("\nWARNING: could not extract a last name from the detail screen text; "
              "manual read of the snapshot above is required to drive the last-name search below.")

    # --- search by last name, if we found one ---
    if last_name:
        page.goto(f"{BASE_URL}/members", wait_until="networkidle")
        page.locator('select[name="by"]').select_option(label="Last Name")
        page.locator('input[name="q"]').fill(last_name)
        find_and_click(page, ["Search", "Find", "Go"])
        page.wait_for_load_state("networkidle")
        snap(page, f"results screen (search by last name {last_name!r})")

    print(
        "\nUSABILITY NOTE (fill in manually after reading the snapshots above):\n"
        "  - Can a label cell be distinguished from its value cell from role+name alone?\n"
        "  - Are result-row buttons/links uniquely named (e.g. contain the member number),\n"
        "    or do multiple rows collapse to the same accessible name?\n"
        "  - Compare against target_app's markup (which is deliberately hostile but has\n"
        "    ARIA roles/labels present) -- is this meaningfully worse or about the same?"
    )
    return last_name, detail_snapshot


def section_3_funds_transfer(page: Page) -> None:
    hr("3. FUNDS TRANSFER (up to REVIEW step, no post)")

    # Discovered route: main menu's "Funds Transfer" link is /members?next=transfer
    # -- it's the same member-selection screen as section 2, but continuing
    # to the transfer flow once a member is picked, rather than a direct
    # transfer-only entry point.
    page.goto(f"{BASE_URL}/members?next=transfer", wait_until="networkidle")
    snap(page, "funds transfer entry screen (member selection, next=transfer)")

    page.locator('input[name="q"]').fill(MEMBER_NO)
    find_and_click(page, ["Search", "Find", "Go", "Continue", "Next"])
    page.wait_for_load_state("networkidle")
    find_and_click(page, [MEMBER_NO, "View", "Select", "Continue", "Next"])
    page.wait_for_load_state("networkidle")
    snap(page, "funds transfer form screen")

    # Both share dropdowns default to the SAME share on load. Their
    # accessible names are the whole enclosing row's concatenated text
    # (ambiguous, same problem as the login form), so pick by DOM position
    # instead -- From Share is the first combobox on the page, To Share the
    # second -- and by index rather than option label, since the aria
    # snapshot's whitespace-normalized option text didn't exact-match the
    # live DOM text for select_option(label=...).
    #
    # 100234-S0001-6 / -S0001-7 both show "OPEN" on the member detail screen
    # but still fail server-side validation with "Source share is HOLD and
    # cannot be debited" -- the displayed OPEN/HOLD badge does not reliably
    # predict debit eligibility for these numbered sub-shares. CERT-26 (From)
    # / MMKT-10 (To) posts a clean review with no validation errors instead.
    comboboxes = page.get_by_role("combobox")
    comboboxes.nth(0).select_option(index=25)  # 100234-CERT-26
    comboboxes.nth(1).select_option(index=9)   # 100234-MMKT-10
    for i in range(comboboxes.count()):
        sel = comboboxes.nth(i).locator("option:checked").text_content()
        print(f"share dropdown[{i}] selected: {sel!r}")
    textboxes = page.get_by_role("textbox")
    textboxes.nth(0).fill("1.00")  # Amount
    textboxes.nth(1).fill("recon script test -- not posted")  # Memo

    find_and_click(page, ["Continue", "Next", "Review"])
    page.wait_for_load_state("networkidle")
    snap(page, "funds transfer REVIEW screen")

    html = page.content()
    print("\n--- REVIEW screen: raw HTML dump (page.content()) ---")
    print(html)

    hidden_inputs = HIDDEN_INPUT_RE.findall(html)
    token_names = TOKEN_NAME_RE.findall(html)
    print(f"\nHidden <input type=hidden> tags found ({len(hidden_inputs)}):")
    for tag in hidden_inputs:
        print(f"  {tag}")
    print(f"\ntoken-shaped field names found ({len(set(t[0] for t in token_names))}):")
    for name, _ in set(token_names):
        print(f"  name={name!r}")

    if hidden_inputs:
        # Grab the first hidden input's value, go back and forward, and
        # compare -- distinguishes a forward-nav artifact from a
        # freshly-generated-per-load token.
        first_name_match = re.search(r'name=["\']([^"\']+)["\']', hidden_inputs[0])
        first_val_match = re.search(r'value=["\']([^"\']*)["\']', hidden_inputs[0])
        field_name = first_name_match.group(1) if first_name_match else None
        val_before = first_val_match.group(1) if first_val_match else None
        print(f"\nTracking field {field_name!r}, value before back/forward: {val_before!r}")

        page.go_back()
        page.wait_for_load_state("networkidle")
        page.go_forward()
        page.wait_for_load_state("networkidle")
        html_after = page.content()
        val_after = None
        if field_name:
            m = re.search(rf'name=["\']{re.escape(field_name)}["\'][^>]*value=["\']([^"\']*)["\']', html_after)
            if m:
                val_after = m.group(1)
        print(f"value after back/forward: {val_after!r}")
        if val_before is not None and val_after is not None:
            verdict = "SAME (looks like a forward-navigation artifact)" if val_before == val_after else \
                      "DIFFERENT (looks freshly generated per screen load)"
            print(f"Verdict: {verdict}")
        else:
            print("Could not compare -- inspect the HTML dumps above manually.")
    else:
        print("\nNo type=hidden inputs found; re-check the raw HTML dump above for any token-shaped fields.")

    print("\nNOT clicking final post -- navigating away instead.")
    page.goto(BASE_URL, wait_until="networkidle")


def section_4_injects(page: Page) -> None:
    hr("4. ?inject= CASES")

    # Real routes discovered in sections 2/3: member inquiry is /members
    # (by=number|name, q=value), funds transfer is /members/<id>/transfer.
    cases = [
        ("notfound", f"{BASE_URL}/members?by=number&q=999999&inject=notfound",
         "GET member inquiry for nonexistent member with ?inject=notfound"),
        ("validation", f"{BASE_URL}/members/{MEMBER_NO}/transfer?inject=validation",
         "GET transfer screen with ?inject=validation"),
        ("maintenance", f"{BASE_URL}/members/{MEMBER_NO}/transfer?inject=maintenance",
         "GET transfer screen with ?inject=maintenance"),
    ]

    for key, url, description in cases:
        print(f"\n--- {description} ---")
        print(f"URL: {url}")
        try:
            response = page.goto(url, wait_until="networkidle")
            status = response.status if response else None
            print(f"HTTP status: {status}")

            # Heuristic: if a modal/dialog role is present, prefer a screenshot
            # (interstitials often don't carry meaningful text in page.content()).
            dialog_present = False
            try:
                dialog_present = page.get_by_role("dialog").first.is_visible(timeout=1000)
            except Exception:
                dialog_present = False

            if dialog_present:
                screenshot_path = f"/tmp/recon_meridian_inject_{key}.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"Interstitial/dialog detected -- screenshot saved to {screenshot_path}")
                print(f"\naria_snapshot of the dialog page:")
                print(page.locator("body").aria_snapshot())
            else:
                print("\nFull response body (page.content()):")
                print(page.content())
        except Exception as exc:
            print(f"ERROR fetching {url}: {exc}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            section_1_login(page)
            section_2_member_inquiry(page)
            section_3_funds_transfer(page)
            section_4_injects(page)
        finally:
            hr("DONE -- closing browser")
            context.close()
            browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
