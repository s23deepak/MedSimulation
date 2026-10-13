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
            start = page.locator("#caseGrid .case-card .case-start-btn").first
            expect(start).to_be_visible(timeout=15_000)
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
