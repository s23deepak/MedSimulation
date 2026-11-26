"""Exercise the learner flow in a real browser against a running local app."""

import argparse

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(f"{args.base_url.rstrip('/')}/simulation", wait_until="domcontentloaded")
            library = page.get_by_role("region", name="Recommended cases")
            rows = library.get_by_role("list", name="Recommended cases").get_by_role("listitem")
            start = library.get_by_role("button", name="Start case").first
            expect(start).to_be_visible(timeout=15_000)
            original_titles = rows.get_by_role("heading").all_text_contents()
            search = library.get_by_role("searchbox", name="Search cases")
            search.fill(original_titles[0])
            expect(rows.first.get_by_role("heading")).to_have_text(original_titles[0])
            search.fill("no-such-case-0000")
            expect(library.get_by_text("0 cases", exact=True)).to_be_visible()
            library.get_by_role("button", name="Clear filters").click()
            assert rows.get_by_role("heading").all_text_contents() == original_titles
            details = rows.first.get_by_role("button", name="Case details")
            details.focus()
            details.press("Enter")
            expect(details).to_have_attribute("aria-expanded", "true")
            expect(rows.first.get_by_text("Source", exact=True)).to_be_visible()
            details.press("Enter")
            expect(details).to_have_attribute("aria-expanded", "false")
            start.click()
            expect(page.locator("#screen-sim.active")).to_be_visible(timeout=15_000)
            expect(page.locator("#presentationText")).not_to_be_empty()

            page.locator("#historyInput").fill("What brings you in today?")
            page.locator("#historyInput").press("Enter")
            expect(page.locator("#chatMessages .msg-patient")).to_have_count(2, timeout=15_000)
            expect(page.locator("#chatMessages .msg-patient").last).to_contain_text(
                "synthetic patient response"
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
