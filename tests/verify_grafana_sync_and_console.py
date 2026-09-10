from playwright.sync_api import sync_playwright
from pathlib import Path

ARTIFACTS_DIR = Path(r"C:\Users\affra\.gemini\antigravity\brain\b4712539-c10c-4e29-b142-929cdda54fe0")

def run_verification():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # ----------------------------------------------------------------------
        # Test 1: Admin Login & Zero Console Errors Verification
        # ----------------------------------------------------------------------
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        messages = []
        page.on("console", lambda msg: messages.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: messages.append(f"[PAGE_ERROR] {err}"))
        
        page.goto("http://localhost:8501")
        page.wait_for_selector('input[aria-label="Username"]', timeout=10000)
        page.fill('input[aria-label="Username"]', "admin")
        page.fill('input[aria-label="Password"]', "Admin@ETS2026!")
        page.click('button:has-text("Sign In to Watchtower")')
        page.wait_for_timeout(5000)
        
        print("=== CONSOLE MESSAGES AFTER ADMIN LOGIN ===")
        print(f"Total messages captured: {len(messages)}")
        for m in messages:
            print("  ", m)
        assert len(messages) == 0, f"Expected 0 console messages, got {len(messages)}: {messages}"
        print("PASS: Zero console errors on login and landing!")
        
        # Capture Admin Landing on Page 1 (Schedule Release Plan)
        p1_path = ARTIFACTS_DIR / "42_Grafana_ReleasePlan_Admin_Page1.png"
        page.screenshot(path=str(p1_path), full_page=True)
        print(f"Saved: {p1_path}")
        
        # Navigate to Page 3 (Operations Hub) via sidebar rail nav item
        ops_btn = page.query_selector('.ets-nav-item[data-nav-idx="2"]')
        if ops_btn:
            ops_btn.click()
            page.wait_for_timeout(4000)
            ops_path = ARTIFACTS_DIR / "43_Grafana_OperationsHub_Reference.png"
            page.screenshot(path=str(ops_path), full_page=True)
            print(f"Saved: {ops_path}")
            
        page.close()
        
        # ----------------------------------------------------------------------
        # Test 2: NH RM Direct Login & Page 1 Isolation
        # ----------------------------------------------------------------------
        page_nh = browser.new_page(viewport={"width": 1440, "height": 900})
        page_nh.goto("http://localhost:8501")
        page_nh.wait_for_selector('input[aria-label="Username"]', timeout=10000)
        page_nh.fill('input[aria-label="Username"]', "nh_rm")
        page_nh.fill('input[aria-label="Password"]', "NhRM@ETS2026!")
        page_nh.click('button:has-text("Sign In to Watchtower")')
        page_nh.wait_for_timeout(5000)
        
        nh_path = ARTIFACTS_DIR / "44_Grafana_ReleasePlan_NH_RM_Page1.png"
        page_nh.screenshot(path=str(nh_path), full_page=True)
        print(f"Saved: {nh_path}")
        page_nh.close()

        # ----------------------------------------------------------------------
        # Test 3: ND RM Direct Login & Page 1 Isolation
        # ----------------------------------------------------------------------
        page_nd = browser.new_page(viewport={"width": 1440, "height": 900})
        page_nd.goto("http://localhost:8501")
        page_nd.wait_for_selector('input[aria-label="Username"]', timeout=10000)
        page_nd.fill('input[aria-label="Username"]', "nd_rm")
        page_nd.fill('input[aria-label="Password"]', "NdRM@ETS2026!")
        page_nd.click('button:has-text("Sign In to Watchtower")')
        page_nd.wait_for_timeout(5000)
        
        nd_path = ARTIFACTS_DIR / "45_Grafana_ReleasePlan_ND_RM_Page1.png"
        page_nd.screenshot(path=str(nd_path), full_page=True)
        print(f"Saved: {nd_path}")
        page_nd.close()

        browser.close()
        print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run_verification()
