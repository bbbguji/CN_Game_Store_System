# 🎮 Online Game Store & Lobby System  
**多人遊戲大廳與商城系統（Network Programming Final Project）**

---

## 📌 專案簡介
本專案是一個基於 **Python Socket (TCP)** 的完整多人遊戲生態系統，包含三個主要組件：

- **Server**：負責連線管理、遊戲協調與資料維護  
- **Developer Client**：供開發者上架、更新與管理遊戲  
- **Player Client**：供玩家瀏覽商城、下載遊戲並加入多人房間  

專案涵蓋以下網路程式設計重點：
- Non-blocking I/O  
- Multithreading  
- Subprocess 管理  
- Dynamic Plugin Loading  

---

## 🏆 功能清單與完成度（Use Case Mapping）

### 👨‍💻 Developer（開發者端）
- [x] **D1 上架新遊戲**：填寫遊戲資訊並上傳壓縮檔  
- [x] **D2 更新遊戲**：伺服器自動管理版本號  
- [x] **D3 下架遊戲**：若仍有活躍房間則禁止下架  
- [x] **模板生成**：RPS、TicTacToe、Gomoku、Snake  

### 🕹️ Player（玩家端）
- [x] **P1 瀏覽商城**：遊戲列表、詳細資訊、評分  
- [x] **P2 下載遊戲**：支援斷點續傳與 Checksum 驗證  
- [x] **P3 遊戲大廳**：建立 / 加入房間、即時同步  
- [x] **P4 評分系統**：僅限實際遊玩過的玩家  
- [x] **PL 插件系統**
  - 動態安裝 / 移除插件（如聊天室）
  - 使用者隔離（不同帳號互不干擾）
  - Thread-safe GUI 設計

### ⚙️ 系統核心
- [x] 多人同步連線（2–4 人以上）  
- [x] Ready Check（版本一致性檢查）  
- [x] Windows / Linux / macOS 跨平台支援  

---

## 📂 專案結構

```text
Project_Root/
├── common/
│   └── utils.py
├── server/
│   ├── server_main.py
│   ├── data/
│   └── uploaded_games/
├── developer/
│   ├── developer_client.py
│   └── dev_workspace/
├── player/
│   ├── lobby_client.py
│   ├── downloads/
│   └── plugins/
└── README.md
```

---

## 🚀 快速開始（Quick Start）

### 環境需求
- Python **3.8+**
- 建議使用 `venv` 虛擬環境

### 1️⃣ 啟動 Server
```bash
cd server
python server_main.py
```
啟動後輸入 Port（預設 12365），Server 會綁定 `0.0.0.0`。

### 2️⃣ 啟動 Developer Client
```bash
cd developer
python developer_client.py
```
登入後可使用 **Create Template** 產生範例遊戲。

### 3️⃣ 啟動 Player Client
```bash
cd player
python lobby_client.py
```
支援多開視窗以模擬多人連線。

---

## 📖 使用者指南（User Guide）

### 玩家流程
1. **註冊 / 登入**
2. **下載遊戲**
   - Store → 選擇遊戲 → Download
3. **開始遊玩**
   - Main Menu → Play (Rooms)
   - Create / Join 房間
   - 人數達標後由房主啟動遊戲
4. **版本檢查**
   - 若版本不一致則禁止啟動

### 聊天室插件（Bonus）
- Plugins → Install RoomChat  
- 進入房間後自動開啟聊天室視窗  

---

## 🛠️ 開發者指南（Developer Guide）

### 1️⃣ 遊戲架構規範
- Client–Server 架構  
- Server **不可寫死 Port**（需由參數指定）  
- Client 需能接收 IP / Port 參數  

### 2️⃣ `manifest.json` 規範
```json
{
  "name": "MyAwesomeGame",
  "version": "1.0",
  "description": "這是一個 2 人對戰的射擊遊戲",
  "type": "GUI",
  "min_players": 2,
  "max_players": 4,
  "execution": {
    "server_cmd": ["python", "server.py"],
    "client_cmd": ["python", "client.py"],
    "args_format": {
      "connect_ip": "--ip",
      "connect_port": "--port"
    }
  }
}
```

### 3️⃣ 程式碼整合範例

**Game Server (`server.py`)**
```python
import argparse
import socket

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("0.0.0.0", args.port))
server.listen()
print(f"Game Server listening on {args.port}")
```

**Game Client (`client.py`)**
```python
import argparse
import socket

parser = argparse.ArgumentParser()
parser.add_argument("--ip", type=str, required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect((args.ip, args.port))
```

### 4️⃣ 開發流程
1. 啟動 `developer_client.py`
2. Generate Template
3. 修改程式與 `manifest.json`
4. Upload New Game
