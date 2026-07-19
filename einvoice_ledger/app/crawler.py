from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
from collections.abc import Awaitable, Callable
from io import BytesIO
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright


LOGIN_URL = "https://www.einvoice.nat.gov.tw/portal/btc/mobile/btc502w/detail"
LOGIN_TTL = timedelta(minutes=5)
SESSION_CHECK_TTL = timedelta(minutes=2)
ProgressCallback = Callable[[str, dict], Awaitable[None]]


class LoginRequired(RuntimeError):
    pass


class PortalChanged(RuntimeError):
    pass


@dataclass
class BrowserLoginPreview:
    screenshot: bytes
    challenge_token: str
    captcha_guess: str = ""
    security_verification: bool = False


@dataclass
class PendingLogin:
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    created_at: datetime
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_ocr = None
_ocr_lock = threading.Lock()


def recognize_captcha(image: bytes) -> str:
    """Run local OCR and return only characters accepted by the CAPTCHA field."""
    global _ocr
    from PIL import Image

    source = Image.open(BytesIO(image)).convert("RGBA")
    background = Image.new("RGBA", source.size, "white")
    background.alpha_composite(source)
    prepared = BytesIO()
    background.convert("RGB").save(prepared, format="PNG")
    with _ocr_lock:
        if _ocr is None:
            import ddddocr

            _ocr = ddddocr.DdddOcr(show_ad=False)
        result = _ocr.classification(prepared.getvalue())
    return re.sub(r"[^A-Za-z0-9]", "", str(result)).strip()


class InvoiceCrawler:
    """Owns short-lived CAPTCHA pages and the persisted authenticated session."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.state_path = data_dir / "browser-state.json"
        self.error_screenshot_path = data_dir / "crawler-error.png"
        self._pending: dict[str, PendingLogin] = {}
        self._pending_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._active: PendingLogin | None = None
        self._session_valid: bool | None = None
        self._session_checked_at: datetime | None = None
        self.headless = os.getenv("E_INVOICE_BROWSER_HEADLESS", "1").lower() not in {"0", "false", "no"}
        for pattern in ("browser-state.json", "*-debug.json", "*.png"):
            for path in self.data_dir.glob(pattern):
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass

    async def _launch_browser(self, playwright: Playwright) -> Browser:
        return await playwright.chromium.launch(headless=self.headless)

    async def _capture_error(self, page: Page, path: Path) -> None:
        """Save a viewport-only diagnostic with account-like data obscured."""
        try:
            await page.evaluate(r"""
                () => {
                  for (const element of document.querySelectorAll('input, textarea')) {
                    element.value = element.type === 'password' ? '••••••••' : '••••••';
                    element.setAttribute('value', '••••••');
                  }
                  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                  while (walker.nextNode()) {
                    walker.currentNode.nodeValue = walker.currentNode.nodeValue
                      .replace(/09\d{8}/g, '09••••••••')
                      .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '•••@•••')
                      .replace(/\/[A-Z0-9+.-]{6,}/gi, '/•••••••');
                  }
                }
            """)
            await page.screenshot(path=str(path), full_page=False)
            os.chmod(path, 0o600)
        except Exception:
            pass

    def has_session(self) -> bool:
        return self._active is not None or (self.state_path.exists() and self.state_path.stat().st_size > 0)

    def session_is_confirmed(self) -> bool:
        """Return the last verified state without starting browser I/O."""
        return self._session_valid is True

    async def _close_pending(self, pending: PendingLogin) -> None:
        try:
            await pending.context.close()
        finally:
            try:
                await pending.browser.close()
            finally:
                await pending.playwright.stop()

    async def _prune_pending(self) -> None:
        cutoff = datetime.now(UTC) - LOGIN_TTL
        expired = []
        async with self._pending_lock:
            for token, pending in list(self._pending.items()):
                if pending.created_at < cutoff:
                    expired.append(self._pending.pop(token))
        for pending in expired:
            await self._close_pending(pending)

    async def login_preview(self) -> BrowserLoginPreview:
        await self._prune_pending()
        playwright = await async_playwright().start()
        browser = await self._launch_browser(playwright)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1500)
            token = secrets.token_urlsafe(32)
            pending = PendingLogin(playwright, browser, context, page, datetime.now(UTC))
            async with self._pending_lock:
                self._pending[token] = pending
            return await self._preview_pending(token, pending)
        except Exception:
            await self._capture_error(page, self.data_dir / "login-preview-error.png")
            await context.close()
            await browser.close()
            await playwright.stop()
            raise

    async def _preview_pending(self, token: str, pending: PendingLogin) -> BrowserLoginPreview:
        async with pending.lock:
            screenshot = await pending.page.screenshot(full_page=False)
            captcha = pending.page.locator('img[alt="圖形驗證碼"]')
            captcha_guess = ""
            if await captcha.count() == 1 and await captcha.is_visible():
                try:
                    captcha_guess = await asyncio.to_thread(recognize_captcha, await captcha.screenshot())
                except Exception:
                    pass
            security = (
                await pending.page.get_by_text("Verify you are human", exact=False).count() > 0
                or await pending.page.get_by_text("Performing security verification", exact=False).count() > 0
            )
            return BrowserLoginPreview(screenshot, token, captcha_guess, security)

    async def interact(self, challenge_token: str, x: float, y: float) -> BrowserLoginPreview:
        """Forward a user click to the short-lived browser; used for manual Cloudflare verification."""
        await self._prune_pending()
        async with self._pending_lock:
            pending = self._pending.get(challenge_token)
        if pending is None:
            raise LoginRequired("登入畫面已過期，請重新載入。")
        async with pending.lock:
            await pending.page.mouse.click(x, y)
            await pending.page.wait_for_timeout(1800)
        return await self._preview_pending(challenge_token, pending)

    async def login(self, challenge_token: str, carrier_identifier: str, password: str, captcha: str) -> None:
        """Submit the exact page that produced the displayed CAPTCHA, then persist cookies."""
        await self._prune_pending()
        async with self._pending_lock:
            pending = self._pending.pop(challenge_token, None)
        if pending is None:
            raise LoginRequired("登入畫面已過期，請重新載入圖形驗證碼。")

        page = pending.page
        keep_open = False
        try:
            async with pending.lock:
                if await page.locator("#captcha").count() != 1:
                    raise LoginRequired("請先在登入畫面完成安全性驗證。")
                await page.locator("#mobile_phone").fill(carrier_identifier)
                await page.locator("#password").fill(password)
                await page.locator("#captcha").fill(captcha)
                # The button starts an async API request and then assigns
                # window.location. Do not let locator.click also wait for that
                # navigation; the loop below observes the transition directly.
                try:
                    await page.locator("#submitBtn").click(no_wait_after=True, timeout=10_000)
                except Exception as exc:
                    await self._capture_error(page, self.data_dir / "login-submit-error.png")
                    raise LoginRequired("財政部登入按鈕未能送出，請重新載入登入畫面後再試。") from exc
                # The current portal submits through JavaScript and only redirects
                # after the API response. A rejected request re-enables submitBtn.
                for _ in range(120):
                    if not await self._is_login_page(page):
                        break
                    if await page.locator("#submitBtn").is_enabled():
                        await page.wait_for_timeout(400)
                        break
                    await page.wait_for_timeout(250)
                await self._wait_for_page(page)
                if await self._is_login_page(page) or "/accounts/" in page.url:
                    try:
                        await self._capture_error(page, self.data_dir / "login-submit-error.png")
                        await self._write_debug_metadata(page)
                    except Exception:
                        pass
                    reason = await self._login_error_message(page)
                    if reason:
                        raise LoginRequired(f"財政部登入失敗：{reason}")
                    raise LoginRequired(
                        "財政部未接受登入，請確認輸入的是申請手機條碼時登記的 10 碼手機號碼、密碼與圖形驗證碼。"
                    )
                if await self._is_security_page(page):
                    raise LoginRequired("財政部要求再次完成安全性驗證，請重新載入登入畫面。")
                # The successful redirect itself proves that the SSO exchange
                # completed. Persist this exact context immediately; navigating
                # to the portal a second time here can time out and falsely turn
                # a successful login into a 502 response.
                await pending.context.storage_state(path=str(self.state_path), indexed_db=True)
                os.chmod(self.state_path, 0o600)

            previous = self._active
            self._active = pending
            self._session_valid = True
            self._session_checked_at = datetime.now(UTC)
            keep_open = True
            if previous is not None and previous is not pending:
                await self._close_pending(previous)
        finally:
            if not keep_open:
                self._session_valid = False
                self._session_checked_at = datetime.now(UTC)
                await self._close_pending(pending)

    async def _is_login_page(self, page: Page) -> bool:
        return "/accounts/login/" in page.url or await page.locator("#mobile_phone").count() > 0

    async def _is_security_page(self, page: Page) -> bool:
        return (
            await page.get_by_text("Verify you are human", exact=False).count() > 0
            or await page.get_by_text("Performing security verification", exact=False).count() > 0
        )

    @staticmethod
    async def _login_error_message(page: Page) -> str:
        """Read only the portal's visible validation/error widgets, never form values."""
        try:
            messages = await page.locator(
                '[role="alert"], .alert, .toast-body, .invalid-feedback, .swal2-html-container'
            ).evaluate_all(
                "els => els.filter(e => e.offsetParent !== null)"
                ".map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 5)"
            )
            combined = "；".join(dict.fromkeys(str(item) for item in messages))
            return re.sub(r"\s+", " ", combined)[:300]
        except Exception:
            return ""

    @staticmethod
    async def _wait_for_page(page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        await page.wait_for_timeout(800)

    async def _page_is_authenticated(self, page: Page) -> bool:
        if await self._is_login_page(page) or await self._is_security_page(page):
            return False
        body = await page.locator("body").inner_text()
        return bool(body.strip())

    def _cache_is_fresh(self) -> bool:
        return (
            self._session_valid is not None
            and self._session_checked_at is not None
            and datetime.now(UTC) - self._session_checked_at < SESSION_CHECK_TTL
        )

    async def session_is_valid(self, force: bool = False) -> bool:
        if not force and self._cache_is_fresh():
            return self._session_valid is True
        if not self.has_session():
            self._session_valid = False
            self._session_checked_at = datetime.now(UTC)
            return False

        if self._active is not None:
            active = self._active
            try:
                async with active.lock:
                    await active.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                    await self._wait_for_page(active.page)
                    valid = await self._page_is_authenticated(active.page)
            except Exception:
                valid = False
            self._session_valid = valid
            self._session_checked_at = datetime.now(UTC)
            return valid

        playwright = await async_playwright().start()
        browser = await self._launch_browser(playwright)
        try:
            context = await browser.new_context(storage_state=str(self.state_path))
            page = await context.new_page()
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            await self._wait_for_page(page)
            valid = await self._page_is_authenticated(page)
            await context.close()
            self._session_valid = valid
            self._session_checked_at = datetime.now(UTC)
            return valid
        except Exception:
            self._session_valid = False
            self._session_checked_at = datetime.now(UTC)
            return False
        finally:
            await browser.close()
            await playwright.stop()

    async def _write_debug_metadata(self, page: Page) -> None:
        """Persist selector diagnostics only; never store field values or page content."""
        try:
            data = {
                "captured_at": datetime.now(UTC).isoformat(),
                "url": urlsplit(page.url)._replace(query="", fragment="").geturl(),
                "title": await page.title(),
                "inputs": await page.locator("input").evaluate_all(
                    "els => els.map(e => ({id: e.id, name: e.name, type: e.type, placeholder: e.placeholder}))"
                ),
                "buttons": await page.locator("button, a").evaluate_all(
                    "els => els.map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 80)"
                ),
            }
            path = self.data_dir / "crawler-debug.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(path, 0o600)
        except Exception:
            pass

    @staticmethod
    def month_dates(month: str) -> tuple[str, str]:
        if len(month) != 6 or not month.isdigit():
            raise ValueError("月份必須是 YYYYMM")
        year, month_number = int(month[:4]), int(month[4:])
        last_day = monthrange(year, month_number)[1]
        return f"{year:04d}-{month_number:02d}-01", f"{year:04d}-{month_number:02d}-{last_day:02d}"

    async def _fill_month(self, page: Page, month: str) -> None:
        start, end = self.month_dates(month)
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
        if end_date > today and month == today.strftime("%Y%m"):
            end = today.isoformat()

        # The current portal uses a readonly VueDatePicker. Setting the input's
        # visible text does not update Vue's range model, so the portal rejects
        # the search with "請一併點選起日及迄日". Drive the real calendar cells.
        date_range = page.locator("#dp-input-searchInvoiceDate")
        if await date_range.count() == 1:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
            end_date = datetime.strptime(end, "%Y-%m-%d").date()
            await date_range.click()
            menu = page.locator(".dp__menu")
            try:
                await menu.wait_for(state="visible", timeout=5_000)
            except Exception as exc:
                raise PortalChanged("財政部日期元件未能開啟。") from exc

            month_selector = menu.get_by_role("button", name="月份設定", exact=True)
            year_selector = menu.get_by_role("button", name="年份設定", exact=True)
            if await month_selector.count() != 1 or await year_selector.count() != 1:
                selectors = menu.locator("button.dp__month_year_select")
                if await selectors.count() < 2:
                    raise PortalChanged("找不到財政部日期元件的月份導覽。")
                month_selector, year_selector = selectors.nth(0), selectors.nth(1)

            displayed_month = re.search(r"\d+", await month_selector.inner_text())
            displayed_year = re.search(r"\d+", await year_selector.inner_text())
            if not displayed_month or not displayed_year:
                raise PortalChanged("無法讀取財政部日期元件的目前月份。")
            displayed_index = int(displayed_year.group()) * 12 + int(displayed_month.group()) - 1
            target_index = start_date.year * 12 + start_date.month - 1
            month_delta = target_index - displayed_index
            if abs(month_delta) > 24:
                raise PortalChanged("財政部日期元件顯示的月份超出預期範圍。")
            direction = "下個月" if month_delta > 0 else "上個月"
            for _ in range(abs(month_delta)):
                await menu.get_by_role("button", name=direction, exact=True).click()

            start_cell = menu.locator(f'[id="{start}"]')
            end_cell = menu.locator(f'[id="{end}"]')
            if await start_cell.count() != 1 or await end_cell.count() != 1:
                raise PortalChanged("找不到財政部日期元件的起日或迄日。")
            await start_cell.click()
            await end_cell.click()
            await page.wait_for_timeout(200)
            selected_value = await date_range.input_value()
            selected_parts = re.findall(r"\d+", selected_value)
            if (
                len(selected_parts) < 6
                or [int(value) for value in selected_parts[:6]]
                != [
                    start_date.year,
                    start_date.month,
                    start_date.day,
                    end_date.year,
                    end_date.month,
                    end_date.day,
                ]
            ):
                raise PortalChanged("財政部日期元件未接受完整查詢日期範圍。")
            return

        month_inputs = page.locator('input[type="month"]')
        month_count = await month_inputs.count()
        if month_count == 1:
            await month_inputs.fill(f"{month[:4]}-{month[4:]}")
            return

        date_inputs = page.locator('input[type="date"]')
        date_count = await date_inputs.count()
        if date_count >= 2:
            await date_inputs.nth(0).fill(start)
            await date_inputs.nth(1).fill(end)
            return

        label_pairs = (
            ("發票日期起", "發票日期迄"), ("開始日期", "結束日期"),
            ("起始日期", "結束日期"), ("查詢起日", "查詢迄日"),
        )
        for start_label, end_label in label_pairs:
            start_input = page.get_by_label(start_label, exact=False)
            end_input = page.get_by_label(end_label, exact=False)
            if await start_input.count() == 1 and await end_input.count() == 1:
                await start_input.fill(start)
                await end_input.fill(end)
                return
        raise PortalChanged("找不到財政部頁面的月份或日期欄位。")

    @staticmethod
    async def _click_named(page: Page, names: tuple[str, ...]) -> None:
        for name in names:
            for role in ("button", "link"):
                locator = page.get_by_role(role, name=name, exact=True)
                if await locator.count() == 1 and await locator.is_visible():
                    await locator.click()
                    return
        raise PortalChanged(f"找不到操作按鈕：{'／'.join(names)}")

    async def _download_month(
        self, page: Page, month: str, progress: ProgressCallback | None = None
    ) -> bytes:
        await self._fill_month(page, month)
        if progress:
            await progress("query_month", {"month": month})
        await self._click_named(page, ("查詢", "搜尋"))
        # Search results are rendered by a background API call; the document's
        # load state is not a reliable signal. Wait for the actual export action.
        download_button = page.get_by_role("button", name="下載CSV檔", exact=True)
        try:
            if progress:
                await progress("wait_results", {"month": month})
            await download_button.wait_for(state="visible", timeout=60_000)
        except Exception as exc:
            raise PortalChanged("查詢完成後找不到「下載CSV檔」按鈕。") from exc

        # The result table defaults to ten rows. Expand it before selecting all,
        # otherwise the exported CSV only contains the first page.
        page_sizes = page.locator("#SelectSizes:visible")
        if await page_sizes.count() == 1:
            if progress:
                await progress("expand_rows", {"month": month})
            page_size = page_sizes
            await page_size.select_option("100")
            apply_size = page_size.locator("xpath=..").locator('button[title="執行"]')
            if await apply_size.count() == 1:
                await apply_size.click()
                await page.wait_for_timeout(1200)

        select_all_details = page.locator("#invoiceDetailAll")
        if await select_all_details.count() != 1:
            raise PortalChanged("找不到發票明細的全選欄位。")
        # The portal's visible label overlaps the native checkbox, so force the
        # native check action while retaining the normal input/change events.
        await select_all_details.check(force=True)
        if progress:
            await progress("select_details", {"month": month})
        async with page.expect_download(timeout=30_000) as download_info:
            if progress:
                await progress("download", {"month": month})
            await download_button.click()
        download = await download_info.value
        path = await download.path()
        if path is None:
            raise RuntimeError("財政部下載未產生檔案。")
        contents = Path(path).read_bytes()
        if not contents:
            raise RuntimeError("財政部下載的 CSV 是空檔案。")
        return contents

    async def sync_months(
        self, months: list[str], progress: ProgressCallback | None = None
    ) -> list[bytes]:
        if not self.has_session():
            raise LoginRequired("找不到有效登入工作階段，請在網頁中完成一次登入。")
        async with self._sync_lock:
            if self._active is not None:
                active = self._active
                async with active.lock:
                    page = active.page
                    if progress:
                        await progress("security_check", {})
                    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                    await self._wait_for_page(page)
                    if progress:
                        await progress("login_check", {})
                    if not await self._page_is_authenticated(page):
                        self._session_valid = False
                        self._session_checked_at = datetime.now(UTC)
                        raise LoginRequired("財政部登入工作階段已失效，請重新登入。")
                    downloads = []
                    for month in months:
                        try:
                            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                            await self._wait_for_page(page)
                            if not await self._page_is_authenticated(page):
                                raise LoginRequired("財政部登入工作階段已失效，請重新登入。")
                            downloads.append(await self._download_month(page, month, progress))
                        except LoginRequired:
                            raise
                        except Exception:
                            await self._capture_error(page, self.error_screenshot_path)
                            await self._write_debug_metadata(page)
                            raise
                    await active.context.storage_state(path=str(self.state_path), indexed_db=True)
                    os.chmod(self.state_path, 0o600)
                    self._session_valid = True
                    self._session_checked_at = datetime.now(UTC)
                    return downloads

            playwright = await async_playwright().start()
            browser = await self._launch_browser(playwright)
            try:
                context = await browser.new_context(storage_state=str(self.state_path), accept_downloads=True)
                page = await context.new_page()
                if progress:
                    await progress("security_check", {})
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                await self._wait_for_page(page)
                if progress:
                    await progress("login_check", {})
                if not await self._page_is_authenticated(page):
                    self._session_valid = False
                    self._session_checked_at = datetime.now(UTC)
                    raise LoginRequired("財政部登入工作階段已失效，請重新登入。")
                downloads = []
                for month in months:
                    try:
                        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                        await self._wait_for_page(page)
                        if not await self._page_is_authenticated(page):
                            raise LoginRequired("財政部登入工作階段已失效，請重新登入。")
                        downloads.append(await self._download_month(page, month, progress))
                    except LoginRequired:
                        raise
                    except Exception:
                        await self._capture_error(page, self.error_screenshot_path)
                        await self._write_debug_metadata(page)
                        raise
                await context.storage_state(path=str(self.state_path), indexed_db=True)
                os.chmod(self.state_path, 0o600)
                self._session_valid = True
                self._session_checked_at = datetime.now(UTC)
                await context.close()
                return downloads
            finally:
                await browser.close()
                await playwright.stop()

    async def close(self) -> None:
        async with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            await self._close_pending(item)
        active = self._active
        self._active = None
        if active is not None:
            await self._close_pending(active)
