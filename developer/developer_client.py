import socket
import sys
import os
import time
import shutil
import json
import hashlib
import struct

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
try:
    from common.utils import *
except ImportError:
    def send_packet(s, t, p): pass 
    def recv_packet(s): return None, {}
    def calculate_checksum(f): return "dummy"
    MSG_LOGIN_REQ = 1; MSG_GAME_UPLOAD_INIT = 10; MSG_GAME_UPLOAD_DATA = 11; MSG_GAME_UPLOAD_END = 12

# [Config] Default
HOST = '140.113.17.11'
PORT = 12365

# ==========================================
#  Developer Client Logic
# ==========================================
class DeveloperClient:
    def __init__(self):
        self.sock = None
        self.username = None
        self.running = True
        self.is_logged_in = False
        self.base_workspace = "dev_workspace"
        self.server_port = PORT
        if not os.path.exists(self.base_workspace):
            os.makedirs(self.base_workspace)
        self.current_user_dir = None

    def _clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')

    def _print_header(self, title):
        self._clear_screen()
        print("=" * 50)
        print(f"{title:^50}")
        print("=" * 50)

    def _wait_input(self):
        input("\nPress Enter to continue...")

    def connect(self):
        try:
            if self.sock: self.sock.close()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(None)
            self.sock.connect((HOST, self.server_port))
            return True
        except ConnectionRefusedError:
            print(f"[!] Cannot connect to {HOST}:{self.server_port}.")
            inp = input("Enter new port (or Press Enter to retry): ")
            if inp.isdigit():
                self.server_port = int(inp)
            return False
        except: return False

    def _safe_recv(self):
        """
        統一接收封包，並攔截強制登出訊息。
        回傳值: (status_code, data_dict)
        如果遇到登出或斷線，status_code 會是 None 或 False
        """
        try:
            packet = recv_packet(self.sock)
            if not packet:
                self._handle_disconnect()
                return None, None
            
            msg_type, data = packet
            
            # 攔截強制登出
            if msg_type == MSG_FORCE_LOGOUT:
                print(f"\n\n[!] Alert: {data.get('msg', 'Logged out by server')}")
                print("[*] Returning to Auth Menu...")
                self._handle_disconnect()
                return None, None # 中斷後續邏輯
                
            return msg_type, data
        except Exception:
            return None, None

    def _handle_disconnect(self):
        self.is_logged_in = False
        self.username = None
        self.current_user_dir = None
        if self.sock:
            try: self.sock.close()
            except: pass
        self.sock = None

    def start(self):
        if not self.connect():
            print(f"[!] Cannot connect to Server {HOST}:{self.server_port}")
            return
        
        while self.running:
            if not self.is_logged_in:
                self.auth_menu()
            else:
                self.main_menu()
        self.sock.close()

    def auth_menu(self):
        self._print_header("Developer Authentication")
        if self.sock is None:
            print("[*] Connection lost. Reconnecting...")
            if not self.connect():
                print("[!] Reconnection failed. Retrying in 3s...")
                time.sleep(3)
                return # 回到 start loop 重新嘗試
        print("1. Login")
        print("2. Register")
        print("3. Exit")
        choice = input("\nSelect: ")

        if choice == '1':
            user = input("Username: ")
            pwd = input("Password: ")
            send_packet(self.sock, MSG_LOGIN_REQ, {"username": user, "password": pwd, "role": "developer"})
            msg_type, resp = self._safe_recv()
            if resp and resp.get("status") == "ok": # 記得檢查 resp 是否存在
                self.handle_login_success(user)
            else:
                msg = resp.get('msg') if resp else "Connection Error"
                print(f"[-] Login failed: {msg}")
                self._wait_input()
        elif choice == '2':
            user = input("New User: ")
            pwd = input("New Pass: ")
            send_packet(self.sock, MSG_REGISTER_REQ, {"username": user, "password": pwd, "role": "developer"})
            msg_type, resp = self._safe_recv()
            if resp:
                print(f"[*] {resp.get('msg')}")
            else:
                print("[-] Connection Error during registration.")
            self._wait_input()
        elif choice == '3':
            self.running = False

    def handle_login_success(self, user):
        self.username = user
        self.is_logged_in = True
        self.current_user_dir = os.path.join(self.base_workspace, user)
        if not os.path.exists(self.current_user_dir):
            os.makedirs(self.current_user_dir)
        print(f"[+] Welcome, {user}! Workspace: {self.current_user_dir}")
        time.sleep(1)

    def main_menu(self):
        self._print_header(f"Dashboard: {self.username}")
        print(f"Workspace: {self.current_user_dir}\n")
        print("1. Create Template Project (Level A/B/C)")
        print("2. Upload New Game")
        print("3. Update Existing Game")
        print("4. Remove Game")
        print("5. View Reviews")
        print("6. Logout")
        choice = input("\nSelect: ")

        if choice == '1': self.generate_template()
        elif choice == '2': self.upload_process(is_update=False)
        elif choice == '3': self.update_process()
        elif choice == '4': self.remove_process()
        elif choice == '5': self.view_reviews_process()
        elif choice == '6':
            print("[*] Logging out...")
            # 主動登出只需切斷本地狀態，Server 會處理斷線
            self._handle_disconnect()

    def fetch_my_games(self):
        send_packet(self.sock, MSG_DEV_MY_GAMES_REQ, {})
        msg_type, resp = self._safe_recv()
        # 檢查是否接收失敗或被登出 (resp 為 None)
        if not resp: 
            return []
        return resp.get("games", [])

    def generate_template(self):
        self._print_header("Project Generator")
        print("1. CLI Rock-Paper-Scissors (Level A: 2P)")
        print("2. GUI Tic-Tac-Toe (Level B: 2P)")
        print("3. GUI Gomoku (Level C: 4P)") # New Gomoku
        print("4. GUI Battle Snake (Level C: 4P)")
        t = input("\nSelect Template: ")
        
        if t == '1':
            def_name, min_p, max_p, ctype = "RPS_CLI", 2, 2, "CLI"
            def_desc = "[Level A] A CLI-based Rock-Paper-Scissors game. Supports 2 players."
            scode, ccode = TEMPLATE_RPS_SERVER, TEMPLATE_RPS_CLIENT
        elif t == '2':
            def_name, min_p, max_p, ctype = "TTT_GUI", 2, 2, "GUI"
            def_desc = "[Level B] A GUI Tic-Tac-Toe game. Supports mouse interaction."
            scode, ccode = TEMPLATE_TTT_SERVER, TEMPLATE_TTT_CLIENT
        elif t == '3':
            def_name, min_p, max_p, ctype = "Gomoku_Multi", 2, 4, "GUI"
            def_desc = "[Level C] A Multiplayer Gomoku game. Supports 2-4 players synchronized."
            scode, ccode = TEMPLATE_GOMOKU_SERVER, TEMPLATE_GOMOKU_CLIENT
        else:
            def_name, min_p, max_p, ctype = "Snake_Multi", 2, 4, "GUI"
            def_desc = "[Level C] A Multiplayer Battle Snake game. Real-time synchronization."
            scode, ccode = TEMPLATE_SNAKE_SERVER, TEMPLATE_SNAKE_CLIENT

        name = input(f"Game Name [Default: {def_name}]: ") or def_name
        version = input("Initial Version [Default 1.0]: ") or "1.0"
        
        # 讓開發者確認或修改自動產生的描述
        print(f"Default Description: {def_desc}")
        desc = input("Description (Press Enter to use default): ") or def_desc
        
        target_dir = os.path.join(self.current_user_dir, name)
        
        if os.path.exists(target_dir):
            print(f"[!] Warning: Folder '{target_dir}' already exists.")
            if input("Overwrite? (y/n): ").lower() != 'y': return

        if not os.path.exists(target_dir): os.makedirs(target_dir)

        manifest = {
            "name": name,
            "version": version,
            "description": desc,
            "type": ctype,
            "min_players": min_p,
            "max_players": max_p,
            "execution": {
                "server_cmd": ["python", "game_server.py"],
                "client_cmd": ["python", "game_client.py"],
                "args_format": {"connect_ip": "--ip", "connect_port": "--port"}
            }
        }
        with open(os.path.join(target_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=4)
        
        with open(os.path.join(target_dir, "game_server.py"), "w", encoding='utf-8') as f:
            f.write(scode.strip())
        with open(os.path.join(target_dir, "game_client.py"), "w", encoding='utf-8') as f:
            f.write(ccode.strip())

        print(f"\n[+] Created successfully at:\n    {target_dir}")
        self._wait_input()

    def upload_process(self, is_update=False):
        action = "Update" if is_update else "Upload New"
        self._print_header(f"{action} Game")
        
        projects = [d for d in os.listdir(self.current_user_dir) if os.path.isdir(os.path.join(self.current_user_dir, d))]
        if not projects:
            print("[!] No projects found in your workspace.")
            print(f"    ({self.current_user_dir})")
            self._wait_input()
            return

        print(f"Available Projects in {self.current_user_dir}:")
        for idx, p in enumerate(projects):
            print(f"{idx+1}. {p}")
        
        try:
            sel = int(input("\nSelect Project Number: ")) - 1
            path = os.path.join(self.current_user_dir, projects[sel])
        except:
            print("[!] Invalid selection.")
            self._wait_input(); return

        try:
            with open(os.path.join(path, "manifest.json"), 'r') as f:
                manifest = json.load(f)
        except:
            print("[!] manifest.json invalid/missing.")
            self._wait_input(); return

        # 確保 manifest 中有必要欄位
        if not all(k in manifest for k in ["name", "version", "execution"]):
            print("[!] Invalid Manifest: Missing name, version or execution keys.")
            self._wait_input(); return

        print(f"\n[*] Target: {manifest['name']} (v{manifest['version']})")
        if input("Confirm? (y/n): ").lower() != 'y': return

        self._execute_upload(path, manifest['name'], manifest['version'], manifest)
        # 顯示「我的遊戲列表」
        if not is_update:
            print("\n[*] Updating your game list...")
            my_games = self.fetch_my_games()
            print(f"{'No.':<4} {'Name':<20} {'Version'}")
            print("-" * 40)
            for idx, g in enumerate(my_games):
                print(f"{idx+1:<4} {g['name']:<20} {g['version']}")
            print("-" * 40)

        self._wait_input()

    def update_process(self):
        self._print_header("Update Existing Game")
        my_games = self.fetch_my_games()
        if not my_games:
            print("[!] You haven't uploaded any games yet.")
            self._wait_input(); return

        print(f"{'No.':<4} {'Name':<20} {'Version'}")
        print("-" * 40)
        for idx, g in enumerate(my_games):
            print(f"{idx+1:<4} {g['name']:<20} {g['version']}")
        
        print("\n[*] Please select the local project to push as update.")
        self._wait_input()
        self.upload_process(is_update=True)

    def remove_process(self):
        self._print_header("Remove Game")
        my_games = self.fetch_my_games()
        if not my_games: print("[!] No games."); self._wait_input(); return

        for idx, g in enumerate(my_games): print(f"{idx+1:<4} {g['name']}")
        try:
            sel = int(input("\nSelect game to remove: ")) - 1
            name = my_games[sel]['name']
            
            # 增加後果提示
            print(f"\n[!] WARNING: You are about to remove '{name}'.")
            print("    - New players will NOT be able to download it.")
            print("    - Existing rooms cannot start if they reload data.")
            print("    - This action cannot be undone immediately.")
            
            if input(f"Confirm REMOVE '{name}'? (y/n) ").lower() == 'y':
                send_packet(self.sock, MSG_GAME_REMOVE_REQ, {"name": name})
                msg_type, resp = self._safe_recv()
                if resp:
                    print(f"Result: {resp.get('msg')}")
                else:
                    print("[-] Connection Error.")
        except: pass
        self._wait_input()

    def view_reviews_process(self):
        self._print_header("Game Reviews")
        my_games = self.fetch_my_games()
        if not my_games: print("[!] No games."); self._wait_input(); return

        for idx, g in enumerate(my_games): print(f"{idx+1:<4} {g['name']}")
        try:
            sel = int(input("\nSelect game: ")) - 1
            name = my_games[sel]['name']
            
            send_packet(self.sock, MSG_GAME_DETAIL_REQ, {"game_name": name})
            msg_type, resp = self._safe_recv()
            
            if resp:
                print(f"\n=== Reviews for {name} ===")
                print(f"Avg Score: {resp.get('avg_score', 0)}")
                reviews = resp.get('reviews', [])
                if not reviews: print("No reviews.")
                for r in reviews:
                    print(f"- {r['user']}: {r['comment']} ({r['score']}/5)")
            else:
                print("[-] Connection Error.")
        except: pass
        self._wait_input()

    def _execute_upload(self, path, name, version, m):
        zip_base = f"{name}_{version}"
        shutil.make_archive(zip_base, 'zip', path)
        try:
            sz = os.path.getsize(zip_base+".zip")
            ck = calculate_checksum(zip_base+".zip")
            send_packet(self.sock, MSG_GAME_UPLOAD_INIT, {
                "name": name, "version": version, "size": sz, "checksum": ck,
                "description": m.get("description", ""),
                "type": m.get("type", "CLI"),
                "min_players": m.get("min_players", 2),
                "max_players": m.get("max_players", 4)
            })
            msg_type, init_resp = self._safe_recv()
            if init_resp and init_resp.get("status") == "ready":
                print("[*] Uploading data...")
                
                with open(zip_base+".zip", 'rb') as f:
                    while True:
                        c = f.read(4096)
                        if not c: break
                        send_packet(self.sock, MSG_GAME_UPLOAD_DATA, c)
                        time.sleep(0.005)
                
                send_packet(self.sock, MSG_GAME_UPLOAD_END, {})
                
                # [Fix] 再次使用 _safe_recv 接收結果
                msg_type, res = self._safe_recv()
                if res:
                    print(f"[+] Result: {res.get('status')} - {res.get('msg', '')}")
                else:
                    print("[-] Upload finished but connection lost (or logged out).")
            else:
                # 若 init_resp 為 None，代表連線錯誤
                if init_resp is None:
                     print("[-] Connection Error: No response from server.")
                else:
                     msg = init_resp.get('msg', 'Unknown Error')
                     print(f"[-] Server rejected upload: {msg}")
        except Exception as e:
            print(f"[!] Upload Exception: {e}")
        finally:
            if os.path.exists(zip_base+".zip"): os.remove(zip_base+".zip")

# ==========================================
#  [Level A] CLI Rock-Paper-Scissors (RPS)
# ==========================================
TEMPLATE_RPS_SERVER = r"""
import socket, argparse, threading, time
class RPSGame:
    def __init__(self):
        self.p1=None; self.p2=None; self.s1=0; self.s2=0; self.r=1; self.lock=threading.Lock(); self.over=False
    def check(self):
        if self.p1 and self.p2:
            res = 0 if self.p1==self.p2 else 1 if (self.p1=='R' and self.p2=='S') or (self.p1=='S' and self.p2=='P') or (self.p1=='P' and self.p2=='R') else 2
            if res==1: self.s1+=1
            elif res==2: self.s2+=1
            ret = f"Round {self.r}: P1({self.p1}) vs P2({self.p2}) -> " + ("Draw" if res==0 else f"P{res} Wins")
            self.p1=None; self.p2=None; self.r+=1
            if self.s1>=2 or self.s2>=2: self.over=True; ret+=f"\nGAME OVER! Winner: P{1 if self.s1>self.s2 else 2}"
            return ret
        return None
def handle(c, pid, g, o):
    try:
        c.sendall(f"WELCOME P{pid}\n".encode())
        while not g.over:
            c.sendall(f"SCORE {g.s1}:{g.s2} | R{g.r} | INPUT (R/P/S): ".encode())
            d = c.recv(1024).strip().decode().upper()
            if not d: break
            if d in ['R','P','S']:
                with g.lock:
                    if pid==1: g.p1=d
                    else: g.p2=d
                c.sendall(b"Waiting...\n")
                while True:
                    if g.over: break
                    with g.lock:
                        if (pid==1 and not g.p1) or (pid==2 and not g.p2): break
                    time.sleep(0.5)
            if g.over: break
        c.sendall(b"Finished.\n")
    except: pass
    finally: c.close()
def start(port):
    s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.bind(('0.0.0.0', port)); s.listen(2); print(f"[*] RPS on {port}")
    c1,_=s.accept(); c2,_=s.accept(); g=RPSGame()
    def mon():
        while not g.over:
            r=g.check()
            if r: c1.sendall(f"\n>>> {r}\n".encode()); c2.sendall(f"\n>>> {r}\n".encode())
            time.sleep(0.5)
    threading.Thread(target=mon, daemon=True).start()
    threading.Thread(target=handle, args=(c1,1,g,c2)).start(); threading.Thread(target=handle, args=(c2,2,g,c1)).start()
    while not g.over: time.sleep(1)
    time.sleep(1); s.close()
if __name__=="__main__": p=argparse.ArgumentParser(); p.add_argument("--port", type=int); a=p.parse_args(); start(a.port)
"""
TEMPLATE_RPS_CLIENT = r"""
import socket, argparse, sys
def start(ip, port):
    s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.connect((ip, port))
    try:
        while True:
            d=s.recv(1024).decode()
            if not d: break
            if "INPUT" in d:
                sys.stdout.write(d); sys.stdout.flush(); s.sendall(input().encode())
            else: print(d, end='')
    except: pass
    finally: s.close(); input("\nDone...")
if __name__=="__main__": p=argparse.ArgumentParser(); p.add_argument("--ip"); p.add_argument("--port", type=int); a=p.parse_args(); start(a.ip, a.port)
"""

# ==========================================
#  [Level B] GUI Tic-Tac-Toe (Sync Fix)
# ==========================================
TEMPLATE_TTT_SERVER = r"""
import socket, argparse, sys, threading, time, json
class TTT:
    def __init__(self): self.b=[" "]*9; self.t=1; self.o=False; self.w=None; self.l=threading.Lock()
    def mv(self, p, i):
        with self.l:
            if self.o or p!=self.t or self.b[i]!=" ": return False
            self.b[i]="O" if p==1 else "X"
            if any(self.b[a]==self.b[b]==self.b[c]!=" " for a,b,c in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]): self.w=p; self.o=True
            elif " " not in self.b: self.o=True
            else: self.t=2 if p==1 else 1
            return True
    def st(self): return json.dumps({"b":self.b,"t":self.t,"w":self.w,"o":self.o})
def h(c, p, g):
    c.settimeout(0.1) 
    try:
        c.sendall(f"WELCOME {p}\n".encode())
        while True:
            try: c.sendall(f"STATE {g.st()}\n".encode())
            except: break
            try:
                d=c.recv(1024).decode().strip()
                if not d: break
                if d.startswith("MOVE"): g.mv(p, int(d.split()[1]))
            except socket.timeout: pass
            except: break
            time.sleep(0.1)
    except: pass
def start(port):
    s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.bind(('0.0.0.0', port)); s.listen(2); print(f"[*] TTT on {port}")
    c1,_=s.accept(); c2,_=s.accept(); g=TTT()
    threading.Thread(target=h, args=(c1,1,g)).start(); threading.Thread(target=h, args=(c2,2,g)).start()
    while not g.o: time.sleep(1)
    time.sleep(2); s.close()
if __name__=="__main__": p=argparse.ArgumentParser(); p.add_argument("--port", type=int); a=p.parse_args(); start(a.port)
"""

TEMPLATE_TTT_CLIENT = r"""
import socket, argparse, json, threading, tkinter as tk
from tkinter import messagebox

class TTTClient:
    def __init__(self, ip, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((ip, port))
        self.pid = int(self.sock.recv(1024).decode().split()[1])
        
        self.root = tk.Tk()
        self.root.title(f"TicTacToe - Player {self.pid} ({'O' if self.pid==1 else 'X'})")
        self.buttons = []
        for i in range(9):
            btn = tk.Button(self.root, text=" ", font=('Arial', 20), width=5, height=2,
                            command=lambda x=i: self.click(x))
            btn.grid(row=i//3, column=i%3)
            self.buttons.append(btn)
        
        self.lbl = tk.Label(self.root, text="Waiting..."); self.lbl.grid(row=3, column=0, columnspan=3)
        self.shown = False # [Fix] Flag to prevent multiple popups
        
        threading.Thread(target=self.loop, daemon=True).start()
        self.root.mainloop()

    def click(self, idx):
        self.sock.sendall(f"MOVE {idx}\n".encode())

    def loop(self):
        buf = ""
        while True:
            try:
                d = self.sock.recv(4096).decode()
                if not d: break
                buf += d
                while "\n" in buf:
                    l, buf = buf.split("\n", 1)
                    if l.startswith("STATE"):
                        self.root.after(0, self.update, json.loads(l[6:]))
            except: break
        self.root.quit()

    def update(self, st):
        for i, v in enumerate(st['b']): self.buttons[i].config(text=v)
        if st['o']:
            if not self.shown: # [Fix] Check if already shown
                self.shown = True
                t = "Draw" if not st['w'] else "You Win!" if st['w']==self.pid else "You Lose!"
                self.lbl.config(text=t); messagebox.showinfo("Game Over", t)
        else:
            self.lbl.config(text="Your Turn" if st['t']==self.pid else "Opponent's Turn")

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--ip"); p.add_argument("--port", type=int); a = p.parse_args()
    TTTClient(a.ip, a.port)
"""

# ==========================================
#  [Level B/C] Gomoku (Bug Fix: Coordinate Parsing)
# ==========================================
TEMPLATE_GOMOKU_SERVER = r"""
import socket, argparse, sys, threading, time, json
class GomokuServer:
    def __init__(self):
        self.size=15; self.board={}; self.turn_order=[]; self.idx=0; self.lock=threading.Lock(); self.winner=None; self.game_over=False
    def add(self, p):
        with self.lock: 
            if p not in self.turn_order: self.turn_order.append(p)
    def curr(self): return self.turn_order[self.idx%len(self.turn_order)] if self.turn_order else -1
    def mv(self, p, x, y):
        with self.lock:
            if self.game_over or p!=self.curr() or not (0<=x<15 and 0<=y<15) or (x,y) in self.board: return False
            self.board[(x,y)]=p
            if self.chk(p,x,y): self.winner=p; self.game_over=True
            self.idx+=1; return True
    def chk(self, p, x, y):
        for dx,dy in [(1,0),(0,1),(1,1),(1,-1)]:
            c=1
            for d in [1,-1]:
                nx,ny=x,y
                while True:
                    nx,ny=nx+dx*d,ny+dy*d
                    if self.board.get((nx,ny))==p: c+=1
                    else: break
            if c>=5: return True
        return False
    def st(self): return json.dumps({"b":{f"{k[0]},{k[1]}":v for k,v in self.board.items()}, "t":self.curr(), "w":self.winner, "o":self.game_over})
def h(c, p, g, all_c):
    c.settimeout(0.1) 
    try:
        g.add(p); c.sendall(f"WELCOME {p}\n".encode())
        while True:
            try: c.sendall(f"STATE {g.st()}\n".encode())
            except: break
            try:
                d=c.recv(1024).strip().decode()
                if not d: break
                if d.startswith("MOVE"): 
                    _,x,y=d.split(); g.mv(p, int(x), int(y))
            except socket.timeout: pass
            except: break
            time.sleep(0.1)
    except: pass
    finally: c.close()
def start(port):
    s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0', port)); s.listen(5); print(f"[*] Gomoku on {port}")
    g=GomokuServer(); cs=[]
    def acc():
        pid=1
        while not g.game_over:
            try: c,_=s.accept(); cs.append(c); threading.Thread(target=h, args=(c,pid,g,cs), daemon=True).start(); print(f"[+] P{pid}"); pid+=1
            except: break
    threading.Thread(target=acc, daemon=True).start()
    while not g.game_over: time.sleep(1)
    time.sleep(2); s.close()
if __name__=="__main__": p=argparse.ArgumentParser(); p.add_argument("--port", type=int); a=p.parse_args(); start(a.port)
"""

TEMPLATE_GOMOKU_CLIENT = r"""
import socket, argparse, sys, time, json, threading, tkinter as tk
from tkinter import messagebox
class GomokuGUI:
    def __init__(self, ip, port):
        self.s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); self.s.connect((ip, port))
        self.pid=int(self.s.recv(1024).decode().split()[1])
        self.r=tk.Tk(); self.r.title(f"Gomoku P{self.pid}"); self.sz=15; self.cs=30
        self.c=tk.Canvas(self.r, width=self.sz*self.cs, height=self.sz*self.cs, bg="#EDB97D"); self.c.pack()
        self.c.bind("<Button-1>", self.clk); self.l=tk.Label(self.r, text="Wait"); self.l.pack()
        self.cols={1:"black",2:"white",3:"red",4:"blue"}; self.ov=False
        self.shown = False 
        threading.Thread(target=self.loop, daemon=True).start(); self.r.mainloop()
    def draw(self, bd):
        self.c.delete("all")
        for i in range(self.sz):
            self.c.create_line(i*self.cs+15,15,i*self.cs+15,self.sz*self.cs-15)
            self.c.create_line(15,i*self.cs+15,self.sz*self.cs-15,i*self.cs+15)
        for k,p in bd.items():
            # [Fix] Parse key properly
            if ',' in k: x,y=map(int,k.split(","))
            else: continue
            cx,cy=x*self.cs+15,y*self.cs+15
            self.c.create_oval(cx-10,cy-10,cx+10,cy+10, fill=self.cols.get(p,"gray"))
    def clk(self, e):
        if self.ov: return
        self.s.sendall(f"MOVE {round((e.x-15)/self.cs)} {round((e.y-15)/self.cs)}\n".encode())
    def loop(self):
        b=""
        while True:
            try:
                d=self.s.recv(4096).decode(); 
                if not d: break
                b+=d
                while "\n" in b: l,b=b.split("\n",1); self.r.after(0, self.upd, json.loads(l[6:])) if l.startswith("STATE") else None
            except: break
        self.r.quit()
    def upd(self, s):
        self.draw(s["b"]) 
        if s["w"]: 
            self.ov=True
            if not self.shown: 
                self.shown = True
                txt="VICTORY!" if s["w"]==self.pid else f"P{s['w']} Wins!"
                self.l.config(text=txt, fg="red"); messagebox.showinfo("End", txt)
        else: self.l.config(text="YOUR TURN" if s["t"]==self.pid else f"P{s['t']}'s Turn", fg="black")
if __name__=="__main__": p=argparse.ArgumentParser(); p.add_argument("--ip"); p.add_argument("--port", type=int); a=p.parse_args(); GomokuGUI(a.ip, a.port)
"""

# ==========================================
#  [Level C] Snake (Fix: End Logic & Popup)
# ==========================================
TEMPLATE_SNAKE_SERVER = r"""
import socket, argparse, sys, threading, time, random, json
W,H=30,30
class G:
    def __init__(self): self.s={}; self.d={}; self.f=(15,15); self.l=threading.Lock(); self.nid=1; self.o=False; self.w=None
    def add(self):
        with self.l: i=self.nid; self.nid+=1; self.s[i]=[(random.randint(5,W-5), random.randint(5,H-5))]; self.d[i]=(0,0); return i
    def dir(self, i, dx, dy):
        with self.l: 
            if i in self.d: self.d[i]=(dx, dy)
    def tick(self):
        with self.l:
            if self.o: return
            for i, b in list(self.s.items()):
                dx, dy = self.d[i]
                if dx==0 and dy==0: continue
                nx, ny = b[0][0]+dx, b[0][1]+dy
                crash = not (0<=nx<W and 0<=ny<H)
                for ob in self.s.values(): 
                    if (nx,ny) in ob: crash=True
                if crash: del self.s[i]; del self.d[i]; continue
                b.insert(0, (nx,ny))
                if (nx,ny)==self.f: self.f=(random.randint(0,W-1), random.randint(0,H-1))
                else: b.pop()
            
            # [Fix] Game Over Logic: 0 snakes (draw) or 1 snake left (winner)
            # Only trigger if game "started" (nid > 2 means at least 2 players joined attempt)
            # Or if it's solo test (nid==2) and len==0
            if (self.nid > 2 and len(self.s) <= 1) or (self.nid==2 and len(self.s)==0):
                self.o = True
                if len(self.s) == 1: self.w = list(self.s.keys())[0] # Winner
                else: self.w = 0 # Draw (everyone died)

    def st(self): return json.dumps({"s":self.s, "f":self.f, "o":self.o, "w":self.w})
def h(c, i, g):
    while True:
        try:
            d=c.recv(128).decode().strip().upper()
            if not d: break
            v={'W':(0,-1),'S':(0,1),'A':(-1,0),'D':(1,0)}
            if d in v: g.dir(i, *v[d])
        except: break
def start(port):
    s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0', port)); s.listen(5); print(f"[*] Snake on {port}")
    g=G(); cs=[]
    def acc():
        while True:
            try: c,_=s.accept(); i=g.add(); c.sendall(f"WELCOME {i}\n".encode()); cs.append(c); threading.Thread(target=h, args=(c,i,g), daemon=True).start(); print(f"[+] P{i}")
            except: break
    threading.Thread(target=acc, daemon=True).start()
    while True:
        time.sleep(0.15); g.tick(); m=f"STATE {g.st()}\n".encode()
        for c in cs: 
            try: c.sendall(m)
            except: pass
        if g.o: break # End loop
    # Final state
    time.sleep(0.5)
    for c in cs: 
        try: c.sendall(f"STATE {g.st()}\n".encode())
        except: pass
    time.sleep(2); s.close()

if __name__=="__main__": p=argparse.ArgumentParser(); p.add_argument("--port", type=int); a=p.parse_args(); start(a.port)
"""
TEMPLATE_SNAKE_CLIENT = r"""
import socket, argparse, sys, time, json, threading, tkinter as tk
from tkinter import messagebox
class C:
    def __init__(self, ip, port):
        self.s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); self.s.connect((ip, port))
        self.pid=int(self.s.recv(1024).decode().split()[1])
        self.r=tk.Tk(); self.r.title(f"Snake P{self.pid}"); self.c=tk.Canvas(self.r, width=600, height=600, bg="black"); self.c.pack()
        self.r.bind("<Key>", lambda e: self.s.sendall(e.keysym.upper().encode()) if e.keysym.upper() in "WASD" else None)
        self.col={1:"green",2:"yellow",3:"cyan",4:"magenta"}
        self.shown = False # [Fix] Flag
        threading.Thread(target=self.loop, daemon=True).start(); self.r.mainloop()
    def loop(self):
        b=""
        while True:
            try:
                d=self.s.recv(4096).decode(); 
                if not d: break
                b+=d
                while "\n" in b: l,b=b.split("\n",1); self.r.after(0, self.draw, json.loads(l[6:])) if l.startswith("STATE") else None
            except: break
        self.r.quit()
    def draw(self, st):
        self.c.delete("all"); fx,fy=st['f']; self.c.create_oval(fx*20, fy*20, (fx+1)*20, (fy+1)*20, fill="red")
        for i, b in st['s'].items():
            col=self.col.get(int(i), "white")
            for x, y in b: self.c.create_rectangle(x*20, y*20, (x+1)*20, (y+1)*20, fill=col)
        
        if st.get('o'): # Game Over
            if not self.shown:
                self.shown = True
                w = st.get('w')
                txt = "VICTORY!" if w == self.pid else "GAME OVER" if w else "DRAW (All Dead)"
                self.c.create_text(300, 300, text=txt, fill="white", font=("Arial", 30))
                messagebox.showinfo("Result", txt)

if __name__=="__main__": p=argparse.ArgumentParser(); p.add_argument("--ip"); p.add_argument("--port", type=int); a=p.parse_args(); C(a.ip, a.port)
"""

if __name__ == "__main__":
    DeveloperClient().start()