"""Read-only diagnostic for the portal CAPTCHA preview and local OCR."""

import asyncio
from pathlib import Path

from app.crawler import InvoiceCrawler


async def main() -> None:
    data_dir = Path("/private/tmp/einvoice-login-check")
    data_dir.mkdir(parents=True, exist_ok=True)
    crawler = InvoiceCrawler(data_dir)
    try:
        preview = await crawler.login_preview()
        print(
            f"ocr_guess={preview.captcha_guess} "
            f"token_created={bool(preview.challenge_token)} "
            f"image_bytes={len(preview.screenshot)}"
        )
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
