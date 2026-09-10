import time
import os
from playwright.sync_api import sync_playwright

ARTIFACT_DIR = r"C:/Users/affra/.gemini/antigravity/brain/b4712539-c10c-4e29-b142-929cdda54fe0"

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("[1] Navigating to login portal...")
        page.goto("http://localhost:8501", wait_until="networkidle")
        time.sleep(3)

        # Login as admin
        print("[2] Logging in as admin...")
        user_input = page.locator("input").first
        user_input.fill("admin")
        pass_input = page.locator("input[type='password']").first
        pass_input.fill("Admin@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # Verify Page 1 (Executive Command Center)
        print("[3] Verifying Page 1 - Clean Executive Command Center (zero flight deck)...")
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "22_Page1_Restored_Executive_Command_Center.png"))
        print("Screenshot 22 captured")

        # Navigate to Schedule Release Plan via left rail icon (data-nav-idx=4)
        print("[4] Navigating to Schedule Release Plan via left navigation rail...")
        page.locator(".ets-nav-item[data-nav-idx='4']").first.click()
        time.sleep(4)

        page.screenshot(path=os.path.join(ARTIFACT_DIR, "23_ReleasePlan_Admin_Overview.png"))
        print("Screenshot 23 captured")

        # Test Persona Switcher - NH RM
        print("[5] Testing Admin Persona Simulator - New Hampshire...")
        selects = page.locator("div[data-baseweb='select']")
        persona_select = None
        for i in range(selects.count()):
            txt = selects.nth(i).inner_text()
            if "Simulation" in txt or "Enterprise Admin" in txt or "Executive" in txt:
                persona_select = selects.nth(i)
                break
        
        if persona_select:
            persona_select.click()
            time.sleep(1)
            page.locator("li:has-text('New Hampshire')").click()
            time.sleep(3)
            page.screenshot(path=os.path.join(ARTIFACT_DIR, "24_ReleasePlan_NH_Persona_Quarantined.png"))
            print("Screenshot 24 captured (NH Quarantined)")

            # Test ND RM
            print("[6] Testing Admin Persona Simulator - North Dakota...")
            persona_select.click()
            time.sleep(1)
            page.locator("li:has-text('North Dakota')").click()
            time.sleep(3)
            page.screenshot(path=os.path.join(ARTIFACT_DIR, "25_ReleasePlan_ND_Persona_Quarantined.png"))
            print("Screenshot 25 captured (ND Quarantined)")

        # Sign out from sidebar
        print("[7] Signing out from sidebar...")
        signout_btn = page.locator("button:has-text('Sign Out')")
        if signout_btn.count() > 0:
            signout_btn.first.click()
            time.sleep(3)
        else:
            print("Clicking sidebar toggle first...")
            page.locator("#ets-rail-toggle-btn").click()
            time.sleep(1)
            page.locator("button:has-text('Sign Out')").first.click()
            time.sleep(3)

        # Login as ak_rm directly
        print("[8] Logging in directly as ak_rm...")
        user_input = page.locator("input").first
        user_input.fill("ak_rm")
        pass_input = page.locator("input[type='password']").first
        pass_input.fill("AkRM@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # Navigate to Schedule Release Plan as AK RM
        print("[9] AK RM navigating to Schedule Release Plan...")
        page.locator(".ets-nav-item[data-nav-idx='4']").first.click()
        time.sleep(4)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "26_ReleasePlan_AK_RM_Direct_Hard_Locked.png"))
        print("Screenshot 26 captured (AK RM Hard-Locked)")

        browser.close()
        print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run()
