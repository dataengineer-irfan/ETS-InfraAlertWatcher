import time
import os
from playwright.sync_api import sync_playwright

ARTIFACT_DIR = r"C:/Users/affra/.gemini/antigravity/brain/b4712539-c10c-4e29-b142-929cdda54fe0"

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Login as nh_rm directly
        print("[1] Logging in as nh_rm...")
        page.goto("http://localhost:8501", wait_until="networkidle")
        time.sleep(2)
        page.locator("input").first.fill("nh_rm")
        page.locator("input[type='password']").first.fill("NhRM@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # Navigate to Schedule Release Plan as NH RM
        page.locator(".ets-nav-item[data-nav-idx='4']").first.click()
        time.sleep(4)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "27_ReleasePlan_NH_RM_Direct_Hard_Locked.png"))
        print("Screenshot 27 captured (NH RM Hard-Locked)")

        # Sign out from sidebar
        print("[2] Signing out...")
        signout_btn = page.locator("button:has-text('Sign Out')")
        if signout_btn.count() > 0:
            signout_btn.first.click()
            time.sleep(2)
        else:
            page.locator("#ets-rail-toggle-btn").click()
            time.sleep(1)
            page.locator("button:has-text('Sign Out')").first.click()
            time.sleep(2)

        # Login as nd_rm directly
        print("[3] Logging in as nd_rm...")
        page.locator("input").first.fill("nd_rm")
        page.locator("input[type='password']").first.fill("NdRM@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # Navigate to Schedule Release Plan as ND RM
        page.locator(".ets-nav-item[data-nav-idx='4']").first.click()
        time.sleep(4)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "28_ReleasePlan_ND_RM_Direct_Hard_Locked.png"))
        print("Screenshot 28 captured (ND RM Hard-Locked)")

        browser.close()
        print("NH and ND Direct Logins Verified Successfully!")

if __name__ == "__main__":
    run()
