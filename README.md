# Side Projects

這個 repository 用來收錄彼此獨立的小型專案。每個專案的程式、環境、文件與執行資料都放在自己的子目錄中。

## 發票記帳助手 E-Invoice Ledger

以台灣財政部電子發票消費明細為資料來源的私有記帳 Web App，可在 Docker 或 Home Assistant OS 上執行。系統自動整理消費歷史、統一不同商店的商品名稱，並用實際購買單價建立個人歷史比價資料。

### 主要功能

- **本機 OCR 驗證碼預填**：財政部登入續期時，由 `ddddocr` 在自己的設備上辨識圖形驗證碼並自動填入欄位；圖片不會傳到第三方，使用者核對後才送出登入。
- **完整消費紀錄歷史**：可依月份、分類及關鍵字搜尋發票品項，查看日期、發票、商品、數量、單價與金額；財政部 CSV 有誤時可逐筆人工修正並隨時還原。
- **自動計算商品歷史最低價**：匯入或同步後，自動依統一後的商品名稱彙整購買次數、最近單價、歷史最低／最高／平均單價、商店比較與價格趨勢。
- **歷史新低與目標價提醒**：商品低於過往最低單價或指定目標價時建立通知事件，重複同步不會重複提醒。
- **自動同步與清洗**：每日同步當月與上月，處理重複匯入、作廢發票、折扣列及常見 CSV 欄位錯位。
- **Home Assistant 整合**：支援 Ingress、MQTT Discovery、Dashboard 與同步／價格通知。

> 商品比價採用財政部 CSV 的「消費明細單價」；若該筆資料經使用者人工修正，則採用修正後單價。不使用發票總額、每 100g／100ml 換算或未分攤折扣推算價格。

![發票記帳助手總覽](einvoice_ledger/docs/screenshots/dashboard.png)

![商品歷史比價](einvoice_ledger/docs/screenshots/products.png)

## 專案文件

- [完整功能、架構與部署說明](./einvoice_ledger/README.md)
- [新電腦安裝與資料搬移](./einvoice_ledger/SETUP.md)
- [版本紀錄](./einvoice_ledger/CHANGELOG.md)

`einvoice_ledger/` 是「發票記帳助手」的完整專案根目錄；安裝、開發與部署指令請在該目錄執行。資料庫、CSV、Cookie、登入工作階段及診斷截圖皆由 `.gitignore` 排除，不應提交到公開 repository。
