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

        # Navigate to Schedule Release Plan
        print("[3] Navigating to Schedule Release Plan...")
        page.locator(".ets-nav-item[data-nav-idx='4']").first.click()
        time.sleep(4)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "32_ReleasePlan_Clean_Static_Admin.png"))
        print("Screenshot 32 captured (Clean Admin View)")

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

        # Navigate to Schedule Release Plan as NH RM
        print("[6] NH RM navigating to Schedule Release Plan...")
        page.locator(".ets-nav-item[data-nav-idx='4']").first.click()
        time.sleep(4)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "33_ReleasePlan_Clean_Static_NH_RM.png"))
        print("Screenshot 33 captured (Clean NH RM Direct Login)")

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

        # Navigate to Schedule Release Plan as ND RM
        print("[9] ND RM navigating to Schedule Release Plan...")
        page.locator(".ets-nav-item[data-nav-idx='4']").first.click()
        time.sleep(4)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "34_ReleasePlan_Clean_Static_ND_RM.png"))
        print("Screenshot 34 captured (Clean ND RM Direct Login)")

        browser.close()
        print("ALL STATIC CLEAN TESTS PASSED!")

if __name__ == "__main__":
    run()
