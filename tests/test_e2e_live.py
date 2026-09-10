import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path
from playwright.sync_api import sync_playwright

artifacts_dir = Path(r"C:\Users\affra\.gemini\antigravity\brain\98ff7e6c-19e0-44a0-9554-4372154376cc")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.goto('http://localhost:8501', wait_until='networkidle')
    page.wait_for_timeout(1000)

    # If login gate is present, sign in with admin credentials
    gate = page.query_selector('button:has-text("Sign In to Watchtower")')
    if gate:
        page.fill('input[placeholder="Enter username (e.g. admin)"]', "admin")
        page.fill('input[placeholder="••••••••••••"]', "Admin@ETS2026!")
        gate.click()
        page.wait_for_timeout(3000)

    # Wait for page elements to render
    page.wait_for_selector('[data-testid="stSidebar"]', timeout=15000)
    page.wait_for_timeout(1500)

    # 1. Verify top tabs are completely hidden (zero duplicate headers)
    top_tabs_sel = '[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stTabs"] > div > [role="tablist"]'
    top_tabs = page.query_selector(top_tabs_sel)
    if top_tabs:
        box = top_tabs.bounding_box()
        print(f"Top tabs bounding box: {box}")
        assert box['height'] == 0 or not top_tabs.is_visible(), f"Top tabs must have 0 height! Box: {box}"
    print("✓ 1. Duplicate top tabs completely hidden (0px height)")

    # 2. Verify floating MENU badge is retired and gone
    old_menu_btn = page.query_selector('#ets-topleft-nav-btn')
    assert old_menu_btn is None or not old_menu_btn.is_visible(), "Old floating menu button must NOT be visible!"
    print("✓ 2. Old floating MENU badge retired completely (zero collision)")

    # 3. Verify permanent slim 48px icon rail docked on left edge
    sb_rect = page.evaluate("() => document.querySelector('[data-testid=\"stSidebar\"]').getBoundingClientRect()")
    main_rect = page.evaluate("() => document.querySelector('[data-testid=\"stMain\"]').getBoundingClientRect()")
    print(f"Collapsed Sidebar rect: {sb_rect}")
    print(f"Dashboard canvas rect: {main_rect}")
    assert 40 <= sb_rect['width'] <= 55, f"Sidebar should be ~48px slim rail, got {sb_rect['width']}px"
    assert main_rect['x'] >= 40, f"Dashboard canvas must sit BESIDE rail at x>=40, got x={main_rect['x']}"
    print(f"✓ 3. Permanent slim rail docked at left ({sb_rect['width']:.1f}px) with dashboard beside it at x={main_rect['x']:.1f}px")

    # Take screenshot of collapsed slim rail beside Executive Command Center
    page.screenshot(path=str(artifacts_dir / "01_Command_Center_Slim_Rail_Collapsed.png"), full_page=False)
    print("✓ 4. Saved screenshot: 01_Command_Center_Slim_Rail_Collapsed.png")

    # 4. Test clicking rail toggle button to expand
    toggle_btn = page.wait_for_selector('#ets-rail-toggle-btn', timeout=10000)
    assert toggle_btn is not None, "Toggle button #ets-rail-toggle-btn must exist!"
    page.evaluate("() => document.getElementById('ets-rail-toggle-btn').click()")
    page.wait_for_timeout(800)

    sb_rect_expanded = page.evaluate("() => document.querySelector('[data-testid=\"stSidebar\"]').getBoundingClientRect()")
    main_rect_expanded = page.evaluate("() => document.querySelector('[data-testid=\"stMain\"]').getBoundingClientRect()")
    print(f"Expanded Sidebar rect: {sb_rect_expanded}")
    print(f"Dashboard canvas expanded rect: {main_rect_expanded}")
    assert sb_rect_expanded['width'] >= 240, f"Sidebar should be expanded to ~260px, got {sb_rect_expanded['width']}px"
    assert main_rect_expanded['x'] >= 240, f"Dashboard canvas must shift BESIDE expanded rail at x>=240, got x={main_rect_expanded['x']}"
    print(f"✓ 5. Toggle button expanded rail to {sb_rect_expanded['width']:.1f}px and shifted dashboard BESIDE it to x={main_rect_expanded['x']:.1f}px")

    # Take screenshot of expanded rail beside Executive Command Center
    page.screenshot(path=str(artifacts_dir / "01_Command_Center_Slim_Rail_Expanded.png"), full_page=False)
    print("✓ 6. Saved screenshot: 01_Command_Center_Slim_Rail_Expanded.png")

    # 5. Test clicking [ ✕ ] close button
    close_btn = page.wait_for_selector('#ets-close-panel-btn', timeout=5000)
    assert close_btn is not None
    page.evaluate("() => document.getElementById('ets-close-panel-btn').click()")
    page.wait_for_timeout(800)

    sb_rect_recollapsed = page.evaluate("() => document.querySelector('[data-testid=\"stSidebar\"]').getBoundingClientRect()")
    main_rect_recollapsed = page.evaluate("() => document.querySelector('[data-testid=\"stMain\"]').getBoundingClientRect()")
    assert 40 <= sb_rect_recollapsed['width'] <= 55, f"Sidebar should return to ~48px, got {sb_rect_recollapsed['width']}px"
    assert main_rect_recollapsed['x'] >= 40, f"Dashboard canvas must return beside rail at x>=40, got x={main_rect_recollapsed['x']}"
    print(f"✓ 7. Close button returned rail to {sb_rect_recollapsed['width']:.1f}px and canvas to x={main_rect_recollapsed['x']:.1f}px")

    # 6. Test switching to Operations Hub via slim icon button
    ops_btn = page.wait_for_selector('.ets-nav-item[data-nav-idx="2"]', timeout=5000)
    page.click('.ets-nav-item[data-nav-idx="2"]')
    page.wait_for_timeout(2500)

    content = page.inner_text('[data-testid="stMainBlockContainer"]')
    has_ops = "Overview & Lineage" in content or "Batch Grid Editor" in content or "Portfolio Matrix" in content
    assert has_ops, "Failed to navigate to Operations Hub!"
    print("✓ 8. Switched to Operations Hub seamlessly")

    # 7. Verify search box has ZERO overlap/collision
    search_input = page.wait_for_selector('input[placeholder="Search schema, env..."]', timeout=5000)
    assert search_input is not None, "Operations Hub search input must exist"
    search_box = search_input.bounding_box()
    print(f"Search input bounding box: {search_box}")
    sb_box = page.evaluate("() => document.querySelector('[data-testid=\"stSidebar\"]').getBoundingClientRect()")
    print(f"Sidebar bounding box during Operations Hub: {sb_box}")
    assert search_box['x'] >= sb_box['right'], f"Search input (x={search_box['x']}) overlaps sidebar (right={sb_box['right']})!"
    print(f"✓ 9. ZERO OVERLAP VERIFIED! Search input starts at x={search_box['x']}px, sidebar ends at x={sb_box['right']}px (clearance = {search_box['x'] - sb_box['right']}px)")

    # Take screenshot of Operations Hub with slim rail and clean, un-obscured search box
    page.screenshot(path=str(artifacts_dir / "02_Operations_Hub_Slim_Rail_Clean.png"), full_page=False)
    print("✓ 10. Saved screenshot: 02_Operations_Hub_Slim_Rail_Clean.png")

    # 8. Test switching to Governance & Alerts
    gov_btn = page.wait_for_selector('.ets-nav-item[data-nav-idx="3"]', timeout=5000)
    page.click('.ets-nav-item[data-nav-idx="3"]')
    page.wait_for_timeout(2500)

    content_gov = page.inner_text('[data-testid="stMainBlockContainer"]')
    has_gov = "Governance" in content_gov or "Alerts" in content_gov or "Lineage" in content_gov or "SMTP" in content_gov
    assert has_gov, "Failed to navigate to Governance & Alerts!"
    print("✓ 11. Switched to Governance & Alerts workspace seamlessly")

    page.screenshot(path=str(artifacts_dir / "03_Governance_Alerts_Slim_Rail_Clean.png"), full_page=False)
    print("✓ 12. Saved screenshot: 03_Governance_Alerts_Slim_Rail_Clean.png")

    browser.close()
    print("\n==============================================")
    print("ALL SLIM RAIL & BESIDE TESTS PASSED 100%!")
    print("==============================================")
