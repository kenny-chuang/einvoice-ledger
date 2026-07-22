"""Non-sensitive smoke probe for the Patchright experiment."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlsplit

from patchright.async_api import async_playwright


PORTAL_URL = "https://www.einvoice.nat.gov.tw/portal/btc/mobile/btc502w/detail"


async def main() -> None:
    headless = os.getenv("E_INVOICE_BROWSER_HEADLESS", "0").lower() in {"1", "true", "yes"}
    async with async_playwright() as patchright:
        browser = await patchright.chromium.launch(headless=headless)
        context = await browser.new_context(locale="zh-TW")
        page = await context.new_page()
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3_000)
        storage_started = asyncio.get_running_loop().time()
        storage_state = await context.storage_state()
        storage_elapsed_ms = round(
            (asyncio.get_running_loop().time() - storage_started) * 1000
        )
        result = {
            "engine": "patchright",
            "headless": headless,
            "url_path": urlsplit(page.url).path,
            "navigator_webdriver": await page.evaluate("navigator.webdriver"),
            "cloudflare_challenge": (
                await page.get_by_text("Verify you are human", exact=False).count() > 0
                or await page.get_by_text("Performing security verification", exact=False).count() > 0
            ),
            "login_form": await page.locator("#mobile_phone").count() == 1,
            "captcha": await page.locator("#captcha").count() == 1,
            "storage_state_ok": isinstance(storage_state.get("cookies"), list),
            "storage_state_ms": storage_elapsed_ms,
        }
        print(json.dumps(result, ensure_ascii=False))
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
