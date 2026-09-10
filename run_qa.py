import asyncio
from playwright.async_api import async_playwright

async def run_qa():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        viewports = [
            {"name": "laptop_1366", "width": 1366, "height": 768},
            {"name": "desktop_1080p", "width": 1920, "height": 1080},
            {"name": "ultrawide_1440p", "width": 2560, "height": 1440}
        ]
        
        for vp in viewports:
            print(f"Testing viewport {vp['name']}...")
            page = await browser.new_page(viewport={"width": vp["width"], "height": vp["height"]})
            await page.goto("http://localhost:8501")
            
            # Wait for Streamlit to render the elements
            await page.wait_for_timeout(5000)
            
            await page.screenshot(path=f"screenshot_{vp['name']}_page1.png", full_page=True)
            
            # Try to click on the second page in the sidebar
            # Note: Streamlit sidebar navigation is based on elements
            try:
                await page.click("text=Governance & Dispatch")
                await page.wait_for_timeout(3000)
                await page.screenshot(path=f"screenshot_{vp['name']}_page4.png", full_page=True)
            except Exception as e:
                print("Could not navigate to Governance:", e)
                
            await page.close()
            
        await browser.close()

asyncio.run(run_qa())
