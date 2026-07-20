# 發票記帳助手安裝指南

本指南適用於從 GitHub 下載全新專案。Git repository 不包含發票資料、財政部登入工作階段、Cookie、資料庫備份或真實設定。

## 最快安裝方式：Docker

### 需求

- Git
- Docker Desktop，或支援 Docker Compose 的 Linux 主機
- 至少 2 GB 可用記憶體
- 第一次建置時可連線至網際網路以下載映像與套件

### 1. 下載專案

```sh
git clone <repository-url>
cd <repository-directory>/einvoice_ledger
```

### 2. 建立本機設定

```sh
cp .env.example .env
```

預設會監聽 `0.0.0.0:8000`，讓同一個可信任區網內的手機可以連線。若只想從本機使用，將 `.env` 改成：

```dotenv
EINVOICE_BIND_ADDRESS=127.0.0.1
```

不要把真實 MQTT 密碼或其他憑證寫入 `.env.example`，也不要提交 `.env`。

### 3. 啟動

```sh
docker compose up -d --build
docker compose ps
```

容器顯示 `healthy` 後開啟：

```text
http://127.0.0.1:8000/#/dashboard
```

區網手機請使用主機的區網 IP：

```text
http://<主機區網IP>:8000/#/dashboard
```

系統沒有公網登入防護。只可用於可信任的家庭區網，請勿在路由器設定 Port Forwarding。

## 第一次使用

啟動後資料庫會自動建立。接著選擇其中一種方式加入資料：

1. 在總覽上傳財政部下載的消費明細 CSV。
2. 前往「設定 → 財政部登入續期」，完成登入後執行立即同步。

財政部密碼只存在該次登入請求的記憶體中；Cookie 工作階段保存在 `data/browser-state.json`，不會進入 Git。

## 停止與更新

停止服務：

```sh
docker compose down
```

取得新版並重新建置：

```sh
git pull
docker compose up -d --build
```

`docker compose down` 不會刪除 `data/`。不要使用 `docker compose down -v`，除非已確認資料備份完成。

## 搬移既有資料

資料庫位於：

```text
einvoice_ledger/data/einvoice.db
```

搬移前先停止兩邊的服務，再透過私人方式複製資料庫。不要透過 GitHub、Email 或公開雲端連結傳送。

建議在新電腦重新完成財政部登入，不要搬移 `browser-state.json`。

## 驗證安裝

```sh
curl http://127.0.0.1:8000/api/health
```

空白系統第一次回應沒有同步紀錄是正常現象。若容器無法啟動：

```sh
docker compose logs --tail=200 einvoice-ledger
```

## 不使用 Docker

專案也能使用 Conda 執行。完整指令請參考 [README 的本機開發章節](README.md#本機開發)。手機本身不適合執行 Playwright、Chromium及背景同步；建議讓 Mac、Linux、NAS 或 Home Assistant 常駐執行後端。
