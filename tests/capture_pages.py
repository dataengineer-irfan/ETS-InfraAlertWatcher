import time
from playwright.sync_api import sync_playwright

def capture_all_pages():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        
        page.goto("http://localhost:8501", wait_until="networkidle")
        time.sleep(3)
        
        # 1. Capture Overview
        page.screenshot(path="tests/0_overview.png")
        print("Captured 0_overview.png")
        
        # Find tabs
        tabs = page.locator('div[data-testid="stTabs"] button, [data-baseweb="tab"]')
        count = tabs.count()
        print(f"Found {count} tabs")
        
        tab_names = ["Overview", "State", "Master-Detail Inspector", "Hierarchical Matrix", "Governance & Alerts"]
        for idx, name in enumerate(tab_names):
            print(f"Targeting tab: {name}")
            tab_btn = page.get_by_role("tab", name=name)
            if tab_btn.count() > 0:
                tab_btn.click()
                time.sleep(2)
                page.screenshot(path=f"tests/{idx}_{name.replace(' ', '_')}.png")
                print(f"Captured {idx}_{name.replace(' ', '_')}.png")
                
                if name == "State":
                    # Click AK, NH, ND buttons
                    for s in ["AK", "NH", "ND"]:
                        s_btn = page.get_by_role("button", name=s)
                        if s_btn.count() > 0:
                            s_btn.click()
                            time.sleep(1.5)
                            page.screenshot(path=f"tests/1_State_{s}.png")
                            print(f"Captured 1_State_{s}.png")
            else:
                print(f"Could not find tab: {name}")
            
        browser.close()

if __name__ == "__main__":
    capture_all_pages()

