from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    c = b.new_context(viewport={"width": 1440, "height": 900})
    page = c.new_page()
    page.goto("http://localhost:8501")
    page.wait_for_selector('input[aria-label="Username"]', timeout=15000)
    page.fill('input[aria-label="Username"]', "admin")
    page.fill('input[aria-label="Password"]', "Admin@ETS2026!")
    page.click('button:has-text("Sign In to Watchtower")')
    page.wait_for_timeout(5000)

    # Click Tab 3 (Governance)
    page.click('.ets-nav-item[data-nav-idx="3"]')
    page.wait_for_timeout(3000)

    # In Governance, find tabs
    tabs = page.query_selector_all('[role="tab"]')
    for t in tabs:
        txt = t.text_content() or ""
        if "Release Cutoff" in txt:
            t.click()
            print("Clicked Release Cutoff tab!")
            page.wait_for_timeout(2000)
            break

    page.screenshot(path=r"C:\Users\affra\.gemini\antigravity\brain\b4712539-c10c-4e29-b142-929cdda54fe0\50_Page4_ReleaseCutoffAlert_Tab.png", full_page=True)
    print("Saved 50_Page4_ReleaseCutoffAlert_Tab.png")
    b.close()
