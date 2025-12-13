import socket
import threading
import sys
import os
import time
import queue
import zipfile
import subprocess
import json
import importlib
import struct

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from common.utils import *

# ==========================================
#  Configuration
# ==========================================
HOST = '140.113.17.11'
PORT = 12365

# States
STATE_DISCONNECTED = 0
STATE_AUTH_MENU = 1
STATE_MAIN_MENU = 2
STATE_ROOM_LIST = 3
STATE_IN_ROOM = 4
STATE_STORE = 5
STATE_PLAYING = 6
STATE_PLUGIN = 7

# ==========================================
#  Lobby Client Class
# ==========================================
class LobbyClient:
    def __init__(self):
        self.sock = None
        self.state = STATE_DISCONNECTED
        self.running = True # 控制整個程式是否結束
        self.connected = False # 控制當前連線是否有效
        self.username = None
        self.recv_thread = None
        self.server_port = PORT
        
        self.data_store = {
            "room_list": [],
            "current_room": None,
            "game_list": []
        }
        
        # Async Response Handling
        self.response_event = threading.Event()
        self.last_response = None
        self.download_complete_event = threading.Event()
        self.download_state = None 

        # Plugin System
        self.active_chat_plugin = None
        # 使用絕對路徑建立 plugins 資料夾，確保 import 路徑正確
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.plugin_dir = os.path.join(self.base_dir, "plugins")
        if not os.path.exists(self.plugin_dir): os.makedirs(self.plugin_dir)
        init_py = os.path.join(self.plugin_dir, "__init__.py")
        if not os.path.exists(init_py): 
            with open(init_py, "w") as f: f.write("")
        if self.base_dir not in sys.path: sys.path.append(self.base_dir)
    
    # -------------------------------------------------
    #  Networking Core
    # -------------------------------------------------
    # 連線函式，失敗時不會結束程式，只會回傳 False
    def connect(self):
        try:
            if self.sock: self.sock.close()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(None)
            self.sock.connect((HOST, self.server_port))
            
            self.connected = True
            self.state = STATE_AUTH_MENU
            
            # 啟動背景接收執行緒
            self.recv_thread = threading.Thread(target=self.network_loop, daemon=True)
            self.recv_thread.start()
            
            print(f"[*] Connected to server {HOST}:{self.server_port}")
            return True
        except ConnectionRefusedError:
            print(f"[!] Cannot connect to {HOST}:{self.server_port}.")
            inp = input("Enter new port (or Press Enter to retry): ")
            if inp.isdigit():
                self.server_port = int(inp)
            return False
        except Exception as e:
            print(f"[!] Connection failed: {e}")
            return False

    # 網路迴圈：負責監聽，斷線時修改 self.connected
    def network_loop(self):
        while self.connected:
            try:
                msg_type, payload = recv_packet(self.sock)
                if msg_type is None:
                    # 伺服器斷線
                    if self.connected and self.running:
                        self.clear_line()
                        print("\n[!] Disconnected from server.")
                        self.connected = False
                        self.response_event.set() # 解除可能卡住的 wait
                        self.download_complete_event.set()
                    break
                
                # 處理訊息前再次確認，避免在關閉時處理
                if self.running:
                    self.handle_server_message(msg_type, payload)
                    
            except (ConnectionResetError, BrokenPipeError, OSError):
                # OSError 通常發生在 socket 被主動 close 時
                if self.connected and self.running:
                    print("\n[!] Connection lost.")
                    self.connected = False
                break
            except Exception as e:
                print(f"[!] Network Error: {e}")
                self.connected = False
                break

    def reset_req(self):
        """發送請求前呼叫，清空上一次的回應狀態"""
        self.response_event.clear()
        self.last_response = None
        
    def wait_for_response(self, timeout=3.0):
        """等待 Server 回應，不會自動清空 last_response 以避免 Race Condition"""
        self.response_event.wait(timeout)
        self.response_event.clear()
        # 如果超時 (last_response 還是 None)，回傳明確的錯誤
        if self.last_response is None:
            return {"status": "error", "msg": "Request Timeout"} 
        return self.last_response
       
    def clear_line(self):
        if not self.running: return
        try:
            sys.stdout.write('\r' + ' ' * 60 + '\r')
            sys.stdout.flush()
        except: pass

    def handle_server_message(self, msg_type, data):
        # Plugin Hook
        if msg_type == MSG_ROOM_CHAT:
            if self.active_chat_plugin and self.active_chat_plugin.running:
                self.active_chat_plugin.on_message(data["user"], data["msg"])
            return # 沒裝 Plugin 的人直接忽略，不崩潰
        
        # Priority Messages
        if msg_type == MSG_FORCE_LOGOUT:
            self.clear_line()
            print(f"\n[!] {data.get('msg', 'Logged out by server')}")
            print("[*] Press Enter to return to Login...")
            self.connected = False # 這會終止 network_loop
            self.username = None
            self.response_event.set()
            # 強制關閉 socket 以中斷 recv
            try: self.sock.close()
            except: pass
            return
        
        if msg_type == MSG_GAME_START_FAIL:
            self.clear_line()
            print(f"\n[!] {data['msg']}")  
            # 如果我是房主且正在轉圈圈等待，這會讓狀態變回 IN_ROOM，跳出迴圈
            if self.state == STATE_PLAYING: # 如果卡在轉圈圈，解鎖
                self.state = STATE_IN_ROOM
            self.print_current_room()
            sys.stdout.write("> ")
            sys.stdout.flush()
            return
        
        # [優先處理] 準備檢查請求 -> 自動檢查並回覆 Server
        if msg_type == MSG_READY_CHECK_REQ:
            self._handle_ready_check(data)
            return
        
        if msg_type == MSG_GAME_LAUNCH_EVENT:
            self.clear_line()
            print(f"\n[*] GAME LAUNCH! Connect to {data['server_ip']}:{data['port']}")
            self.launch_game_client(data)
            return
        
        # In-Game Suppression
        if self.state == STATE_PLAYING:
            # 這裡只做資料更新，不 print
            if msg_type == MSG_ROOM_LIST_RESP:
                self.data_store["room_list"] = data["rooms"]
            elif msg_type == MSG_ROOM_STATUS_UPDATE:
                self.data_store["current_room"] = data["room"]
            # 忽略其他 UI 相關訊息
            return

        # Response Handling
        if msg_type == MSG_LOGIN_RESP:
            self.last_response = data
            if data["status"] == "ok":
                self.state = STATE_MAIN_MENU
            self.response_event.set()

        elif msg_type == MSG_REGISTER_RESP:
            self.last_response = data
            self.response_event.set()

        elif msg_type == MSG_ROOM_LIST_RESP:
            self.data_store["room_list"] = data["rooms"]
            self.response_event.set()

        elif msg_type in [MSG_ROOM_CREATE_RESP, MSG_ROOM_JOIN_RESP]:
            self.last_response = data
            if data["status"] == "ok":
                self.state = STATE_IN_ROOM
                self.data_store["current_room"] = data["room"]
            self.response_event.set()

        elif msg_type == MSG_ROOM_STATUS_UPDATE:
            self.data_store["current_room"] = data["room"]
            # 只有當使用者在房間畫面時才刷新，避免干擾 Store 畫面
            if self.state == STATE_IN_ROOM:
                self.clear_line()
                self.print_current_room()
                sys.stdout.write("> ")
                sys.stdout.flush()

        elif msg_type == MSG_GAME_LIST_RESP:
            self.data_store["game_list"] = data["games"]
            self.response_event.set()

        elif msg_type == MSG_GAME_DOWNLOAD_INIT:
            self.last_response = data
            if data["status"] == "ok":
                self.start_download(data)
            self.response_event.set()

        elif msg_type == MSG_GAME_DOWNLOAD_DATA:
            if self.download_state:
                try:
                    self.download_state["f"].write(data)
                    self.download_state["received"] += len(data)
                except: pass

        elif msg_type == MSG_GAME_DOWNLOAD_END:
            if self.download_state:
                self.finish_download()
            self.download_complete_event.set()
            
        elif msg_type == MSG_GAME_LAUNCH_EVENT:
            self.clear_line()
            print(f"\n[*] GAME LAUNCH! Connect to {data['server_ip']}:{data['port']}")
            self.launch_game_client(data)
            
        elif msg_type == MSG_GAME_DETAIL_RESP:
            self.last_response = data
            self.response_event.set()

        elif msg_type in [MSG_PLUGIN_LIST_RESP, MSG_PLUGIN_DOWNLOAD_RESP]:
            self.last_response = data
            self.response_event.set()

        # 新增評分回應處理
        elif msg_type == MSG_GAME_RATE_RESP:
            self.last_response = data
            self.response_event.set()

    # -------------------------------------------------
    #  Internal Helpers (Download / Plugin / Launch)
    # -------------------------------------------------
    def start_download(self, data):
        game_name = data["game_name"]
        save_dir = os.path.join("downloads", self.username, game_name)
        if not os.path.exists(save_dir): os.makedirs(save_dir)
        zip_path = os.path.join(save_dir, "game.zip")
        
        self.download_state = {
            "f": open(zip_path, "wb"), "path": zip_path, "dir": save_dir,
            "size": data["size"], "expected_checksum": data["checksum"],
            "received": 0, "name": game_name, "start_time": time.time()
        }
        self.download_complete_event.clear()


    def finish_download(self):
        state = self.download_state
        state["f"].close()
        self.clear_line()
        print(f"[*] Download finished. Verifying...")
        
        cal_sum = calculate_checksum(state["path"])
        if cal_sum == state["expected_checksum"]:
            try:
                with zipfile.ZipFile(state["path"], 'r') as zip_ref:
                    zip_ref.extractall(state["dir"])
                print(f"[+] Game installed: {state['name']}")
                os.remove(state["path"])
            except Exception as e:
                print(f"[-] Extraction failed: {e}")
        else:
            print(f"[-] Checksum Mismatch!")
        self.download_state = None
   
    def _get_local_version(self, game_name):
        manifest_path = os.path.join("downloads", self.username, game_name, "manifest.json")
        if not os.path.exists(manifest_path):
            return None # 未安裝
        try:
            with open(manifest_path, 'r') as f:
                return json.load(f).get("version")
        except:
            return None

    def _handle_ready_check(self, data):
        game_name = data["game_name"]; req_ver = data["version"]
        local_ver = self._get_local_version(game_name)
        status, msg = "ok", "Ready"
        if not local_ver: status, msg = "error", "Not installed"
        elif local_ver != req_ver: status, msg = "error", f"Ver mismatch ({local_ver})"
        if status != "ok":
            self.clear_line(); print(f"\n[!] Ready Check Failed: {msg}. Go to Store.")
            self.print_current_room(); sys.stdout.write("> "); sys.stdout.flush()
        send_packet(self.sock, MSG_READY_CHECK_RESP, {"status": status, "msg": msg})

    def launch_game_client(self, data):
        room = self.data_store.get("current_room")
        game_name = room.get("game_name", "TicTacToe") if room else "TicTacToe"
        game_dir = os.path.join("downloads", self.username, game_name)
        manifest_path = os.path.join(game_dir, "manifest.json")
        
        if not os.path.exists(manifest_path):
            print(f"[!] Game not installed: {game_dir}")
            print(f"[!] Please go to Store to download.")
            return

        try:
            with open(manifest_path, 'r') as f: manifest = json.load(f)
            local_ver = manifest.get("version", "0.0")
            server_ver = data.get("version", "1.0")
            
            if local_ver != server_ver:
                self.clear_line()
                print(f"\n[!] Version Mismatch!")
                print(f"    Server requires: v{server_ver}")
                print(f"    You have:        v{local_ver}")
                print(f"[!] Please go to Store and UPDATE the game to continue.")
                
                # 強制切回房間狀態，不啟動遊戲
                self.state = STATE_IN_ROOM
                self.print_current_room()
                sys.stdout.write("> ")
                sys.stdout.flush()
                return
            cmd_list = manifest["execution"]["client_cmd"]
            args_format = manifest["execution"]["args_format"]
            
            # 確保使用當前的 python 執行檔 (解決環境變數問題)
            
            if cmd_list[0] == "python":
                cmd_list[0] = sys.executable

            # 檢查執行檔是否存在
            script_path = os.path.join(game_dir, cmd_list[1])
            if not os.path.exists(script_path):
                 print(f"[!] Missing script file: {script_path}")
                 print(f"[!] Please Re-Download the game from Store.")
                 return
                
            final_cmd = list(cmd_list)
            # 注入 IP 與 Port
            ip_flag = args_format.get("connect_ip", "--ip")
            port_flag = args_format.get("connect_port", "--port")
            
            final_cmd.append(ip_flag)
            final_cmd.append(data["server_ip"])
            final_cmd.append(port_flag)
            final_cmd.append(str(data["port"]))
            
            self.clear_line()
            print(f"[*] Launching Game: {' '.join(final_cmd)}")
            print(f"[*] Game Dir: {game_dir}")
            self.state = STATE_PLAYING
            
            # Windows 使用 CREATE_NEW_CONSOLE 開啟獨立視窗
            kwargs = {}
            if sys.platform == "win32":
                kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
            
            # 啟動遊戲 (在新視窗中)
            subprocess.Popen(final_cmd, cwd=game_dir, **kwargs).wait()
            
            print("\n[*] Game Session finished.")
            # 遊戲結束後，將狀態切回房間，並重繪介面
            self.state = STATE_IN_ROOM
            self.print_current_room()
            sys.stdout.write("> ")
            sys.stdout.flush()

        except Exception as e:
            print(f"[!] Error running game: {e}")
            self.state = STATE_IN_ROOM

    # 出下載邏輯，供 Store 和 Create Room 共用
    def _download_helper(self, game_name):
        self.reset_req()
        send_packet(self.sock, MSG_GAME_DOWNLOAD_REQ, {"game_name": game_name})
        resp = self.wait_for_response()
        
        if resp.get("status") == "ok":
            print(f"[*] Downloading {game_name}...")
            self.download_complete_event.wait()
            return True
        else:
            print(f"[-] Download failed: {resp.get('msg')}")
            return False

    # Plugin Helpers
    # Plugin Management
    def _plugin_send_wrapper(self, msg_type, payload):
        send_packet(self.sock, msg_type, payload)


    def _get_user_plugin_path(self):
        if not self.username: return None
        return os.path.join(self.plugin_dir, f"room_chat_{self.username}.py")

    # 取得當前使用者的 Plugin 模組名稱 (for importlib)
    def _get_user_plugin_module(self):
        if not self.username: return None
        return f"plugins.room_chat_{self.username}"
    
    def _activate_chat_plugin(self):
        if not self.username: return
        path = self._get_user_plugin_path()
        
        if not path or not os.path.exists(path): return
        
        try:
            mod_name = self._get_user_plugin_module()
            
            # 針對不同使用者載入不同模組
            if mod_name in sys.modules:
                mod = importlib.reload(sys.modules[mod_name])
            else:
                mod = importlib.import_module(mod_name)
            
            self.active_chat_plugin = mod.RoomChat(self._plugin_send_wrapper, self.username)
            self.active_chat_plugin.start()
            print(f"[*] Chat Plugin Activated for {self.username}")
        except Exception as e:
            print(f"[!] Plugin Error: {e}")

    def _deactivate_chat_plugin(self):
        if self.active_chat_plugin:
            try:
                self.active_chat_plugin._close()
            except: pass
            self.active_chat_plugin = None
            
    # -------------------------------------------------
    #  UI Menus
    # -------------------------------------------------           
    def start(self):
        while self.running:
            if not self.connect(): time.sleep(3); continue
            while self.connected and self.running:
                try:
                    if self.state == STATE_AUTH_MENU: self.auth_menu()
                    elif self.state == STATE_MAIN_MENU: self.main_menu()
                    elif self.state == STATE_ROOM_LIST: self.room_list_menu()
                    elif self.state == STATE_IN_ROOM: self.in_room_menu()
                    elif self.state == STATE_STORE: self.store_menu()
                    elif self.state == STATE_PLUGIN: self.plugin_menu()
                    elif self.state == STATE_PLAYING: time.sleep(0.5)
                    else: time.sleep(0.1)
                except KeyboardInterrupt: self.running = False; break
                except: pass
            
            if self.running: print("\n[*] Reconnecting..."); time.sleep(1)
        
        # 優雅關閉
        if self.sock: 
            try: self.sock.close() # 觸發 recv 異常退出
            except: pass
        
        # 等待背景執行緒結束，避免 stdout 競爭
        if self.recv_thread and self.recv_thread.is_alive():
            self.recv_thread.join(timeout=1.0)
            
        print("[*] Byte!")

    def auth_menu(self):
        print("\n=== Game Store Auth ===")
        print("1. Login  2. Register  3. Exit")
        choice = input("Select: ")
        
        # 防止在 input 期間斷線導致崩潰
        if not self.connected: return 

        if choice == '1':
            user = input("Username: ")
            pwd = input("Password: ")
            self.reset_req()
            if send_packet(self.sock, MSG_LOGIN_REQ, {"username": user, "password": pwd}):
                resp = self.wait_for_response()
                if resp.get("status") == "ok":
                    self.username = user
                    print(f"[+] Welcome {user}!")
                else:
                    print(f"[-] Failed: {resp.get('msg')}")
        elif choice == '2':
            user = input("New User: ")
            pwd = input("New Pass: ")
            self.reset_req()
            if send_packet(self.sock, MSG_REGISTER_REQ, {"username": user, "password": pwd}):
                resp = self.wait_for_response()
                print(f"[*] {resp.get('msg')}")
        elif choice == '3':
            self.running = False
            self.connected = False # Break inner loop

    def main_menu(self):
        print("\n=== Main Menu ===\n1. Play (Rooms)\n2. Store\n3. Plugins (Bonus)\n4. Logout")
        c = input("Select: ")
        if c == '1': 
            self.state = STATE_ROOM_LIST
            self.reset_req()
            send_packet(self.sock, MSG_ROOM_LIST_REQ, {})
            time.sleep(0.1) 
        elif c == '2': self.state = STATE_STORE
        elif c == '3': self.state = STATE_PLUGIN
        elif c == '4': self.state = STATE_AUTH_MENU; self.username = None

    def store_menu(self):
        while self.connected:
            print("\n=== Game Store ===")
            print("[*] Fetching Game List...")
            
            self.reset_req()
            send_packet(self.sock, MSG_GAME_LIST_REQ, {})
            resp = self.wait_for_response(timeout=3.0)
            
            if resp.get("status") == "error":
                print(f"[!] Failed to load game list: {resp.get('msg')}")
                choice = input("[R]etry or [B]ack? ").upper()
                if choice == 'B': 
                    self.state = STATE_MAIN_MENU
                    return
                continue # Retry loop
            
            games = self.data_store.get("game_list", [])
            
            if not games:
                print("\n[!] Currently no games available for download.")
                choice = input("[R]efresh or [B]ack? ").upper()
                if choice == 'B':
                    self.state = STATE_MAIN_MENU
                    return
                continue

            print(f"\n{'No.':<4} {'Name':<15} {'Latest':<8} {'Status'}")
            print("-" * 45)
            for idx, g in enumerate(games):
                # [UX Fix] 判斷狀態
                local_ver = self._get_local_version(g['name'])
                server_ver = g['version']
                
                status = "[New]"
                if local_ver:
                    if local_ver == server_ver:
                        status = "[Installed]"
                    else:
                        status = "[Update!]" # 版本不同，提示更新
                
                print(f"{idx+1:<4} {g['name']:<15} {server_ver:<8} {status}")
            print("-" * 45)
            
            print("\n[Input Number] Details/Download | [B] Back")
            choice = input("Select: ").upper()
            if choice == 'B':
                self.state = STATE_MAIN_MENU
                return
            
            try:
                sel = int(choice) - 1
                if 0 <= sel < len(games):
                    # 進入詳細頁面
                    target_game = games[sel]["name"]
                    self.game_detail_menu(target_game)
                else:
                    print("[!] Invalid selection.")
                    time.sleep(0.5)
            except ValueError:
                print("[!] Invalid input.")
                time.sleep(0.5)

    # 詳細資訊頁面 (包含下載與評分功能)
    def game_detail_menu(self, game_name):
        print(f"[*] Fetching details for '{game_name}'...")
        
        self.reset_req()
        send_packet(self.sock, MSG_GAME_DETAIL_REQ, {"game_name": game_name})
        
        # 增加逾時判斷與錯誤印出
        if not self.response_event.wait(3.0): # 等待 3 秒
            print("\n[!] Error: Timeout waiting for server response.")
            input("Press Enter to return...")
            return

        resp = self.last_response
        # print(f"[Debug] Server Raw Response: {resp}") # 若想看原始回應可取消註解
        
        if resp.get("status") != "ok":
            print(f"\n[!] Server Error: {resp.get('msg', 'Unknown Error')}")
            input("Press Enter to return...")
            return

        # 取得遊玩資格 (預設為 False 以策安全)
        has_played = resp.get("has_played", False)
        # 取得本地版本資訊
        local_ver = self._get_local_version(game_name)
        server_ver = resp['version']
        
        install_status = "Not Installed"
        if local_ver:
            if local_ver == server_ver:
                install_status = f"Installed (v{local_ver})"
            else:
                install_status = f"Update Available (v{local_ver} -> v{server_ver})"

        # 顯示優化後的資訊
        self.clear_line()
        print(f"\n{'='*12} {resp['name']} {'='*12}")
        print(f"Author:      {resp.get('owner', 'Unknown')}")
        print(f"Type:        {resp.get('type', 'CLI')}")
        print(f"Players:     {resp.get('min_players')}-{resp.get('max_players')}")
        print(f"Rating:      {resp.get('avg_score', 'N/A')} / 5.0")
        print(f"Status:      {install_status}") # [UX Fix] 明確告知版本狀態
        print(f"Played:      {'Yes' if resp.get('has_played') else 'No'}")
        print("-" * 40)
        print(f"Description:\n  {resp.get('description', 'No description')}")
        print("-" * 40)
        print("Latest Reviews:")
        reviews = resp.get('reviews', [])
        if not reviews:
            print("  (No reviews yet)")
        else:
            for r in reviews:
                print(f"  [{r['score']}/5] {r['user']}: {r['comment']}")
        print("=" * 40)

        print("\n[Options] 1. Download Game  2. Rate Game  3. Back")
        act = input("Select: ")
        
        if act == '1': # 下載
            self.response_event.clear()
            self.download_complete_event.clear()
            send_packet(self.sock, MSG_GAME_DOWNLOAD_REQ, {"game_name": game_name})
            d_resp = self.wait_for_response()
            if d_resp.get("status") == "ok":
                print(f"[*] Downloading {game_name}...")
                self.download_complete_event.wait()
                input("\n[Done] Press Enter...")
            else:
                print(f"[-] Download failed: {d_resp.get('msg')}")
                input("Press Enter...")

        elif act == '2': # 評分
            # 前端阻擋：沒玩過不讓填
            if not has_played:
                print("\n[!] You have not played this game yet.")
                print("[!] Please download and play at least once to rate.")
                input("Press Enter...")
                return

            try:
                score = int(input("Score (1-5): "))
                if not (1 <= score <= 5): raise ValueError
                comment = input("Comment: ")
                
                self.reset_req()
                send_packet(self.sock, MSG_GAME_RATE_REQ, {
                    "game_name": game_name, "score": score, "comment": comment
                })
                
                print("[*] Submitting review...")
                # [Fix 2] 後端確認：檢查 Server 回應狀態
                rate_resp = self.wait_for_response()
                
                if rate_resp.get("status") == "ok":
                    print("[+] Review submitted successfully!")
                else:
                    print(f"[-] Review Failed: {rate_resp.get('msg', 'Unknown reason')}")
                
                input("Press Enter...")
            except ValueError:
                print("[!] Invalid score.")
                time.sleep(1)
        
        elif act == '3':
            return # 回到上一層 (store_menu)

    def plugin_menu(self):
        print("\n=== Plugin Manager ===")
        self.reset_req()
        send_packet(self.sock, MSG_PLUGIN_LIST_REQ, {})
        resp = self.wait_for_response()
        
        pl = resp.get("plugins", [])
        if not pl:
            print("(No plugins available on server)")
        else:
            print(f"{'Name':<12} {'Description':<30} {'Status'}")
            print("-" * 55)
            for p in pl:
                # [Fix] 檢查「自己的」檔案是否存在
                path = self._get_user_plugin_path()
                installed = os.path.exists(path) if path else False
                
                st = "[Installed]" if installed else "[Not Installed]"
                print(f"{p['name']:<12} {p['desc']:<30} {st}")
            print("-" * 55)
            
        print("\n1. Install RoomChat")
        print("2. Remove RoomChat")
        print("3. Back")
        c = input("Select: ")
        
        if c == '1':
            print("[*] Requesting download...")
            self.reset_req()
            send_packet(self.sock, MSG_PLUGIN_DOWNLOAD_REQ, {"name": "RoomChat"})
            r = self.wait_for_response()
            if r.get("status") == "ok":
                try:
                    with open(self._get_user_plugin_path(), "w", encoding='utf-8') as f:
                        f.write(r['code'])
                    print(f"[+] Plugin 'RoomChat' installed for {self.username}!")
                except Exception as e: print(f"[-] Write failed: {e}")
            else:
                print(f"[-] Install failed: {r.get('msg', 'Server rejected')}")
            input("Press Enter to continue...")
            
        elif c == '2':
            target = self._get_user_plugin_path()
            if target and os.path.exists(target):
                os.remove(target)
                print(f"[+] Plugin 'RoomChat' removed for {self.username}.")
            else:
                print("[-] Plugin 'RoomChat' is NOT installed.")
            input("Press Enter to continue...")
            
        elif c == '3': self.state = STATE_MAIN_MENU
       
    def room_list_menu(self):
        print("\n=== Room List ===")
        # 1. 刷新列表
        self.reset_req()
        send_packet(self.sock, MSG_ROOM_LIST_REQ, {})
        self.wait_for_response()
        
        rooms = self.data_store.get("room_list", [])
        if not rooms:
            print("(No rooms currently open)")
        else:
            # [UX Fix] 增加 Game Name 欄位，排版優化
            print(f"{'ID':<4} {'Room Name':<15} {'Game':<12} {'Players':<8} {'Status'}")
            print("-" * 55)
            for r in rooms:
                # 兼容舊 Server (若沒傳 game_name 則顯示 Unknown)
                gname = r.get('game_name', 'Unknown')
                print(f"{r['id']:<4} {r['name']:<15} {gname:<12} {r['players']:<8} {r['status']}")
            print("-" * 55)
        
        print("\n[Options] 1. Create  2. Join  3. Refresh  4. Back")
        choice = input("Select: ")
        
        if choice == '1':
            self.reset_req()
            send_packet(self.sock, MSG_GAME_LIST_REQ, {})
            self.wait_for_response()
            games = self.data_store.get("game_list", [])
            print("\nSelect Game:")
            valid_games = {}
            for g in games:
                lv = self._get_local_version(g['name'])
                st = "[OK]" if lv == g['version'] else "[Need DL]"
                print(f"ID: {g['id']} | {g['name']} {st}")
                valid_games[g['id']] = g
            gid = input("Game ID: ")
            if not gid.isdigit() or int(gid) not in valid_games: return
            target = valid_games[int(gid)]
            if self._get_local_version(target['name']) != target['version']:
                if input(f"Download {target['name']}? (y/n) ") == 'y':
                    if not self._download_helper(target['name']): return
                else: return
            name = input("Room Name: ")
            self.reset_req()
            send_packet(self.sock, MSG_ROOM_CREATE_REQ, {"room_name": name, "game_id": int(gid)})
            if self.wait_for_response().get("status") != "ok": print("Failed.")
            else: self._activate_chat_plugin()
            
        elif choice == '2':
            rid = input("Room ID: ")
            self.reset_req()
            send_packet(self.sock, MSG_ROOM_JOIN_REQ, {"room_id": rid})
            if self.wait_for_response().get("status") != "ok": print("Failed.")
            else: self._activate_chat_plugin()
            
        elif choice == '3':
            self.response_event.clear()
            send_packet(self.sock, MSG_ROOM_LIST_REQ, {})
            self.wait_for_response()
        elif choice == '4':
            self.state = STATE_MAIN_MENU

    def in_room_menu(self):
        room = self.data_store.get("current_room")
        if not room: 
            self.state = STATE_ROOM_LIST
            return
            
        is_host = (room["host"] == self.username)
        print("\n[Commands] 1. Leave " + ("2. Start Game" if is_host else ""))
        
        # 這裡如果不處裡，遊戲啟動時輸入的 '1' 會被這裡吃到
        choice = input("> ")
        
        if choice == '1':
            send_packet(self.sock, MSG_ROOM_LEAVE_REQ, {})
            self._deactivate_chat_plugin()
            self.state = STATE_ROOM_LIST
            self.reset_req()
            send_packet(self.sock, MSG_ROOM_LIST_REQ, {})
            
        elif choice == '2' and is_host:
            print("[*] Starting game...")
            send_packet(self.sock, MSG_GAME_START_CMD, {})
            # 房主按下開始後，進入等待迴圈，直到遊戲視窗關閉
            # 這樣可以防止房主在 Lobby 亂按
            self.state = STATE_PLAYING # 預先切換狀態
            while self.state == STATE_PLAYING:
                time.sleep(0.5)

    def print_current_room(self):
        r = self.data_store.get("current_room")
        if r:
            print(f"\n{'='*10} Room {r['id']} {'='*10}")
            print(f"Name:    {r['name']}")
            print(f"Game:    {r.get('game_name', 'Unknown')}") # [UX Fix] 顯示遊戲名稱
            print(f"Host:    {r['host']}")
            print(f"Players: {', '.join(r['members'])}")
            print("=" * 30)
   
if __name__ == "__main__":
    client = LobbyClient()
    client.start()