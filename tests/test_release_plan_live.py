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

        # Capture landing screen
        page.screenshot(path=str(SCREENSHOTS_DIR / "13_ReleasePlan_Landing.png"))
        print("[+] Captured 13_ReleasePlan_Landing.png")

        # 2. Check 5th nav icon
        print("[3] Clicking Schedule Release Plan (data-nav-idx=4)...")
        release_nav = page.wait_for_selector('.ets-nav-item[data-nav-idx="4"]', timeout=8000)
        assert release_nav is not None, "Nav button with data-nav-idx=4 not found!"
        page.click('.ets-nav-item[data-nav-idx="4"]')
        time.sleep(3)

        # Capture Release Plan workspace overview
        page.screenshot(path=str(SCREENSHOTS_DIR / "14_ReleasePlan_Workspace_Overview.png"))
        print("[+] Captured 14_ReleasePlan_Workspace_Overview.png")

        # 3. Verify Zero-Scroll
        scroll_pos = page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
        print(f"[*] Page window scroll position: {scroll_pos}")
        assert scroll_pos["y"] == 0, f"Page scrollY was {scroll_pos['y']}, expected 0 for zero-scroll compliance!"

        # 4. Verify Executive KPI Cards and Text
        content = page.content()
        assert "Total Planned Releases" in content, "KPI Total Planned Releases missing!"
        assert "Production Cutovers" in content, "KPI Production Cutovers missing!"
        assert "Gate SLA Compliance" in content, "KPI Gate SLA Compliance missing!"
        assert "State Release Management (RM) Command Centers" in content, "RM Command section missing!"
        assert "Alaska State MMIS" in content, "Alaska State card missing!"
        assert "North Dakota State MMIS" in content, "North Dakota State card missing!"
        assert "New Hampshire State MMIS" in content, "New Hampshire State card missing!"
        print("[+] All KPIs and RM Command Centers verified in DOM.")

        # 5. Test Detail Inspector Tabs for Default Selection
        print("[4] Testing Contextual Inspector Tabs...")
        # Tab 2: Granular WBS Milestones
        milestones_tab = page.locator('button[role="tab"]:has-text("WBS Milestones")')
        if milestones_tab.count() > 0:
            milestones_tab.first.click()
            time.sleep(1)
            page.screenshot(path=str(SCREENSHOTS_DIR / "16_ReleasePlan_WBS_Milestones.png"))
            print("[+] Captured 16_ReleasePlan_WBS_Milestones.png")

        # Tab 3: Multi-Tier Environment Lineage
        lineage_tab = page.locator('button[role="tab"]:has-text("Environment Lineage")')
        if lineage_tab.count() > 0:
            lineage_tab.first.click()
            time.sleep(1)
            page.screenshot(path=str(SCREENSHOTS_DIR / "17_ReleasePlan_Lineage.png"))
            print("[+] Captured 17_ReleasePlan_Lineage.png")

        # Tab 4: Raw JSON & Export
        payload_tab = page.locator('button[role="tab"]:has-text("Raw JSON & Export")')
        if payload_tab.count() > 0:
            payload_tab.first.click()
            time.sleep(1)
            page.screenshot(path=str(SCREENSHOTS_DIR / "18_ReleasePlan_Payload_Export.png"))
            print("[+] Captured 18_ReleasePlan_Payload_Export.png")

        # Reset to Tab 1
        tab1 = page.locator('button[role="tab"]:has-text("Stage Gates & Schedule")')
        if tab1.count() > 0:
            tab1.first.click()
            time.sleep(1)

        # 6. Test State Slicer: Select North Dakota (ND)
        print("[5] Testing State Slicer: selecting ND...")
        state_select = page.locator('div[data-testid="stSelectbox"]:has-text("State Filter")')
        state_select.click()
        time.sleep(0.5)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        time.sleep(2)

        page.screenshot(path=str(SCREENSHOTS_DIR / "15_ReleasePlan_ND_Slicer.png"))
        print("[+] Captured 15_ReleasePlan_ND_Slicer.png")

        # 7. Test Instant Search
        print("[6] Testing Instant Search...")
        search_input = page.locator('input[aria-label="Instant Search"]')
        if search_input.count() > 0:
            search_input.first.fill("ND.02.12.05")
            page.keyboard.press("Enter")
            time.sleep(1.5)
            page.screenshot(path=str(SCREENSHOTS_DIR / "19_ReleasePlan_Search_Filter.png"))
            print("[+] Captured 19_ReleasePlan_Search_Filter.png")

        browser.close()
        print("[SUCCESS] All Schedule Release Plan E2E live tests passed!")

if __name__ == "__main__":
    test_release_plan_e2e()
