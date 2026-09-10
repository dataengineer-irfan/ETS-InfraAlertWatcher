import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ARTIFACT_DIR = Path(r"C:\Users\affra\.gemini\antigravity\brain\b4712539-c10c-4e29-b142-929cdda54fe0")

def run_verification():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        console_msgs = []
        page.on("console", lambda msg: console_msgs.append(f"[{msg.type}] {msg.text}"))

        print("Navigating to app...")
        page.goto("http://localhost:8501", timeout=30000)
        
        # Robust Login
        print("Waiting for login inputs...")
        page.wait_for_selector('input[aria-label="Username"]', timeout=20000)
        page.fill('input[aria-label="Username"]', "admin")
        page.fill('input[aria-label="Password"]', "Admin@ETS2026!")
        page.click('button:has-text("Sign In to Watchtower")')
        print("Clicked sign in, waiting for landing...")
        page.wait_for_timeout(6000)

        # 1. Capture Page 1: Schedule Release Plan
        print("Capturing Page 1...")
        page.screenshot(path=str(ARTIFACT_DIR / "46_Page1_ReleaseCutoffAlerts_Admin.png"), full_page=True)
        print("Saved 46_Page1_ReleaseCutoffAlerts_Admin.png")

        # 2. Capture Page 2: Executive Command Center
        print("Navigating to Page 2 (Command Center)...")
        btn_cmd = page.query_selector('.ets-nav-item[data-nav-idx="1"]')
        if btn_cmd:
            btn_cmd.click()
            page.wait_for_timeout(5000)
            page.screenshot(path=str(ARTIFACT_DIR / "47_Page2_CommandCenter_Fitted.png"), full_page=True)
            print("Saved 47_Page2_CommandCenter_Fitted.png")

        # 3. Capture Page 4: Governance & Alerts
        print("Navigating to Page 4 (Governance)...")
        btn_gov = page.query_selector('.ets-nav-item[data-nav-idx="3"]')
        if btn_gov:
            btn_gov.click()
            page.wait_for_timeout(5000)
            page.screenshot(path=str(ARTIFACT_DIR / "48_Page4_Governance_AlignedBoxes.png"), full_page=True)
            print("Saved 48_Page4_Governance_AlignedBoxes.png")

            # Click the Release Cutoff Alerts tab inside Governance
            subtab = page.query_selector('button[role="tab"]:has-text("Release Cutoff")')
            if subtab:
                subtab.click()
                page.wait_for_timeout(3000)
                page.screenshot(path=str(ARTIFACT_DIR / "50_Page4_ReleaseCutoffAlert_Tab.png"), full_page=True)
                print("Saved 50_Page4_ReleaseCutoffAlert_Tab.png")

        # 4. Capture Page 5: RBAC Workspace
        print("Navigating to Page 5 (RBAC)...")
        btn_rbac = page.query_selector('.ets-nav-item[data-nav-idx="4"]')
        if btn_rbac:
            btn_rbac.click()
            page.wait_for_timeout(5000)
            page.screenshot(path=str(ARTIFACT_DIR / "49_Page5_RBAC_GrafanaDesign.png"), full_page=True)
            print("Saved 49_Page5_RBAC_GrafanaDesign.png")

        print("\n=== CONSOLE LOG SUMMARY ===")
        print(f"Total console messages: {len(console_msgs)}")
        for m in console_msgs[:10]:
            print(" ", m)

        browser.close()

if __name__ == "__main__":
    run_verification()
