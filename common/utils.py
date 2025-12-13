import socket
import struct
import json
import hashlib
import os

# --- Protocol Constants ---
MSG_LOGIN_REQ = 1
MSG_LOGIN_RESP = 2
MSG_REGISTER_REQ = 3
MSG_REGISTER_RESP = 4

MSG_GAME_UPLOAD_INIT = 10
MSG_GAME_UPLOAD_DATA = 11
MSG_GAME_UPLOAD_END = 12
MSG_GAME_REMOVE_REQ = 13 
MSG_GAME_REMOVE_RESP = 14

MSG_GAME_LIST_REQ = 20
MSG_GAME_LIST_RESP = 21
MSG_GAME_DOWNLOAD_REQ = 22
MSG_GAME_DOWNLOAD_INIT = 23
MSG_GAME_DOWNLOAD_DATA = 24
MSG_GAME_DOWNLOAD_END = 25

MSG_ROOM_CREATE_REQ = 30
MSG_ROOM_CREATE_RESP = 31
MSG_ROOM_LIST_REQ = 32
MSG_ROOM_LIST_RESP = 33
MSG_ROOM_JOIN_REQ = 34
MSG_ROOM_JOIN_RESP = 35
MSG_ROOM_LEAVE_REQ = 36
MSG_ROOM_STATUS_UPDATE = 37
MSG_GAME_START_CMD = 38      
MSG_GAME_LAUNCH_EVENT = 39   
MSG_GAME_RATE_REQ = 40       
MSG_GAME_RATE_RESP = 41      

# 查詢開發者擁有的遊戲
MSG_DEV_MY_GAMES_REQ = 50 
MSG_DEV_MY_GAMES_RESP = 51

# 準備檢查流程 (Ready Check)
MSG_READY_CHECK_REQ = 60   # Server -> Client: "你有這款遊戲嗎?"
MSG_READY_CHECK_RESP = 61  # Client -> Server: "有/沒有"
MSG_GAME_START_FAIL = 62   # Server -> Client: "啟動失敗(某人沒檔案)"

# [新增] 強制登出 (重複登入時使用)
MSG_FORCE_LOGOUT = 70

# [Spec P1] 查詢遊戲詳細資料 (含評論)
MSG_GAME_DETAIL_REQ = 80
MSG_GAME_DETAIL_RESP = 81

# [Spec PL1-4] Plugin 相關
MSG_PLUGIN_LIST_REQ = 90
MSG_PLUGIN_LIST_RESP = 91
MSG_PLUGIN_DOWNLOAD_REQ = 92
MSG_PLUGIN_DOWNLOAD_RESP = 93
MSG_ROOM_CHAT = 95  # 聊天訊息封包

def send_packet(sock, msg_type, payload):
    if sock is None: return False
    try:
        if isinstance(payload, dict):
            payload_bytes = json.dumps(payload).encode('utf-8')
        elif isinstance(payload, bytes):
            payload_bytes = payload
        else:
            return False
        
        msg_len = 1 + len(payload_bytes)
        header = struct.pack('>IB', msg_len, msg_type)
        
        sock.sendall(header + payload_bytes)
        return True
    except Exception:
        return False

def recv_packet(sock):
    try:
        raw_len = recv_all(sock, 4)
        if not raw_len: return None, None
        
        msg_len = struct.unpack('>I', raw_len)[0]
        data = recv_all(sock, msg_len)
        if not data: return None, None
        
        msg_type = data[0]
        payload_bytes = data[1:]
        
        try:
            payload = json.loads(payload_bytes.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = payload_bytes
            
        return msg_type, payload
        
    except Exception:
        return None, None

def recv_all(sock, n):
    data = b''
    while len(data) < n:
        try:
            packet = sock.recv(n - len(data))
            if not packet: return None
            data += packet
        except:
            return None
    return data

def calculate_checksum(filepath):
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except:
        return ""