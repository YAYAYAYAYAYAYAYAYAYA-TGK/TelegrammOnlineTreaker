import asyncio
import sqlite3
import threading
import socket
import winsound
from datetime import datetime
from contextlib import closing
from tkinter import messagebox

try:
    import socks
except ImportError:
    socks = None

import customtkinter as ctk
from telethon import TelegramClient, connection
from telethon.tl.types import UserStatusOnline, UserStatusOffline

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class MultiTracker:
    def __init__(self, api_id, api_hash, phone, proxy=None, status_callback=None, log_callback=None):
        # Принудительно IPv4
        try:
            socket.has_ipv6 = False
        except:
            pass

        self.client = TelegramClient(
            'multi_tracker_session', api_id, api_hash,
            proxy=proxy,
            connection=connection.ConnectionTcpFull,
            device_model="PC",
            system_version="Windows 10",
            app_version="1.0",
            lang_code="ru",
            system_lang_code="ru",
            connection_retries=5,
            timeout=45
        )
        self.phone = phone
        self.targets = {}
        self.db_lock = threading.Lock()
        self.status_callback = status_callback
        self.log_callback = log_callback
        self._running = False

    def add_target(self, username):
        if username not in self.targets:
            self.targets[username] = {
                'last_status': None,
                'online_since': None,
                'user_obj': None,
                'history': []
            }

    def remove_target(self, username):
        self.targets.pop(username, None)

    async def start(self):
        self._running = True
        await self.client.start(self.phone)
        for username in list(self.targets.keys()):
            try:
                user = await self.client.get_entity(username)
                self.targets[username]['user_obj'] = user
                if self.log_callback:
                    self.log_callback(f"Найден пользователь: {user.first_name} (@{username})")
            except Exception as e:
                if self.log_callback:
                    self.log_callback(f"Не найден @{username}: {e}")
                self.targets.pop(username, None)
        if not self.targets:
            if self.log_callback:
                self.log_callback("Нет пользователей для отслеживания!")
            self._running = False
            if self.status_callback:
                self.status_callback("__done__", "", None)
            return
        if self.log_callback:
            self.log_callback(f"Отслеживание {len(self.targets)} пользователей...")
        await self.tracking_loop()

    async def stop(self):
        self._running = False

    async def tracking_loop(self):
        while self._running:
            now = datetime.now()
            for username, data in list(self.targets.items()):
                if not data['user_obj']:
                    continue
                try:
                    user = data['user_obj']
                    full = await self.client.get_entity(user.id)
                    if isinstance(full.status, UserStatusOnline):
                        cur = "ОНЛАЙН"
                        if data['last_status'] != "ОНЛАЙН":
                            data['online_since'] = now
                            self.log_event(username, "ВОШЕЛ В СЕТЬ", now)
                            if self.log_callback:
                                self.log_callback(f"🟢 {user.first_name} ВОШЕЛ В СЕТЬ!")
                    elif isinstance(full.status, UserStatusOffline):
                        cur = "ОФФЛАЙН"
                        if data['last_status'] == "ОНЛАЙН" and data['online_since']:
                            dur = now - data['online_since']
                            m, s = divmod(int(dur.total_seconds()), 60)
                            self.log_event(username, "ВЫШЕЛ ИЗ СЕТИ", now, f"{m} мин {s} сек")
                            if self.log_callback:
                                self.log_callback(f"🔴 {user.first_name} ВЫШЕЛ! Был онлайн: {m} мин {s} сек")
                            data['online_since'] = None
                    else:
                        cur = "НЕИЗВЕСТНО"
                    data['last_status'] = cur
                except Exception as e:
                    if self.log_callback:
                        self.log_callback(f"Ошибка @{username}: {e}")
            if self.status_callback:
                self.status_callback("__update__", "", None)
            await asyncio.sleep(10)

    def log_event(self, username, event, timestamp, details=""):
        with self.db_lock:
            try:
                with closing(sqlite3.connect('multi_history.db', timeout=5)) as conn:
                    c = conn.cursor()
                    c.execute("""CREATE TABLE IF NOT EXISTS multi_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT, event TEXT, timestamp DATETIME, details TEXT
                    )""")
                    c.execute("INSERT INTO multi_history (username, event, timestamp, details) VALUES (?,?,?,?)",
                              (username, event, timestamp.isoformat(), details))
                    conn.commit()
                    self.targets[username]['history'].append(dict(event=event, time=timestamp, details=details))
                    if len(self.targets[username]['history']) > 10:
                        self.targets[username]['history'].pop(0)
            except Exception:
                pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Online Tracker")
        self.geometry("1100x700")
        self.minsize(900, 600)

        self.tracker = None
        self.loop = None
        self.loop_thread = None
        self.running = False
        self.users = []
        self.notifications_enabled = True

        self.setup_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=300, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(sidebar, text="Telegram\nOnline Tracker",
                      font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, padx=20, pady=(20, 15))

        sf = ctk.CTkFrame(sidebar)
        sf.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(sf, text="API Настройки", font=ctk.CTkFont(weight="bold")).pack(pady=(5, 5))

        for label, key, default in [
            ("API ID:", "api_id", ""),
            ("API Hash:", "api_hash", ""),
            ("Телефон:", "phone", ""),
        ]:
            ctk.CTkLabel(sf, text=label).pack(anchor="w", padx=10)
            e = ctk.CTkEntry(sf)
            e.pack(fill="x", padx=10, pady=(0, 5))
            e.insert(0, default)
            setattr(self, f"{key}_entry", e)

        ctk.CTkLabel(sf, text="Прокси (если нужно):", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 5))
        pf = ctk.CTkFrame(sf, fg_color="transparent")
        pf.pack(fill="x", padx=10, pady=(0, 5))
        pf.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(pf, text="Тип:").grid(row=0, column=0, padx=(0, 5))
        self.proxy_type = ctk.CTkComboBox(pf, values=["", "socks5", "MTProto"], width=120)
        self.proxy_type.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(pf, text="Хост:").grid(row=1, column=0, padx=(0, 5), pady=(3, 0))
        self.proxy_host = ctk.CTkEntry(pf, placeholder_text="host")
        self.proxy_host.grid(row=1, column=1, sticky="ew", pady=(3, 0))
        ctk.CTkLabel(pf, text="Порт:").grid(row=2, column=0, padx=(0, 5), pady=(3, 0))
        self.proxy_port = ctk.CTkEntry(pf, placeholder_text="port")
        self.proxy_port.grid(row=2, column=1, sticky="ew", pady=(3, 0))
        ctk.CTkLabel(pf, text="Логин:").grid(row=3, column=0, padx=(0, 5), pady=(3, 0))
        self.proxy_user = ctk.CTkEntry(pf, placeholder_text="(опц.)")
        self.proxy_user.grid(row=3, column=1, sticky="ew", pady=(3, 0))
        ctk.CTkLabel(pf, text="Пароль:").grid(row=4, column=0, padx=(0, 5), pady=(3, 0))
        self.proxy_pass = ctk.CTkEntry(pf, placeholder_text="(опц.)", show="*")
        self.proxy_pass.grid(row=4, column=1, sticky="ew", pady=(3, 0))

        uf = ctk.CTkFrame(sidebar)
        uf.grid(row=2, column=0, padx=10, pady=5, sticky="ew")
        uf.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(uf, text="Цели отслеживания", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, pady=(5, 5))

        addf = ctk.CTkFrame(uf, fg_color="transparent")
        addf.grid(row=1, column=0, padx=10, sticky="ew")
        addf.grid_columnconfigure(0, weight=1)

        self.entry_username = ctk.CTkEntry(addf, placeholder_text="@username")
        self.entry_username.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.entry_username.bind("<Return>", lambda e: self.add_user())

        ctk.CTkButton(addf, text="+", width=40, command=self.add_user).grid(row=0, column=1)

        self.users_frame = ctk.CTkScrollableFrame(uf, height=150)
        self.users_frame.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="ew")

        cf = ctk.CTkFrame(sidebar)
        cf.grid(row=4, column=0, padx=10, pady=5, sticky="ew")

        self.btn_start = ctk.CTkButton(cf, text="Старт", fg_color="#2e7d32",
                                       hover_color="#1b5e20", command=self.start_tracking)
        self.btn_start.pack(fill="x", padx=10, pady=5)

        self.btn_stop = ctk.CTkButton(cf, text="Стоп", fg_color="#c62828",
                                      hover_color="#b71c1c", command=self.stop_tracking,
                                      state="disabled")
        self.btn_stop.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(cf, text="Уведомления", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 0))
        nf = ctk.CTkFrame(cf, fg_color="transparent")
        nf.pack(fill="x", padx=10, pady=5)
        self.btn_notif_toggle = ctk.CTkButton(nf, text="🔔 Вкл", fg_color="#2e7d32",
                                              hover_color="#1b5e20", command=self.toggle_notifications)
        self.btn_notif_toggle.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(nf, text="🔊 Тест", command=self.play_sound).pack(side="right", fill="x", expand=True)

        main = ctk.CTkFrame(self, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=2)
        main.grid_rowconfigure(1, weight=1)

        sframe = ctk.CTkFrame(main)
        sframe.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="nsew")
        sframe.grid_columnconfigure(0, weight=1)
        sframe.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(sframe, text="Статус пользователей",
                      font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, pady=(5, 5))
        self.txt_status = ctk.CTkTextbox(sframe, state="disabled", font=ctk.CTkFont(size=14))
        self.txt_status.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")

        lframe = ctk.CTkFrame(main)
        lframe.grid(row=1, column=0, padx=10, pady=(5, 10), sticky="nsew")
        lframe.grid_columnconfigure(0, weight=1)
        lframe.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(lframe, text="Лог событий",
                      font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, pady=(5, 5))
        self.txt_log = ctk.CTkTextbox(lframe, state="disabled", font=ctk.CTkFont(size=13))
        self.txt_log.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")

    def add_user(self):
        name = self.entry_username.get().strip()
        if not name:
            return
        if not name.startswith("@"):
            name = "@" + name
        if name in self.users:
            return
        self.users.append(name)
        self.entry_username.delete(0, "end")
        self.refresh_users_list()
        self.append_log(f"Добавлен: {name}")

    def remove_user(self, username):
        if username in self.users:
            self.users.remove(username)
            self.refresh_users_list()
            self.append_log(f"Удален: {username}")

    def refresh_users_list(self):
        for w in self.users_frame.winfo_children():
            w.destroy()
        for uname in self.users:
            row = ctk.CTkFrame(self.users_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=uname).pack(side="left", padx=5)
            ctk.CTkButton(row, text="✕", width=30, fg_color="transparent",
                          hover_color="#c62828",
                          command=lambda u=uname: self.remove_user(u)).pack(side="right")

    def append_log(self, msg):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def refresh_status(self):
        self.txt_status.configure(state="normal")
        self.txt_status.delete("0.0", "end")
        if self.tracker and self.tracker.targets:
            for uname, data in self.tracker.targets.items():
                user = data.get('user_obj')
                name = user.first_name if user else uname
                if data['last_status'] == "ОНЛАЙН" and data['online_since']:
                    dur = datetime.now() - data['online_since']
                    m, s = divmod(int(dur.total_seconds()), 60)
                    self.txt_status.insert("end", f"  {name} ({uname}): онлайн {m:02d}:{s:02d}\n")
                else:
                    self.txt_status.insert("end", f"  {name} ({uname}): оффлайн\n")
        else:
            self.txt_status.insert("end", "Нет активных целей\n")
        self.txt_status.configure(state="disabled")

    def play_sound(self):
        import os
        import sys
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(__file__)
        for name in ("notification.mp3", "notification.wav"):
            path = os.path.join(base, name)
            if not os.path.exists(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext == ".wav":
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif ext == ".mp3":
                try:
                    from playsound import playsound
                    threading.Thread(target=playsound, args=(path,), daemon=True).start()
                except ImportError:
                    try:
                        import pygame
                        pygame.mixer.init()
                        pygame.mixer.music.load(path)
                        pygame.mixer.music.play()
                    except ImportError:
                        pass
            return
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)

    def toggle_notifications(self):
        self.notifications_enabled = not self.notifications_enabled
        if self.notifications_enabled:
            self.btn_notif_toggle.configure(text="🔔 Вкл", fg_color="#2e7d32", hover_color="#1b5e20")
        else:
            self.btn_notif_toggle.configure(text="🔕 Выкл", fg_color="#616161", hover_color="#424242")

    def show_notification(self, title, message):
        if self.notifications_enabled:
            self.play_sound()

    def on_status_update(self, _, __, ___):
        self.after(0, self.refresh_status)

    def on_log_message(self, msg):
        self.after(0, self.append_log, msg)
        if "ВОШЕЛ В СЕТЬ" in msg:
            name = msg.replace("🟢 ", "").replace(" ВОШЕЛ В СЕТЬ!", "")
            self.after(0, self.show_notification, "Пользователь онлайн", f"{name} зашёл в Telegram!")

    def start_tracking(self):
        api_id = self.api_id_entry.get().strip()
        api_hash = self.api_hash_entry.get().strip()
        phone = self.phone_entry.get().strip()

        if not all([api_id, api_hash, phone]):
            messagebox.showerror("Ошибка", "Заполните все API настройки")
            return
        if not self.users:
            messagebox.showerror("Ошибка", "Добавьте хотя бы одного пользователя")
            return

        try:
            api_id = int(api_id)
        except ValueError:
            messagebox.showerror("Ошибка", "API ID должен быть числом")
            return

        proxy = self.build_proxy()

        self.tracker = MultiTracker(api_id, api_hash, phone, proxy=proxy,
                                    status_callback=self.on_status_update,
                                    log_callback=self.on_log_message)
        for u in self.users:
            self.tracker.add_target(u)

        self.running = True
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()
        asyncio.run_coroutine_threadsafe(self.tracker.start(), self.loop)

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.append_log("Отслеживание запущено")

    def build_proxy(self):
        ptype = self.proxy_type.get()
        host = self.proxy_host.get().strip()
        port = self.proxy_port.get().strip()
        if not ptype or not host or not port:
            return None
        try:
            port = int(port)
        except ValueError:
            return None
        if ptype == "socks5":
            if socks is None:
                messagebox.showerror("Ошибка", "Установи pysocks: pip install pysocks")
                return None
            user = self.proxy_user.get().strip() or None
            pwd = self.proxy_pass.get().strip() or None
            if user and pwd:
                return (socks.SOCKS5, host, port, True, user, pwd)
            return (socks.SOCKS5, host, port)
        elif ptype == "MTProto":
            secret = self.proxy_pass.get().strip()
            if secret:
                return (host, port, secret)
        return None

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop_tracking(self):
        if self.tracker and self.running:
            asyncio.run_coroutine_threadsafe(self.tracker.stop(), self.loop)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.running = False
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.append_log("Отслеживание остановлено")

    def on_closing(self):
        if self.running:
            self.stop_tracking()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
