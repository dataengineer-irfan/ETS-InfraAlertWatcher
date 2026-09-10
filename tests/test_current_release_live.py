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
        context = browser.new_context(viewport={"width": 1600, "height": 950})
        page = context.new_page()

        print("[1] Navigating to login gate...")
        page.goto(APP_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Login
        print("[2] Logging in as admin...")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("Admin@ETS2026!")
        page.locator("button:has-text('Sign In to Watchtower')").click()
        time.sleep(4)

        # 1. Verify Schedule Release Plan on Landing Screen (Page 1)
        print("[3] Verifying Schedule Release Plan on landing...")
        content = page.content()
        assert "Schedule Release Plan" in content, "Schedule Release Plan header missing!"
        assert "ND.02.12.09.00" in content, "ND active cutover release missing on landing!"
        assert "CUTOVER TODAY" in content, "ND 'CUTOVER TODAY' badge missing!"
        print("[+] Schedule Release Plan Page 1 verified on landing screen!")

        # Verify Zero-Scroll on landing screen
        scroll_pos = page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
        print(f"[*] Landing page window scroll position: {scroll_pos}")
        assert scroll_pos["y"] == 0, f"Page scrollY was {scroll_pos['y']}, expected 0 for zero-scroll compliance!"

        page.screenshot(path=str(SCREENSHOTS_DIR / "20_Live_Flight_Deck_Landing.png"))
        print("[+] Captured 20_Live_Flight_Deck_Landing.png")

        browser.close()
        print("[SUCCESS] All Current Release date-driven verification tests passed!")

if __name__ == "__main__":
    test_current_release_display()
