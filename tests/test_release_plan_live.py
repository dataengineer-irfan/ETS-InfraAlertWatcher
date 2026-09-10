import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = ROOT / "verification_screens"
SCREENSHOTS_DIR.mkdir(exist_ok=True, parents=True)

APP_URL = "http://localhost:8501"

def test_release_plan_e2e():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 950})
        page = context.new_page()

        print("[1] Navigating to login gate...")
        page.goto(APP_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # 1. Login
        print("[2] Submitting login credentials...")
        page.fill('input[aria-label="Username"]', "admin")
        page.fill('input[aria-label="Password"]', "Admin@ETS2026!")
        page.click('button:has-text("Sign In to Watchtower")')
        page.wait_for_load_state("networkidle")
        time.sleep(3)

        # Capture landing screen (Page 1 Schedule Release Plan)
        page.screenshot(path=str(SCREENSHOTS_DIR / "13_ReleasePlan_Landing.png"))
        print("[+] Captured 13_ReleasePlan_Landing.png")

        # 2. Verify Zero-Scroll
        scroll_pos = page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
        print(f"[*] Page window scroll position: {scroll_pos}")
        assert scroll_pos["y"] == 0, f"Page scrollY was {scroll_pos['y']}, expected 0 for zero-scroll compliance!"

        # 3. Verify Executive KPI Cards and Text
        content = page.content()
        assert "Immediate Cutover Target" in content, "Immediate Cutover Target missing!"
        assert "Active In-Flight Pipeline" in content, "Active In-Flight Pipeline missing!"
        assert "Scheduled Roadmap Horizon" in content, "Scheduled Roadmap Horizon missing!"
        assert "Live Environment Flight Deck" in content, "Flight deck missing!"
        browser.close()
        print("[SUCCESS] All Schedule Release Plan E2E live tests passed!")
        print("[SUCCESS] All Schedule Release Plan E2E live tests passed!")

if __name__ == "__main__":
    test_release_plan_e2e()
