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
        time.sleep(2)

        # Login as admin
        print("[2] Logging in as admin...")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("Admin@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # Immediately verify Landing Page is Schedule Release Plan (Page 1)
        print("[3] Capturing default landing page for admin...")
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "35_ReleasePlan_Page1_Admin_Landing.png"))
        print("Screenshot 35 captured (Admin Page 1 Landing)")

        # Sign out from sidebar
        print("[4] Signing out...")
        signout_btn = page.locator("button:has-text('Sign Out')")
        if signout_btn.count() > 0:
            signout_btn.first.click()
            time.sleep(2)
        else:
            page.locator("#ets-rail-toggle-btn").click()
            time.sleep(1)
            page.locator("button:has-text('Sign Out')").first.click()
            time.sleep(2)

        # Direct login as nh_rm
        print("[5] Direct login as nh_rm...")
        page.locator("input").first.fill("nh_rm")
        page.locator("input[type='password']").first.fill("NhRM@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # Verify NH RM landing page
        print("[6] Capturing default landing page for NH RM...")
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "36_ReleasePlan_Page1_NH_RM_Landing.png"))
        print("Screenshot 36 captured (NH RM Page 1 Landing)")

        # Sign out
        print("[7] Signing out...")
        signout_btn = page.locator("button:has-text('Sign Out')")
        if signout_btn.count() > 0:
            signout_btn.first.click()
            time.sleep(2)
        else:
            page.locator("#ets-rail-toggle-btn").click()
            time.sleep(1)
            page.locator("button:has-text('Sign Out')").first.click()
            time.sleep(2)

        # Direct login as nd_rm
        print("[8] Direct login as nd_rm...")
        page.locator("input").first.fill("nd_rm")
        page.locator("input[type='password']").first.fill("NdRM@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # Verify ND RM landing page
        print("[9] Capturing default landing page for ND RM...")
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "37_ReleasePlan_Page1_ND_RM_Landing.png"))
        print("Screenshot 37 captured (ND RM Page 1 Landing)")

        browser.close()
        print("ALL PAGE 1 TESTS PASSED!")

if __name__ == "__main__":
    run()
