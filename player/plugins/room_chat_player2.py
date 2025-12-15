
import tkinter as tk
import threading
import time

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
        
        self.root.protocol("WM_DELETE_WINDOW", self._close_from_ui)
        
        # [Fix] 啟動 Polling 機制，每 200ms 檢查一次是否該關閉
        # 這樣可以確保 destroy() 是由 UI 執行緒自己呼叫的
        self.root.after(200, self._check_alive)
        self.root.mainloop()

    def _check_alive(self):
        # 這是 UI 執行緒自己在跑
        if not self.running:
            self._safe_destroy()
        else:
            if self.root:
                self.root.after(200, self._check_alive)

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
            # 這裡使用 after 是安全的，因為我們只是放入佇列，不涉及銷毀
            self.root.after(0, lambda: self._append_text(user, msg))
        except: pass

    def _append_text(self, user, msg):
        try:
            self.text_area.config(state='normal')
            self.text_area.insert('end', f"[{user}]: {msg}\n")
            self.text_area.see('end')
            self.text_area.config(state='disabled')
        except: pass

    def _close_from_ui(self):
        # 使用者點擊視窗 X 關閉
        self.running = False
        self._safe_destroy()

    def _close(self):
        # [Fix] 外部 (Client Main Thread) 呼叫關閉
        # 我們只設定 flag，絕對不要在這裡碰 self.root
        self.running = False

    def _safe_destroy(self):
        try:
            if self.root:
                self.root.quit()
                self.root.destroy()
        except: pass
        self.root = None
