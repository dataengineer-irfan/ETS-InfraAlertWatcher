import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = ROOT / "verification_screens"
SCREENSHOTS_DIR.mkdir(exist_ok=True, parents=True)

APP_URL = "http://localhost:8501"

def test_current_release_display():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 950})

        print("[1] Navigating to login gate...")
        page.goto(APP_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Login
        print("[2] Logging in as admin...")
        page.fill('input[aria-label="Username"]', "admin")
        page.fill('input[aria-label="Password"]', "Admin@ETS2026!")
        page.click('button:has-text("Sign In to Watchtower")')
        page.wait_for_load_state("networkidle")
        time.sleep(3)

        # 1. Verify Flight Deck on Landing Screen
        print("[3] Verifying Live State Release Flight Deck on landing...")
        content = page.content()
        assert "Current Active Releases // Multi-State Flight Deck" in content, "Flight deck header missing!"
        assert "ND.02.12.09.00" in content, "ND active release missing in flight deck!"
        assert "CUTOVER TODAY!" in content, "ND 'CUTOVER TODAY!' badge missing!"
        assert "NH.15.3" in content, "NH active release missing in flight deck!"
        assert "AK.26.09.4.X" in content, "AK active release missing in flight deck!"
        print("[+] All 3 active state releases verified on landing screen!")

        # Verify Zero-Scroll on landing screen
        scroll_pos = page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
        print(f"[*] Landing page window scroll position: {scroll_pos}")
        assert scroll_pos["y"] == 0, f"Page scrollY was {scroll_pos['y']}, expected 0 for zero-scroll compliance!"

        page.screenshot(path=str(SCREENSHOTS_DIR / "20_Live_Flight_Deck_Landing.png"))
        print("[+] Captured 20_Live_Flight_Deck_Landing.png")

        # 2. Switch to Schedule Release Plan workspace
        print("[4] Navigating to Schedule Release Plan...")
        page.click('.ets-nav-item[data-nav-idx="4"]')
        time.sleep(3)

        # Verify Zero-Scroll on release plan workspace
        rel_scroll_pos = page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
        print(f"[*] Release Plan window scroll position: {rel_scroll_pos}")
        assert rel_scroll_pos["y"] == 0, f"Page scrollY was {rel_scroll_pos['y']}, expected 0 for zero-scroll compliance!"

        # Verify that smart default auto-selected ND.02.12.09.00 (the release cutting over today)
        rel_content = page.content()
        assert "PRODUCTION CUTOVER ACTIVE TODAY // SEP 10, 2026" in rel_content, "Cutover today alert banner missing in inspector!"
        assert "ND.02.12.09.00" in rel_content, "ND.02.12.09.00 not selected in inspector!"
        assert "CUTOVER TODAY" in rel_content, "CUTOVER TODAY badge missing in master table!"
        print("[+] Smart auto-selection and live cutover alert verified!")

        page.screenshot(path=str(SCREENSHOTS_DIR / "21_ReleasePlan_Smart_Default_Cutover_Today.png"))
        print("[+] Captured 21_ReleasePlan_Smart_Default_Cutover_Today.png")

        browser.close()
        print("[SUCCESS] All Current Release date-driven verification tests passed!")

if __name__ == "__main__":
    test_current_release_display()
