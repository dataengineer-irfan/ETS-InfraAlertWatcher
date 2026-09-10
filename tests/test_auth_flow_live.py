import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import pathlib
import time
from playwright.sync_api import sync_playwright

def main():
    artifacts_dir = pathlib.Path(r"C:\Users\affra\.gemini\antigravity\brain\b4712539-c10c-4e29-b142-929cdda54fe0\verification_screens")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("1. Loading http://localhost:8501 ...")
        page.goto("http://localhost:8501", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        # Step 1: Verify Login Gate is active
        content_gate = page.inner_text('[data-testid="stAppViewContainer"]')
        assert "ENTERPRISE ACCESS PORTAL" in content_gate, "Login gate header not found!"
        assert "Sign In to Watchtower" in content_gate, "Sign In button not found!"
        assert "Executive Operations & Accountability Deck" not in content_gate, "Protected content must NOT be visible!"
        print("✓ Step 1: Login gate active, protected data hidden.")

        shot1 = str(artifacts_dir / "01_Auth_Login_Gate.png")
        page.screenshot(path=shot1, full_page=False)
        print(f"✓ Screenshot 1 saved: {shot1}")

        # Step 2: Attempt invalid login
        print("2. Testing invalid password rejection...")
        u_input = page.wait_for_selector('input[placeholder="Enter username (e.g. admin)"]', timeout=5000)
        u_input.fill("admin")

        p_input = page.wait_for_selector('input[placeholder="••••••••••••"]', timeout=5000)
        p_input.fill("WrongPassword999!")

        submit_btn = page.wait_for_selector('button:has-text("Sign In to Watchtower")', timeout=5000)
        submit_btn.click()
        page.wait_for_timeout(2000)

        content_err = page.inner_text('[data-testid="stAppViewContainer"]')
        assert "Authentication failed" in content_err, "Error alert not displayed on bad credentials!"
        print("✓ Step 2: Bad password rejected with security alert.")

        shot2 = str(artifacts_dir / "02_Auth_Invalid_Credentials.png")
        page.screenshot(path=shot2, full_page=False)
        print(f"✓ Screenshot 2 saved: {shot2}")

        # Step 3: Enter authentic admin credentials
        print("3. Signing in with authentic admin credentials...")
        u_input = page.wait_for_selector('input[placeholder="Enter username (e.g. admin)"]', timeout=5000)
        u_input.fill("admin")
        p_input = page.wait_for_selector('input[placeholder="••••••••••••"]', timeout=5000)
        p_input.fill("Admin@ETS2026!")
        submit_btn = page.wait_for_selector('button:has-text("Sign In to Watchtower")', timeout=5000)
        submit_btn.click()
        page.wait_for_timeout(5000)

        # Step 4: Verify authenticated session
        page.screenshot(path=str(artifacts_dir / "03_Auth_LoggedIn_Dashboard.png"), full_page=False)
        content_dash = page.inner_text('[data-testid="stAppViewContainer"]')
        assert "signed in as:" in content_dash.lower() or "active session" in content_dash.lower(), f"Session user header not found! Snippet: {content_dash[:200]}"
        assert "admin" in content_dash, "Active user admin not displayed!"
        assert "Log Out" in content_dash or "Sign Out" in content_dash, "Log Out button missing!"
        print("✓ Step 3: Successfully authenticated! Command Center and user session active.")

        shot3 = str(artifacts_dir / "03_Auth_LoggedIn_Dashboard.png")
        page.screenshot(path=shot3, full_page=False)
        print(f"✓ Screenshot 3 saved: {shot3}")

        # Step 5: Test Log Out action
        print("4. Testing Log Out button...")
        logout_btn = page.wait_for_selector('button:has-text("Log Out")', timeout=5000)
        logout_btn.click()
        page.wait_for_timeout(3000)

        content_after = page.inner_text('[data-testid="stAppViewContainer"]')
        assert "ENTERPRISE ACCESS PORTAL" in content_after, "Failed to return to Sign In portal after logout!"
        assert "Executive Operations & Accountability Deck" not in content_after, "Protected content still visible after logout!"
        print("✓ Step 4: Log Out successfully purged session state and returned to Sign In gate.")

        shot4 = str(artifacts_dir / "04_Auth_LoggedOut_Return.png")
        page.screenshot(path=shot4, full_page=False)
        print(f"✓ Screenshot 4 saved: {shot4}")

        browser.close()
        print("\n==============================================")
        print("ALL AUTHENTICATION & LOGOUT TESTS PASSED 100%!")
        print("==============================================")

if __name__ == "__main__":
    main()
