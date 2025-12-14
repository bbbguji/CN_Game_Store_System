# 🎮 Online Game Store & Lobby System (多人遊戲大廳與商城系統)

**Network Programming Final Project**

這是一個基於 Python Socket (TCP) 實作的完整多人遊戲生態系統。系統包含三個主要組件：負責邏輯與資料管理的 **Server**、供遊戲創作者上架作品的 **Developer Client**，以及供玩家下載與連線遊玩的 **Player Client**。

本專案展示了非阻塞式 I/O (Non-blocking I/O)、多執行緒 (Multithreading)、子行程管理 (Subprocess) 以及動態插件載入 (Dynamic Plugin Loading) 等進階網路程式設計技巧。

---

## 🏆 功能清單與完成度 (Use Case Mapping)

本系統已完整實作以下功能規格：

### 👨‍💻 開發者端 (Developer)
- [x] **D1 上架新遊戲**: 支援填寫遊戲資訊 (人數、類型) 並上傳打包好的遊戲檔案。
- [x] **D2 更新遊戲**: 支援對已上架的遊戲推送版本更新 (Server 自動處理版號)。
- [x] **D3 下架遊戲**: 支援將遊戲從商城移除 (具備安全檢查：若有活躍房間則禁止下架)。
- [x] **模板生成**: 內建 4 種遊戲模板 (RPS, TicTacToe, Gomoku, Snake) 供快速測試。

### 🕹️ 玩家端 (Player)
- [x] **P1 瀏覽商城**: 查看遊戲列表、詳細資訊、評分與評論。
- [x] **P2 下載遊戲**: 支援斷點續傳機制的檔案下載與完整性驗證 (Checksum)。
- [x] **P3 遊戲大廳**: 建立房間、加入房間、即時狀態同步。
- [x] **P4 評分系統**: 僅限已安裝並遊玩過的玩家進行評分。
- [x] **PL 插件系統**: 
    - 支援動態安裝/移除插件 (如：聊天室)。
    - **使用者隔離**: 同一台電腦不同帳號的插件互不干擾。
    - **Thread-Safe GUI**: 解決了 GUI 與網路執行緒衝突導致的死鎖問題。

### ⚙️ 系統核心
- [x] **多人連線**: 支援 2-4 人以上的同步遊戲 (如貪食蛇)。
- [x] **Ready Check**: 啟動遊戲前自動檢查所有玩家版本是否一致。
- [x] **跨平台支援**: 自動偵測 Windows/Linux/macOS 調整啟動參數。

---

## 📂 專案結構

```text
Project_Root/
├── common/
│   └── utils.py           # 通用協定定義 (Protocol Constants) 與 封包收發函式
├── server/
│   ├── server_main.py     # [核心] 伺服器主程式 (Socket Server, Process Manager)
│   ├── data/              # [資料庫] 儲存 users.json (帳戶) 與 games_meta.json (遊戲資訊)
│   └── uploaded_games/    # [儲存區] 存放開發者上傳的遊戲壓縮檔
├── developer/
│   ├── developer_client.py # [前端] 開發者工具 (含遊戲模板產生器)
│   └── dev_workspace/      # [工作區] 開發者本地的遊戲專案目錄
├── player/
│   ├── lobby_client.py     # [前端] 玩家大廳與商城介面
│   ├── downloads/          # [安裝區] 玩家下載的遊戲存放處
│   └── plugins/            # [擴充區] 已安裝的 Python 插件 (如聊天室)
└── README.md

---

## 執行方式

### 啟動伺服器
cd server
python server_main.py

### 開發者端
cd developer
python developer_client.py

### 玩家端
cd player
python lobby_client.py