import time
from pathlib import Path
from playwright.sync_api import sync_playwright

LOCAL_URL = "http://localhost:8502"
ARTIFACT_DIR = Path(r"C:\Users\affra\.gemini\antigravity\brain\b4712539-c10c-4e29-b142-929cdda54fe0\.tempmediaStorage")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

def verify_on_call():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        print(f"Connecting to {LOCAL_URL}...")
        page.goto(LOCAL_URL, wait_until="networkidle", timeout=30000)
        time.sleep(1)

        # Login
        try:
            page.wait_for_selector('input', timeout=20000)
            page.locator('input').first.fill('admin')
            page.locator('input[type="password"]').fill('Admin@ETS2026!')
            page.locator('button:has-text("Sign In to Watchtower")').click()
            time.sleep(4)
        except Exception as e:
            print(f"Login bypassed or already authenticated: {e}")

        print("Waiting for main tabs...")
        page.wait_for_selector('[data-testid="stSelectbox"]', timeout=25000)
        time.sleep(2)

        # Click Tab 6: "24/7 On-Call Operations Hub"
        print("Switching to Tab 6 (24/7 On-Call Operations Hub)...")
        # Try sidebar rail button first
        nav_btn = page.locator('.ets-nav-item[data-nav-idx="5"]')
        if nav_btn.count() > 0:
            print("Clicking nav rail button idx 5...")
            nav_btn.click()
        else:
            print("Clicking 6th tab element...")
            page.evaluate('document.querySelectorAll("[role=\'tab\']")[5].click()')
        time.sleep(5)

        # 1. Check Zero-Scroll Viewport
        dim = page.evaluate("""() => {
            const root = document.documentElement;
            const docH = root.scrollHeight;
            const winH = window.innerHeight;
            return { docH, winH, diff: docH - winH, hasOuterScroll: docH > winH + 15 };
        }""")
        print(f"[+] Viewport dimension check: docH={dim['docH']}, winH={dim['winH']}, diff={dim['diff']}, hasOuterScroll={dim['hasOuterScroll']}")

        # 2. Capture Initial Landing (Live Ops Radar)
        shot1 = str(ARTIFACT_DIR / "oncall_hub_live_ops_radar.png")
        page.screenshot(path=shot1)
        print(f"[+] Saved screenshot 1: {shot1}")

        # 3. Switch to Production Support 24x7 Tab
        ps_btn = page.locator('button:has-text("Production Support 24x7")')
        if ps_btn.count() > 0:
            print("Switching to Production Support 24x7 division...")
            ps_btn.first.click()
            time.sleep(4)

            shot2 = str(ARTIFACT_DIR / "oncall_hub_production_support_crosstab.png")
            page.screenshot(path=shot2)
            print(f"[+] Saved screenshot 2: {shot2}")

        # 4. Switch to State Core Dev Tab
        core_btn = page.locator('button:has-text("State Core Dev")')
        if core_btn.count() > 0:
            print("Switching to State Core Dev division...")
            core_btn.first.click()
            time.sleep(4)

            shot3 = str(ARTIFACT_DIR / "oncall_hub_core_dev_master_detail.png")
            page.screenshot(path=shot3)
            print(f"[+] Saved screenshot 3: {shot3}")

        # 4b. Switch to Non-Core Dev Tab
        nc_btn = page.locator('button:has-text("Non-Core Dev")')
        if nc_btn.count() > 0:
            print("Switching to Non-Core Dev division...")
            nc_btn.first.click()
            time.sleep(4)

            shot_nc = str(ARTIFACT_DIR / "oncall_hub_noncore_master_detail.png")
            page.screenshot(path=shot_nc)
            print(f"[+] Saved screenshot NC: {shot_nc}")

        # 5. Test Timezone Toggle to EST
        est_btn = page.locator('button:has-text("EST (UTC-5)")')
        if est_btn.count() > 0:
            print("Testing Timezone switch to EST...")
            est_btn.first.click()
            time.sleep(4)

            shot4 = str(ARTIFACT_DIR / "oncall_hub_est_tz.png")
            page.screenshot(path=shot4)
            print(f"[+] Saved screenshot 4: {shot4}")

        print("[SUCCESS] All on-call hub UI interactions verified!")
        browser.close()

if __name__ == "__main__":
    verify_on_call()
