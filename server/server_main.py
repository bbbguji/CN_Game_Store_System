import socket
import select
import queue
import sys
import os
import json
import shutil
import subprocess
import time
import traceback
import threading
import atexit
import signal

# 嘗試引用 utils，若失敗則使用下方的 Fallback 定義
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
try:
    from common.utils import *
except ImportError:
    # 避免本地測試找不到 utils 報錯
    MSG_LOGIN_REQ = 1; MSG_LOGIN_RESP = 2; MSG_REGISTER_REQ = 3; MSG_REGISTER_RESP = 4
    MSG_GAME_UPLOAD_INIT = 10; MSG_GAME_UPLOAD_DATA = 11; MSG_GAME_UPLOAD_END = 12
    MSG_GAME_LIST_REQ = 20; MSG_GAME_LIST_RESP = 21; MSG_GAME_DOWNLOAD_REQ = 22
    MSG_GAME_DOWNLOAD_INIT = 23; MSG_GAME_DOWNLOAD_DATA = 24; MSG_GAME_DOWNLOAD_END = 25
    MSG_ROOM_CREATE_REQ = 30; MSG_ROOM_CREATE_RESP = 31; MSG_ROOM_LIST_REQ = 32
    MSG_ROOM_LIST_RESP = 33; MSG_ROOM_JOIN_REQ = 34; MSG_ROOM_JOIN_RESP = 35
    MSG_ROOM_LEAVE_REQ = 36; MSG_ROOM_STATUS_UPDATE = 37
    MSG_GAME_START_CMD = 38; MSG_GAME_LAUNCH_EVENT = 39

    def recv_packet(s): return None, None
    def send_packet(s, t, p): pass
    def calculate_checksum(f): return "dummy"

# ==========================================
#  Global Configurations & Constants
# ==========================================
SERVER_IP = '140.113.17.11'        # 部署時的 Public IP
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
USERS_DB = os.path.join(DATA_DIR, 'users.json')
GAMES_META_DB = os.path.join(DATA_DIR, 'games_meta.json')
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploaded_games')

# ==========================================
#  Helper Functions
# ==========================================
def find_free_port():
    """尋找一個可用的 Ephemeral Port"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('0.0.0.0', 0))
            return s.getsockname()[1]
    except Exception:
        return 0

# ==========================================
#  Main Server Class
# ==========================================
class GameStoreServer:
    def __init__(self):
        self.server_socket = None
        self.inputs = []
        self.outputs = []
        self.message_queues = {}
        
        # 資料庫載入
        # 結構: {"player": {"u1": "pwd1"}, "developer": {"d1": "pwd2"}}
        self.users = self.load_users_db()
        self.games_meta = self.load_json(GAMES_META_DB)
        
        # 連線與狀態管理
        self.socket_map = {}       # {socket: {"username":..., "role":...}}
        # 用於快速查詢該帳號是否已登入，以實作「踢除舊連線」
        self.active_sessions = {}  # {(role, username): socket} - 用於防止重複登入
        self.rooms = {}            # {room_id: room_info}
        self.next_room_id = 1
        
        # 上傳與遊戲執行狀態
        self.upload_states = {}    # 處理大檔案分塊上傳
        self.running_games = {}    # {room_id: subprocess}
        self.thread_results = queue.Queue()

        # 註冊資源清理
        atexit.register(self.cleanup_server)
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    # -------------------------------------------------
    #  Core: Networking & Loop
    # -------------------------------------------------
    def start(self):
        """啟動伺服器主迴圈"""
        if not os.path.exists(UPLOAD_DIR): os.makedirs(UPLOAD_DIR)
        
        # Port 配置 (允許動態輸入以避免衝突)
        while True:
            try:
                port_input = input(f"Enter Server Port (Default 12365): ")
                port = int(port_input) if port_input else 12365
                
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.server_socket.bind(('0.0.0.0', port)) # Bind to all interfaces
                self.server_socket.listen(10)
                self.server_socket.setblocking(False)
                self.inputs = [self.server_socket]
                print(f"[*] Server running on {SERVER_IP}:{port}")
                break
            except ValueError:
                print("[!] Invalid port number.")
            except OSError as e:
                print(f"[!] Port {port} is busy or unavailable ({e}). Please try another.")
                try: self.server_socket.close()
                except: pass
        
        # Main Select Loop(還有socket在監聽就繼續)
        while self.inputs:
            try:
                readable, writable, exceptional = select.select(self.inputs, self.outputs, self.inputs, 0.1)
                
                # 傳入資料的socket處理
                for s in readable:
                    if s is self.server_socket:
                        # 有新連線進來
                        try:
                            conn, addr = s.accept()
                            conn.setblocking(False)
                            self.inputs.append(conn)
                            self.message_queues[conn] = queue.Queue()
                            print(f"[+] New connection from {addr}")
                        except Exception as e:
                            print(f"[!] Accept failed: {e}")
                    # client socket 有資料可讀取
                    else:
                        try:
                            msg_type, payload = recv_packet(s)
                            if msg_type is not None:
                                self.handle_packet(s, msg_type, payload)
                            else:
                                self.handle_disconnect(s)
                        except Exception as e:
                            print(f"[!] Error processing packet: {e}")
                            self.handle_disconnect(s)

                # 寫入資料的socket處理
                for s in writable:
                    try:
                        # 此client有資料要發送
                        if s in self.message_queues and not self.message_queues[s].empty():
                            try:
                                msg_type, payload = self.message_queues[s].get_nowait()
                                send_packet(s, msg_type, payload)
                            except queue.Empty: pass
                        else:
                            if s in self.outputs: self.outputs.remove(s)
                    except Exception as e:
                        print(f"[!] Write failed for {s.fileno()}: {e}")
                        self.handle_disconnect(s)
                
                # 處理異常socket
                for s in exceptional:
                    self.handle_disconnect(s)

                # 處理背景任務結果與檢查子行程
                self.process_thread_results()
                self.check_game_processes()

            except KeyboardInterrupt:
                print("\n[*] Server stopping...")
                break
            except Exception as e:
                print(f"[!] Select Loop Error: {e}")
                traceback.print_exc()

        self.cleanup_server()

    def handle_packet(self, sock, msg_type, payload):
        """封包路由分發器"""
        handlers = {
            # Auth
            MSG_LOGIN_REQ: self.handle_login,
            MSG_REGISTER_REQ: self.handle_register,
            # Room
            MSG_ROOM_LIST_REQ: self.handle_room_list,
            MSG_ROOM_CREATE_REQ: self.handle_room_create,
            MSG_ROOM_JOIN_REQ: self.handle_room_join,
            MSG_ROOM_LEAVE_REQ: self.handle_leave_room,
            # Game Store (Upload/Download/Info)
            MSG_GAME_UPLOAD_INIT: self.handle_upload_init,
            MSG_GAME_UPLOAD_DATA: self.handle_upload_data,
            MSG_GAME_UPLOAD_END: self.handle_upload_end,
            MSG_GAME_LIST_REQ: self.handle_game_list,
            MSG_GAME_DOWNLOAD_REQ: self.handle_game_download,
            # Game Launch
            MSG_GAME_START_CMD: self.handle_game_start,
            MSG_GAME_REMOVE_REQ: self.handle_game_remove, 
            MSG_GAME_RATE_REQ: self.handle_game_rate,
            MSG_DEV_MY_GAMES_REQ: self.handle_dev_my_games,
            MSG_READY_CHECK_RESP: self.handle_ready_check_resp,
            MSG_GAME_DETAIL_REQ: self.handle_game_detail,
            
            # Plugin
            MSG_PLUGIN_LIST_REQ: self.handle_plugin_list,
            MSG_PLUGIN_DOWNLOAD_REQ: self.handle_plugin_download,
            MSG_ROOM_CHAT: self.handle_room_chat
            
        }
        handler = handlers.get(msg_type)
        if handler:
            try:
                handler(sock, payload)
            except Exception as e:
                # 捕捉 Handler 內部的錯誤
                print(f"[!] Error in handler {msg_type}: {e}")
                traceback.print_exc() # 印出詳細錯誤位置
        else: print(f"[!] Unknown message type: {msg_type}")

    # -------------------------------------------------
    #  Handlers: Authentication
    # -------------------------------------------------
    def handle_login(self, sock, data):
        username = data.get("username")
        pwd = data.get("password")
        role = data.get("role", "player") # 預設為 player

        if role not in ["player", "developer"]:
            self.send_to(sock, MSG_LOGIN_RESP, {"status": "error", "msg": "Invalid role"})
            return

        # 驗證帳密
        user_db = self.users[role] # 根據角色讀取對應的字典
        stored_pwd = user_db.get(username)
        
        if stored_pwd and stored_pwd == pwd:
            # 處理重複登入 (Kick old session)
            key = (role, username)
            if key in self.active_sessions:
                old_sock = self.active_sessions[key]
                if old_sock != sock:
                    print(f"[*] Detect duplicate login for {role} {username}, kicking old session...")
                    # 發送通知給舊連線，舊Client收到這個封包後會自己斷線
                    self.send_to(old_sock, MSG_FORCE_LOGOUT, {"msg": "Logged in from another location"})   
                    # 從 socket_map 移除舊的關聯 (但暫時保留 socket 連線讓訊息傳送)
                    # 這樣下次 handle_disconnect 觸發時才不會誤刪新 session
                    if old_sock in self.socket_map:
                        # 標記舊 socket 為失效，但不立即關閉 IO
                        self.socket_map[old_sock]["username"] = None

            # 登入成功，記錄 Session
            self.active_sessions[key] = sock
            self.socket_map[sock] = {"username": username, "role": role}
            
            print(f"[+] {role.capitalize()} logged in: {username}")
            self.send_to(sock, MSG_LOGIN_RESP, {"status": "ok", "msg": "Success"})
        else:
            print(f"[-] Login failed for {role} {username}")
            self.send_to(sock, MSG_LOGIN_RESP, {"status": "error", "msg": "Invalid credentials"})

    def handle_register(self, sock, data):
        username = data.get("username")
        pwd = data.get("password")
        role = data.get("role", "player")

        if role not in ["player", "developer"]:
            self.send_to(sock, MSG_REGISTER_RESP, {"status": "error", "msg": "Invalid role"})
            return

        user_db = self.users[role]

        if not username:
            self.send_to(sock, MSG_REGISTER_RESP, {"status": "error", "msg": "Empty username"})
        elif username in user_db:
            # 帳號已被使用
            self.send_to(sock, MSG_REGISTER_RESP, {"status": "error", "msg": "Username already taken"})
        else:
            # 註冊成功
            user_db[username] = pwd
            self.save_json(USERS_DB, self.users)
            print(f"[+] Registered new {role}: {username}")
            self.send_to(sock, MSG_REGISTER_RESP, {"status": "ok", "msg": "Registered"})

    # -------------------------------------------------
    #  Handlers: Room Management
    # -------------------------------------------------
    def handle_room_list(self, sock, data):
        room_list = []
        for k, v in self.rooms.items():
            room_list.append({
                "id": k, 
                "name": v["name"], 
                "game_id": v["game_id"],
                "game_name": v["game_name"],
                "players": f"{len(v['members'])}/{v['max_players']}", 
                "status": v["status"]
            })
        self.send_to(sock, MSG_ROOM_LIST_RESP, {"rooms": room_list})

    def handle_room_create(self, sock, data):
        username = self.get_player_name(sock)
        if not username: return # 只有 Player 可以建房
        
        self.handle_leave_room(sock, None) 

        room_id = self.next_room_id
        self.next_room_id += 1
        game_id = data.get("game_id", 1)
        
        # 查找對應遊戲資料
        game_name = "Unknown"
        for name, meta in self.games_meta.items():
            if meta["id"] == int(game_id):
                game_name = name
                game_meta = meta
                break
        
        if not game_name or not game_meta:
            self.send_to(sock, MSG_ROOM_CREATE_RESP, {"status": "error", "msg": "Invalid Game ID"})
            return

        # 從 metadata 讀取 min/max players，不寫死
        max_p = game_meta.get("max_players", 2)
        min_p = game_meta.get("min_players", 2)

        room_info = {
            "id": room_id,
            "name": data.get("room_name", f"{username}'s Room"),
            "game_id": int(game_id),
            "game_name": game_name,
            "host": username,
            "members": [username],
            "max_players": max_p,
            "min_players": min_p,
            "status": "WAITING"
        }
        self.rooms[room_id] = room_info
        self.send_to(sock, MSG_ROOM_CREATE_RESP, {"status": "ok", "room": room_info})
        print(f"[*] Room {room_id} created by {username} (Max: {max_p})")

    def handle_room_join(self, sock, data):
        username = self.get_player_name(sock)
        if not username: return

        try: rid = int(data.get("room_id"))
        except: return
        
        if rid in self.rooms:
            room = self.rooms[rid]
            if username in room["members"]:
                 self.send_to(sock, MSG_ROOM_JOIN_RESP, {"status": "ok", "room": room})
                 return

            if len(room["members"]) < room["max_players"] and room["status"] == "WAITING":
                self.handle_leave_room(sock, None) 
                room["members"].append(username)
                self.send_to(sock, MSG_ROOM_JOIN_RESP, {"status": "ok", "room": room})
                self.broadcast_room_status(rid)
            else:
                self.send_to(sock, MSG_ROOM_JOIN_RESP, {"status": "error", "msg": "Full or Playing"})
        else:
            self.send_to(sock, MSG_ROOM_JOIN_RESP, {"status": "error", "msg": "Room not found"})

    def handle_leave_room(self, sock, data):
        username = self.get_player_name(sock)
        if not username: return
        
        target_rid = None
        for rid, room in self.rooms.items():
            if username in room["members"]:
                target_rid = rid
                break
        
        if target_rid:
            room = self.rooms[target_rid]
            if username in room["members"]:
                room["members"].remove(username)
            
            if not room["members"]:
                print(f"[*] Room {target_rid} is empty. Cleaning up...")
                if target_rid in self.running_games:
                    try: self.running_games[target_rid].terminate()
                    except: pass
                del self.rooms[target_rid]
            else:
                if room["host"] == username and room["members"]:
                    room["host"] = room["members"][0]
                self.broadcast_room_status(target_rid)

    # 聊天轉發
    def handle_room_chat(self, sock, data):
        username = self.get_player_name(sock)
        if not username: return
        
        # 找出房間
        room = None
        for r in self.rooms.values():
            if username in r["members"]:
                room = r; break
        
        if room:
            packet = {"user": username, "msg": data.get("msg", "")}
            # 廣播給房間所有人 (包含沒裝 Plugin 的人，讓 Client 自己決定要不要顯示)
            for s, info in self.socket_map.items():
                if info["role"] == "player" and info["username"] in room["members"]:
                    self.send_to(s, MSG_ROOM_CHAT, packet)

    # -------------------------------------------------
    #  Handlers: Game Store (Upload/Info/Rate)
    # -------------------------------------------------
    def handle_upload_init(self, sock, data):
        # 檢查權限: 只有 Developer 可以上傳
        user_info = self.socket_map.get(sock)
        if not user_info or user_info["role"] != "developer":
            self.send_to(sock, MSG_GAME_UPLOAD_INIT, {"status": "error", "msg": "Permission denied"})
            return

        game_name = data.get("name")
        version = data.get("version")
        checksum = data.get("checksum")
        save_dir = os.path.join(UPLOAD_DIR, game_name, version)
        if not os.path.exists(save_dir): os.makedirs(save_dir)
        temp_file_path = os.path.join(save_dir, "game_archive.zip.tmp")
        
        try:
            f = open(temp_file_path, "wb")
            self.upload_states[sock] = {
                "file_handle": f, "path": temp_file_path,
                "final_path": os.path.join(save_dir, "game_archive.zip"),
                "expected_checksum": checksum, "meta": data
            }
            self.send_to(sock, MSG_GAME_UPLOAD_INIT, {"status": "ready"})
        except Exception as e:
            self.send_to(sock, MSG_GAME_UPLOAD_INIT, {"status": "error", "msg": str(e)})

    def handle_upload_data(self, sock, data):
        if sock in self.upload_states:
            try: self.upload_states[sock]["file_handle"].write(data)
            except: self.handle_disconnect(sock)

    def handle_upload_end(self, sock, _=None):
        if sock not in self.upload_states: return
        state = self.upload_states[sock]
        state["file_handle"].close()
        
        # 取得上傳者身分
        user_info = self.socket_map.get(sock)
        if not user_info: 
            del self.upload_states[sock]; return
            
        if calculate_checksum(state["path"]) == state["expected_checksum"]:
            if os.path.exists(state["final_path"]): os.remove(state["final_path"])
            os.rename(state["path"], state["final_path"])
            
            meta = state["meta"]
            g_name = meta["name"]
            
            # 更新檢查：如果是更新，檢查擁有權
            if g_name in self.games_meta:
                existing_owner = self.games_meta[g_name].get("owner")
                # 如果有記錄 owner 且不是當前用戶 -> 拒絕
                if existing_owner and existing_owner != user_info["username"]:
                    self.send_to(sock, MSG_GAME_UPLOAD_END, {"status": "error", "msg": "Permission denied: You do not own this game"})
                    print(f"[-] Update denied: {user_info['username']} tried to update {g_name} (owned by {existing_owner})")
                    del self.upload_states[sock]
                    return

            if g_name not in self.games_meta:
                self.games_meta[g_name] = {
                    "id": len(self.games_meta) + 1, 
                    "name": g_name, 
                    "versions": {},
                    "owner": user_info["username"],
                    # 初始化 (如果第一次上傳)
                    "description": meta.get("description", ""),
                    "type": meta.get("type", "CLI"),
                    "min_players": meta.get("min_players", 2),
                    "max_players": meta.get("max_players", 2)
                }
            # [Spec Add] 每次更新都覆寫這些 Metadata
            self.games_meta[g_name]["description"] = meta.get("description", "")
            self.games_meta[g_name]["type"] = meta.get("type", "CLI")
            self.games_meta[g_name]["min_players"] = int(meta.get("min_players", 2)) # 強制轉 int
            self.games_meta[g_name]["max_players"] = int(meta.get("max_players", 2))
            self.games_meta[g_name]["owner"] = user_info["username"]
            self.games_meta[g_name]["latest_version"] = meta["version"]
            self.games_meta[g_name]["versions"][meta["version"]] = {
                "checksum": state["expected_checksum"], 
                "path": state["final_path"]
            }
            self.save_json(GAMES_META_DB, self.games_meta)
            self.send_to(sock, MSG_GAME_UPLOAD_END, {"status": "ok"})
            print(f"[+] Upload Success: {g_name} v{meta['version']}")
        else:
            self.send_to(sock, MSG_GAME_UPLOAD_END, {"status": "error", "msg": "Checksum mismatch"})
            
        del self.upload_states[sock]

    def handle_game_list(self, sock, data):
        print("[Debug] Received game list request from", sock)
        try:
            game_list = []
            for name, meta in self.games_meta.items():
                game_list.append({
                    "id": meta.get("id", 0),
                    "name": name,
                    "version": meta["latest_version"],
                    "min_players": meta.get("min_players", 2),
                    "max_players": meta.get("max_players", 2),
                    "owner": meta.get("owner", "Unknown")
                })
            self.send_to(sock, MSG_GAME_LIST_RESP, {"status": "ok", "games": game_list})
        except Exception as e:
            print("[!] handle_game_list error:", e)
            self.send_to(sock, MSG_GAME_LIST_RESP, {"status": "error", "msg": str(e)})

    def handle_game_download(self, sock, data):
        game_name = data.get("game_name")
        if game_name in self.games_meta:
            latest = self.games_meta[game_name]["latest_version"]
            f_info = self.games_meta[game_name]["versions"][latest]
            f_path = f_info["path"]
            if os.path.exists(f_path):
                try:
                    self.send_to(sock, MSG_GAME_DOWNLOAD_INIT, {
                        "status": "ok", "size": os.path.getsize(f_path), 
                        "checksum": f_info["checksum"], "version": latest, "game_name": game_name
                    })
                    with open(f_path, "rb") as f:
                        while chunk := f.read(4096): 
                            self.send_to(sock, MSG_GAME_DOWNLOAD_DATA, chunk)
                    self.send_to(sock, MSG_GAME_DOWNLOAD_END, {})
                except Exception as e:
                    print(f"[!] Download error: {e}")
            else:
                self.send_to(sock, MSG_GAME_DOWNLOAD_INIT, {"status": "error", "msg": "File missing"})
        else:
            self.send_to(sock, MSG_GAME_DOWNLOAD_INIT, {"status": "error", "msg": "Game not found"})

    # 獲取遊戲詳細資訊與評價 ---
    def handle_game_detail(self, sock, data):
        game_name = data.get("game_name")
        if game_name not in self.games_meta:
            self.send_to(sock, MSG_GAME_DETAIL_RESP, {"status": "error", "msg": "Game not found"})
            return

        meta = self.games_meta[game_name]
        
        # 計算平均評分
        reviews = meta.get("reviews", [])
        avg_score = 0.0
        if reviews:
            total = sum(r["score"] for r in reviews)
            avg_score = round(total / len(reviews), 1)

        # 檢查當前用戶是否玩過
        user_info = self.socket_map.get(sock)
        has_played = False
        if user_info:
            username = user_info["username"]
            played_list = meta.get("played_by", [])
            if username in played_list:
                has_played = True
                
        # 打包回傳資料
        # 根據 Spec，需包含：名稱、作者、版本、簡介、評分、評論
        resp_data = {
            "status": "ok",
            "name": meta.get("name", game_name),
            "version": meta.get("latest_version", "1.0"),
            "owner": meta.get("owner", "Unknown"),
            "description": meta.get("description", "No description available"),
            "type": meta.get("type", "CLI"),           # 新增
            "min_players": meta.get("min_players", 2), # 新增
            "max_players": meta.get("max_players", 2), # 新增
            "avg_score": avg_score,
            "reviews": reviews[-5:],
            "has_played": has_played
        }
        self.send_to(sock, MSG_GAME_DETAIL_RESP, resp_data)

    # 評分邏輯：加入資格檢查
    def handle_game_rate(self, sock, data):
        user_info = self.socket_map.get(sock)
        if not user_info or user_info["role"] != "player":
            self.send_to(sock, MSG_GAME_RATE_RESP, {"status": "error", "msg": "Only players can rate"})
            return

        game_name = data.get("game_name")
        score = data.get("score")
        comment = data.get("comment")
        username = user_info["username"]

        if game_name not in self.games_meta:
            self.send_to(sock, MSG_GAME_RATE_RESP, {"status": "error", "msg": "Game not found"})
            return

        # 檢查是否玩過
        played_list = self.games_meta[game_name].get("played_by", [])
        if username not in played_list:
            print(f"[-] Rate denied: {username} hasn't played {game_name}")
            self.send_to(sock, MSG_GAME_RATE_RESP, {"status": "error", "msg": "You must play this game first!"})
            return

        if "reviews" not in self.games_meta[game_name]:
            self.games_meta[game_name]["reviews"] = []

        review_entry = {
            "user": username,
            "score": score,
            "comment": comment,
            "time": time.time()
        }
        self.games_meta[game_name]["reviews"].append(review_entry)
        self.save_json(GAMES_META_DB, self.games_meta)

        print(f"[*] New review for {game_name} from {username}")
        self.send_to(sock, MSG_GAME_RATE_RESP, {"status": "ok", "msg": "Review added"})

    def handle_game_remove(self, sock, data):
        user_info = self.socket_map.get(sock)
        if not user_info or user_info["role"] != "developer":
            self.send_to(sock, MSG_GAME_REMOVE_RESP, {"status": "error", "msg": "Permission denied"})
            return

        game_name = data.get("name")
        if game_name in self.games_meta:
            game_data = self.games_meta[game_name]
            owner = game_data.get("owner")
            game_id = game_data.get("id")
            
            # 檢查擁有權
            if owner and owner != user_info["username"]:
                self.send_to(sock, MSG_GAME_REMOVE_RESP, {"status": "error", "msg": "You do not own this game"})
                return

            # 檢查是否有活躍房間正在使用此遊戲
            # 若有房間 (WAITING 或 PLAYING) 使用此 game_id，則禁止下架
            active_rooms = []
            for rid, r in self.rooms.items():
                if r.get("game_id") == game_id:
                    active_rooms.append(rid)
            
            if active_rooms:
                msg = f"Cannot remove: Game is active in {len(active_rooms)} room(s)."
                print(f"[-] Remove denied: {game_name} is active in rooms {active_rooms}")
                self.send_to(sock, MSG_GAME_REMOVE_RESP, {"status": "error", "msg": msg})
                return

            # 安全下架
            del self.games_meta[game_name]
            self.save_json(GAMES_META_DB, self.games_meta)
            
            # (選擇性) 刪除實體檔案，或保留檔案但移除索引
            # 這裡為了安全起見，通常只移除索引(下架)，保留檔案以免誤刪
            # 若要刪除檔案： shutil.rmtree(os.path.join(UPLOAD_DIR, game_name))
            
            print(f"[*] Game '{game_name}' removed by {user_info['username']}")
            self.send_to(sock, MSG_GAME_REMOVE_RESP, {"status": "ok", "msg": "Game removed from store."})
        else:
            self.send_to(sock, MSG_GAME_REMOVE_RESP, {"status": "error", "msg": "Not found"})

    # 查詢開發者自己的遊戲
    def handle_dev_my_games(self, sock, data):
        user_info = self.socket_map.get(sock)
        if not user_info or user_info["role"] != "developer":
            return # Ignore

        my_list = []
        for g_name, g_info in self.games_meta.items():
            if g_info.get("owner") == user_info["username"]:
                my_list.append({
                    "name": g_name, 
                    "version": g_info["latest_version"],
                    "id": g_info["id"]
                })
        
        self.send_to(sock, MSG_DEV_MY_GAMES_RESP, {"games": my_list})

    # -------------------------------------------------
    #  Handlers: Game Launch & Process
    # -------------------------------------------------
    def handle_game_start(self, sock, data):
        username = self.get_player_name(sock)
        if not username: return

        room = None
        for r in self.rooms.values():
            if username in r["members"]:
                room = r; break
        
        if not room or room["host"] != username: return

        # 檢查房間人數是否足夠
        # 動態判斷最小人數
        min_p = room.get("min_players", 2)
        if len(r["members"]) < min_p:
            self.send_to(sock, MSG_GAME_START_FAIL, {"msg": f"Not enough players (Min {min_p})"})
            return
        
        game_id = room["game_id"]
        game_meta = None
        for name, meta in self.games_meta.items():
            if meta["id"] == game_id:
                game_meta = meta; break
        
        if not game_meta: return

        latest_version = game_meta.get("latest_version", "1.0")

        print(f"[*] Initiating Ready Check for Room {room['id']}...")
        
        # 初始化檢查狀態
        room["ready_check"] = {
            "target_count": len(room["members"]),
            "responses": 0,
            "all_ok": True,
            "failed_reason": None,
            "game_meta": game_meta,
            "version": latest_version
        }

        # 廣播檢查請求給所有人 (包含房主自己)
        check_req = {
            "game_name": game_meta["name"],
            "version": latest_version
        }
        
        # 找出房間內所有 socket 發送請求
        for s, info in self.socket_map.items():
            if info["role"] == "player" and info["username"] in room["members"]:
                self.send_to(s, MSG_READY_CHECK_REQ, check_req)

    # 遊戲啟動流程 Step 2: 收集回報
    def handle_ready_check_resp(self, sock, data):
        username = self.get_player_name(sock)
        if not username: return

        # 找到該玩家所在的房間
        room = None
        for r in self.rooms.values():
            if username in r["members"]:
                room = r; break
        
        if not room or "ready_check" not in room: return

        check = room["ready_check"]
        check["responses"] += 1
        
        status = data.get("status")
        if status != "ok":
            check["all_ok"] = False
            # 記錄是誰沒準備好
            check["failed_reason"] = f"{username}: {data.get('msg', 'Not ready')}"

        # 如果所有人都回報了
        if check["responses"] >= check["target_count"]:
            if check["all_ok"]:
                # 全員通過 -> 真正啟動遊戲
                self._start_game_sequence(room, check["game_meta"], check["version"])
            else:
                # 有人失敗 -> 廣播失敗訊息，取消啟動
                fail_packet = {"msg": f"Start Failed! {check['failed_reason']}"}
                for s, info in self.socket_map.items():
                    if info["role"] == "player" and info["username"] in room["members"]:
                        self.send_to(s, MSG_GAME_START_FAIL, fail_packet)
            
            # 清除檢查狀態
            del room["ready_check"]

    # 真正啟動邏輯
    def _start_game_sequence(self, room, game_meta, version):
        print(f"[*] All players ready. Launching Room {room['id']}...")
        task_data = {
            "room_id": room["id"], "game_meta": game_meta,
            "game_id": room["game_id"], "members": list(room["members"]),
            "latest_version": version
        }
        # 使用執行緒啟動子行程，避免卡住主迴圈
        t = threading.Thread(target=self._launch_game_worker, args=(task_data,))
        t.daemon = True; t.start()

    def _launch_game_worker(self, data):
        room_id = data["room_id"]
        game_meta = data["game_meta"]
        try:
            # 解壓縮與準備環境
            latest_ver = game_meta["latest_version"]
            archive_path = game_meta["versions"][latest_ver]["path"]
            base_dir = os.path.dirname(archive_path) 
            extract_dir = os.path.join(base_dir, f"run_env_{room_id}")
            if os.path.exists(extract_dir): shutil.rmtree(extract_dir, ignore_errors=True)
            os.makedirs(extract_dir)
            
            import zipfile
            with zipfile.ZipFile(archive_path, 'r') as z: z.extractall(extract_dir)
        
            manifest_path = os.path.join(extract_dir, "manifest.json")
            with open(manifest_path) as f: manifest = json.load(f)
            if "execution" not in manifest or "server_cmd" not in manifest["execution"]:
                raise ValueError("Manifest error")

            server_cmd = manifest["execution"]["server_cmd"]
            game_port = find_free_port()
            if game_port == 0: raise RuntimeError("No free ports")

            cmd = list(server_cmd) + ["--port", str(game_port)]
            proc = subprocess.Popen(cmd, cwd=extract_dir)
            
            result = {
                "room_id": room_id, "pid": proc.pid, "proc": proc,
                "port": game_port, "game_id": data["game_id"], "members": data["members"],
                "version": data["latest_version"]
            }
            self.thread_results.put(("GAME_LAUNCH_SUCCESS", result))
        except Exception as e:
            err_msg = str(e)
            print(f"[!] Launch Error (Room {room_id}): {err_msg}")
            self.thread_results.put(("GAME_LAUNCH_FAIL", {"room_id": room_id, "msg": err_msg}))

    # 遊戲啟動成功後，記錄玩家已遊玩
    def on_game_launched(self, result):
        room_id = result["room_id"]
        game_id = result["game_id"]
        
        # 記錄正在執行的遊戲process
        self.running_games[room_id] = result["proc"]
        
        # 1. 找出遊戲名稱並更新 played_by 紀錄
        target_game_name = None
        for name, meta in self.games_meta.items():
            if meta["id"] == game_id:
                target_game_name = name
                break
        
        if target_game_name:
            if "played_by" not in self.games_meta[target_game_name]:
                self.games_meta[target_game_name]["played_by"] = []
            
            # 將房間內的成員加入遊玩紀錄 (避免重複)
            changed = False
            for member in result["members"]:
                if member not in self.games_meta[target_game_name]["played_by"]:
                    self.games_meta[target_game_name]["played_by"].append(member)
                    changed = True
            
            if changed:
                self.save_json(GAMES_META_DB, self.games_meta)
                print(f"[*] Updated play history for {target_game_name}")

        # 2. 通知房間成員與更新狀態 (保持原樣)
        if room_id in self.rooms:
            self.rooms[room_id]["status"] = "PLAYING"
            self.broadcast_room_status(room_id)
            
            packet = {
                "server_ip": SERVER_IP, 
                "port": result["port"], 
                "game_id": result["game_id"],
                "version": result.get("version", "1.0")
            }
            for s, info in self.socket_map.items():
                if info["role"] == "player" and info["username"] in result["members"]:
                    self.send_to(s, MSG_GAME_LAUNCH_EVENT, packet)
        
        print(f"[*] Room {room_id} launched on port {result['port']} (PID: {result['pid']})")

    def on_game_launch_failed(self, result):
        print(f"[!] Room {result['room_id']} failed to launch: {result['msg']}")
        
    # -------------------------------------------------
    #  Handlers: Plugin System
    # -------------------------------------------------
    # Plugin 列表
    def handle_plugin_list(self, sock, data):
        print(f"[DEBUG] Handling Plugin List Request from {self.get_player_name(sock)}")
        plugins = [
            {"name": "RoomChat", "desc": "Chat in Room (Window)", "ver": "1.0"}
        ]
        self.send_to(sock, MSG_PLUGIN_LIST_RESP, {"plugins": plugins})
        
    # Plugin 下載
    def handle_plugin_download(self, sock, data):
        p_name = data.get("name")
        print(f"[DEBUG] Handling Plugin Download: {p_name}")
        
        if p_name == "RoomChat":
            # 確保 CHAT_PLUGIN_CODE 變數有定義且不為空
            if not CHAT_PLUGIN_CODE:
                print("[!] Error: CHAT_PLUGIN_CODE is empty!")
                self.send_to(sock, MSG_PLUGIN_DOWNLOAD_RESP, {"status": "error", "msg": "Server Error (No Code)"})
                return

            self.send_to(sock, MSG_PLUGIN_DOWNLOAD_RESP, {
                "status": "ok", 
                "code": CHAT_PLUGIN_CODE
            })
            print("[DEBUG] Plugin code sent.")
        else:
            self.send_to(sock, MSG_PLUGIN_DOWNLOAD_RESP, {"status": "error", "msg": "Not found"})

    # Internal / Utils
    def load_json(self, path):
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r') as f: return json.load(f)
        except: return {}

    # 專門讀取 User DB，確保結構正確
    def load_users_db(self):
        data = self.load_json(USERS_DB)
        # 確保有分開的 role 區塊
        if "player" not in data: data["player"] = {}
        if "developer" not in data: data["developer"] = {}
        return data

    def save_json(self, path, data):
        temp = path + '.tmp'
        try:
            with open(temp, 'w') as f: json.dump(data, f, indent=4)
            os.replace(temp, path)
        except Exception as e:
            print(f"[!] Save JSON error: {e}")

    def signal_handler(self, signum, frame):
        print(f"\n[*] Caught signal {signum}, shutting down...")
        self.cleanup_server()
        sys.exit(0)

    # --- Room Logic (需配合 socket_map 取得 username) ---
    def get_player_name(self, sock):
        info = self.socket_map.get(sock)
        if info and info["role"] == "player":
            return info["username"]
        return None

    def send_to(self, sock, msg_type, payload):
        if sock not in self.inputs: return
        if sock in self.message_queues:
            self.message_queues[sock].put((msg_type, payload))
            if sock not in self.outputs: self.outputs.append(sock)

    # 斷線處理更新
    def handle_disconnect(self, sock):
        # 清理上傳狀態
        if sock in self.upload_states:
            try: self.upload_states[sock]["file_handle"].close()
            except: pass
            del self.upload_states[sock]
            
        # 清理使用者 Session
        if sock in self.socket_map:
            user_info = self.socket_map[sock]
            username = user_info["username"]
            role = user_info["role"]
            
            if username:
                print(f"[-] {role.capitalize()} {username} disconnected")
                
                key = (role, username)
                if key in self.active_sessions and self.active_sessions[key] == sock:
                    del self.active_sessions[key]

                if role == "player":
                    self.handle_leave_room(sock, None)

            del self.socket_map[sock]

        # 關閉 Socket
        if sock in self.inputs: self.inputs.remove(sock)
        if sock in self.outputs: self.outputs.remove(sock)
        if sock in self.message_queues: del self.message_queues[sock]
        try: sock.close()
        except: pass

    def process_thread_results(self):
        while not self.thread_results.empty():
            try:
                task_type, result = self.thread_results.get_nowait()
                if task_type == "GAME_LAUNCH_SUCCESS":
                    self.on_game_launched(result)
                elif task_type == "GAME_LAUNCH_FAIL":
                    self.on_game_launch_failed(result)
            except queue.Empty:
                break

    def check_game_processes(self):
        finished_rooms = []
        for rid, proc in self.running_games.items():
            if proc.poll() is not None:
                finished_rooms.append(rid)
        
        for rid in finished_rooms:
            exit_code = self.running_games[rid].returncode
            print(f"[*] Room {rid} Game Server finished (Exit Code: {exit_code})")
            del self.running_games[rid]
            if rid in self.rooms:
                self.rooms[rid]["status"] = "WAITING"
                self.broadcast_room_status(rid)

    def cleanup_server(self):
        print("[*] Cleaning up resources...")
        for rid, proc in list(self.running_games.items()):
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=2)
                    except subprocess.TimeoutExpired: proc.kill()
            except: pass
        self.running_games.clear()
        for s in self.inputs:
            try: s.close()
            except: pass

    def broadcast_room_status(self, room_id):
        if room_id not in self.rooms: return
        room = self.rooms[room_id]
        packet = {"room": room}
        # 廣播給房間內的所有成員 (需透過 socket_map 查找 socket)
        # 這裡需要反向查找: 哪些 socket 的 username 在 room['members'] 裡
        # 效率較低但功能正確的寫法:
        for s, info in list(self.socket_map.items()):
            if info["role"] == "player" and info["username"] in room["members"]:
                self.send_to(s, MSG_ROOM_STATUS_UPDATE, packet)

                  
# [Spec PL] 內建的聊天室 Plugin 程式碼 (Client 端執行)
CHAT_PLUGIN_CODE = r"""
import tkinter as tk
import threading
import struct
import json

class RoomChat:
    def __init__(self, send_func, username):
        self.send_func = send_func
        self.username = username
        self.root = None
        self.text_area = None
        self.entry = None
        self.running = False

    def start(self):
        self.running = True
        t = threading.Thread(target=self._gui_loop, daemon=True)
        t.start()

    def _gui_loop(self):
        self.root = tk.Tk()
        self.root.title(f"Chat - {self.username}")
        self.root.geometry("300x400")
        
        self.text_area = tk.Text(self.root, state='disabled')
        self.text_area.pack(expand=True, fill='both')
        
        frame = tk.Frame(self.root)
        frame.pack(fill='x')
        self.entry = tk.Entry(frame)
        self.entry.pack(side='left', expand=True, fill='x')
        self.entry.bind("<Return>", self._send)
        btn = tk.Button(frame, text="Send", command=self._send)
        btn.pack(side='right')
        
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.mainloop()

    def _send(self, event=None):
        try:
            msg = self.entry.get()
            if not msg: return
            self.entry.delete(0, 'end')
            self.send_func(95, {"msg": msg})
        except: pass

    def on_message(self, user, msg):
        if not self.root: return
        try:
            # 確保在 GUI 執行緒更新文字
            self.root.after(0, lambda: self._append_text(user, msg))
        except: pass

    def _append_text(self, user, msg):
        try:
            self.text_area.config(state='normal')
            self.text_area.insert('end', f"[{user}]: {msg}\n")
            self.text_area.see('end')
            self.text_area.config(state='disabled')
        except: pass

    def _close(self):
        self.running = False
        if self.root:
            # [Critical Fix] 使用 after 將銷毀動作排程回 GUI 執行緒
            # 防止從 Main Thread 呼叫 destroy 導致死鎖
            try: self.root.after(0, self._safe_destroy)
            except: pass

    def _safe_destroy(self):
        try:
            if self.root:
                self.root.quit()    # 停止 mainloop
                self.root.destroy() # 銷毀視窗
        except: pass
        self.root = None
"""

if __name__ == "__main__":
    server = GameStoreServer()
    server.start()