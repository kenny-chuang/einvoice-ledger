# Patchright 實驗

此分支只驗證 Patchright 是否能降低財政部網站對 Docker 瀏覽器的自動化判定，不修改資料庫、CSV 匯入、商品、分類或 Vue 操作流程。

## 變更範圍

- `playwright` 改為 `patchright`，維持相同 async API。
- Chromium 在 Docker 的 Xvfb 虛擬顯示器中以 headful 模式執行。
- 不加入 noVNC、遠端桌面、自動點擊 Turnstile 或第三方 CAPTCHA 服務。
- 手機號碼、密碼與 ddddocr 圖形驗證碼仍使用原本的登入表單。

## 驗證

```bash
docker compose exec -T einvoice-ledger python scripts/patchright_probe.py
```

通過條件：`navigator_webdriver` 不是 `true`，且能到達財政部登入表單或既有登入後的查詢頁。若仍出現 `cloudflare_challenge: true`，則 Patchright 在目前 Docker／網路環境下不足以解決問題。

若 Docker Desktop 無法解析財政部網域，可用 `.env` 的
`EINVOICE_PORTAL_IP` 覆寫 `docker-compose.yml` 內的 DNS fallback；該值是
Cloudflare 節點位址，不能視為永久不變的官方固定 IP。
