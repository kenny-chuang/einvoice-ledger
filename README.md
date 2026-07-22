# E-Invoice Ledger｜發票記帳助手

把電子發票明細整理成真正能每天使用的私人記帳工具。

系統從台灣財政部電子發票資料建立消費紀錄與商品價格歷史，可部署在 Docker 或 Home Assistant OS。資料、登入工作階段與 OCR 都留在自己的設備中。

## 解決的問題

一般記帳工具常只保留發票總額，無法回答「同一件商品以前在哪裡買得比較便宜」。這個專案保留品項、數量與單價，並處理不同店家品名、錯誤資料與重複同步。

## 主要功能

- 自動同步與匯入電子發票消費明細
- 搜尋、分類、修正與還原消費資料
- 合併不同店家的同一商品名稱
- 計算最近、最低、最高與平均購買單價
- 歷史新低與目標價通知
- 本機 OCR 預填登入驗證碼
- Home Assistant Ingress、MQTT Discovery 與通知整合

![發票記帳助手總覽](einvoice_ledger/docs/screenshots/dashboard.png)

![商品歷史比價](einvoice_ledger/docs/screenshots/products.png)

## 專案文件

- [完整功能與部署說明](./einvoice_ledger/README.md)
- [安裝與資料搬移](./einvoice_ledger/SETUP.md)
- [版本紀錄](./einvoice_ledger/CHANGELOG.md)

> 這是單一使用者、私有部署的生活工具。資料庫、CSV、Cookie、登入工作階段與診斷截圖皆由 `.gitignore` 排除。

[查看 Kenny 的個人履歷網站](https://kenny-chuang-resume.fact780404.chatgpt.site/)
