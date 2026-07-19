import asyncio
from io import BytesIO

import pytest
from PIL import Image

from app import crawler as crawler_module
from app.crawler import InvoiceCrawler, LoginRequired, recognize_captcha


class FakeOcr:
    def classification(self, _: bytes) -> str:
        return " A-1_b 2! "


def test_month_dates_cover_leap_year_and_reject_invalid_input():
    assert InvoiceCrawler.month_dates("202602") == ("2026-02-01", "2026-02-28")
    assert InvoiceCrawler.month_dates("202402") == ("2024-02-01", "2024-02-29")
    with pytest.raises(ValueError):
        InvoiceCrawler.month_dates("2026-02")
    with pytest.raises(ValueError):
        InvoiceCrawler.month_dates("202613")


def test_captcha_ocr_result_is_sanitized(monkeypatch):
    monkeypatch.setattr(crawler_module, "_ocr", FakeOcr())
    image = BytesIO()
    Image.new("RGB", (20, 10), "white").save(image, format="PNG")
    assert recognize_captcha(image.getvalue()) == "A1b2"


def test_expired_or_unknown_login_challenge_is_rejected_without_browser(tmp_path):
    crawler = InvoiceCrawler(tmp_path)
    with pytest.raises(LoginRequired, match="已過期"):
        asyncio.run(crawler.login("missing", "account", "password", "1234"))
