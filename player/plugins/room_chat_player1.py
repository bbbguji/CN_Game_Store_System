
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
