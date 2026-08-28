"""Standalone, throwaway recon script for the MERIDIAN "Place Account Hold"
flow, mirroring scripts/recon_meridian.py's style. NOT part of agent/ --
exploration only, to determine the real permission-boundary mechanism
before designing the Place Hold capability's escalation behavior.

Run: python scripts/recon_place_hold.py
"""

from __future__ import annotations

import re
import sys

from playwright.sync_api import Page, sync_playwright

BASE_URL = "https://web-sample.interface-hiring.com"
MEMBER_NO = "100234"


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
    for name in candidates:
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        attempts = [
            page.get_by_role("textbox", name=pattern),
            page.get_by_role("row", name=f"{name}:", exact=True).get_by_role("textbox"),
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


def sign_on(page: Page, operator_id: str, password: str) -> None:
    page.goto(BASE_URL, wait_until="networkidle")
    find_and_fill(page, ["Operator ID"], operator_id)
    find_and_fill(page, ["Password"], password)
    find_and_click(page, ["Sign On"])
    page.wait_for_load_state("networkidle")
    snap(page, f"main menu after sign-on as {operator_id}")


def attempt_place_hold(page: Page, label: str) -> None:
    hr(f"PLACE ACCOUNT HOLD -- as currently signed-on operator ({label})")

    if not find_and_click(page, ["Place Account Hold"]):
        print("WARNING: 'Place Account Hold' link not found on main menu.")
        return
    page.wait_for_load_state("networkidle")
    snap(page, "after clicking Place Account Hold (member selection?)")
    print(f"URL after click: {page.url}")

    # Likely lands on a member-selection screen like transfer's /members?next=...
    if page.locator('input[name="q"]').count() > 0:
        page.locator('input[name="q"]').first.fill(MEMBER_NO)
        find_and_click(page, ["Search", "Find", "Go", "Continue", "Next"])
        page.wait_for_load_state("networkidle")
        snap(page, "member search results")
        find_and_click(page, [MEMBER_NO, "View", "Select", "Continue", "Next"])
        page.wait_for_load_state("networkidle")
        snap(page, "after selecting member")
        print(f"URL after member select: {page.url}")

    # Pick first share dropdown/select if present, and a reason code if present.
    comboboxes = page.get_by_role("combobox")
    print(f"\ncomboboxes found on this screen: {comboboxes.count()}")
    for i in range(comboboxes.count()):
        try:
            sel = comboboxes.nth(i).locator("option:checked").text_content()
            print(f"  combobox[{i}] currently selected: {sel!r}")
        except Exception as exc:
            print(f"  combobox[{i}] read error: {exc}")

    if comboboxes.count() > 0:
        # First combobox is likely Share selection -- pick a non-default option.
        try:
            comboboxes.nth(0).select_option(index=1)
        except Exception as exc:
            print(f"could not select share option: {exc}")
        if comboboxes.count() > 1:
            try:
                comboboxes.nth(1).select_option(index=1)
            except Exception as exc:
                print(f"could not select reason code option: {exc}")

    textboxes = page.get_by_role("textbox")
    print(f"textboxes found: {textboxes.count()}")
    if textboxes.count() > 0:
        textboxes.last.fill("recon script -- place hold exploration")

    snap(page, "hold form filled")
    page.screenshot(path=f"/tmp/recon_hold_{label}_filled.png", full_page=True)

    if not find_and_click(page, ["Continue", "Next", "Review", "Submit", "Place Hold", "Post"]):
        print("WARNING: could not find a submit/continue control.")
        return
    page.wait_for_load_state("networkidle")
    snap(page, "after submitting hold form (review or result)")
    print(f"URL after submit: {page.url}")
    page.screenshot(path=f"/tmp/recon_hold_{label}_after_submit.png", full_page=True)

    # If there's a review/confirm step, look for a supervisor-override
    # in-context prompt vs a hard rejection.
    body_text = page.locator("body").inner_text()
    print("\n--- visible body text after submit ---")
    print(body_text[:3000])

    # Try to actually post/confirm if a further action is available, to see
    # the terminal state (in-context override prompt or hard rejection).
    if find_and_click(page, ["Post", "Confirm", "Place Hold", "Submit", "Authorize"]):
        page.wait_for_load_state("networkidle")
        snap(page, "after final confirm/post attempt")
        print(f"URL after final action: {page.url}")
        page.screenshot(path=f"/tmp/recon_hold_{label}_final.png", full_page=True)
        print("\n--- visible body text after final action ---")
        print(page.locator("body").inner_text()[:3000])


def sign_off(page: Page) -> None:
    find_and_click(page, ["Sign Off"])
    page.wait_for_load_state("networkidle")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            hr("A. AS TELLER1 (non-supervisor)")
            sign_on(page, "teller1", "password")
            attempt_place_hold(page, "teller1")
            sign_off(page)

            hr("B. AS SUPER1 (supervisor)")
            sign_on(page, "super1", "password")
            attempt_place_hold(page, "super1")
            sign_off(page)
        finally:
            hr("DONE -- closing browser")
            context.close()
            browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
