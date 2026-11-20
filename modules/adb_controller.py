import subprocess
import time
import re
import os
from typing import Optional

class ADBController:
    """Điều khiển Emulator (LDPlayer) qua Android Debug Bridge (ADB)"""
    def __init__(self, device_name: str = "127.0.0.1:5555", adb_path: str = "adb"):
        # Hỗ trợ cả trường hợp truyền ngược vị trí
        if device_name.endswith(".exe") or ("\\" in device_name or "/" in device_name):
            adb_path, device_name = device_name, "127.0.0.1:5555"

        # Tự động tìm adb.exe của LDPlayer nếu không có trong PATH
        ld_adb = r"E:\LDPlayer\LDPlayer9\adb.exe"
        if (adb_path == "adb" or not os.path.exists(adb_path)) and os.path.exists(ld_adb):
            self.adb_path = ld_adb
        else:
            self.adb_path = adb_path
            
        self.device = device_name
        self.is_connected = False

    def _run_cmd(self, args: list) -> str:
        cmd = [self.adb_path] + args
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, encoding="utf-8", errors="replace")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            print(f"[ADB] [TIMEOUT] Executing: {' '.join(cmd)}")
            return ""
        except Exception as e:
            print(f"[ADB] [ERROR] Lỗi thực thi ADB: {e}")
            return ""

    def setup_network_redirect(self):
        """Chuyển hướng gói tin 10.0.2.2:10002 -> 127.0.0.1:10002 và reverse port 10002 để APK p10002 thông mạng"""
        try:
            for p in [10001, 10002, 10003]:
                self._run_cmd(["-s", self.device, "reverse", f"tcp:{p}", f"tcp:{p}"])
            self._run_cmd(["-s", self.device, "shell", "su 0 sysctl -w net.ipv4.conf.all.route_localnet=1"])
            self._run_cmd(["-s", self.device, "shell", "su 0 iptables -t nat -F OUTPUT"])
            # FIX: Thêm --dport 10002 để chỉ redirect traffic đến port 10002
            self._run_cmd(["-s", self.device, "shell", "su 0 iptables -t nat -A OUTPUT -p tcp -d 10.0.2.2 --dport 10002 -j REDIRECT"])
        except Exception:
            pass

    def connect(self) -> bool:
        """Kết nối tới Emulator"""
        # Nếu là local emulator (VD: emulator-5576), không cần chạy lệnh connect
        if self.device.startswith("emulator-"):
            self.is_connected = True
            self.setup_network_redirect()
            return True
            
        out = self._run_cmd(["connect", self.device])
        if "connected" in out.lower() or "already" in out.lower():
            self.is_connected = True
            self.setup_network_redirect()
            print(f"[ADB] ✅ Đã kết nối tới giả lập {self.device}")
            return True
        print(f"[ADB] ❌ Không thể kết nối tới giả lập {self.device}. Output: {out}")
        self.is_connected = False
        return False
    def get_screen_size(self) -> tuple:
        """Lấy kích thước màn hình (width, height) từ wm size"""
        out = self._run_cmd(["-s", self.device, "shell", "wm", "size"])
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 540, 960

    def is_app_foreground(self, package_name: str = "com.lori.app") -> bool:
        """Kiểm tra xem ứng dụng game có đang mở sẵn ở màn hình trước mặt (foreground) không"""
        try:
            out = self._run_cmd(["-s", self.device, "shell", "dumpsys", "window"])
            for line in out.splitlines():
                if "mCurrentFocus" in line or "mFocusedApp" in line:
                    if package_name != "com.lori.app":
                        if package_name in line:
                            return True
                    else:
                        candidates = [
                            "cmn.phongvan.truyenky",
                            "vn.cmn.bokinhvan",
                            "com.lori.app"
                        ]
                        if any(c in line for c in candidates):
                            return True
        except Exception:
            pass
        return False

    def open_app(self, package_name: str = "com.lori.app", delay: float = 3.0):
        """Mở ứng dụng game tự động bằng ADB Monkey (bỏ qua nếu game đã mở sẵn ở foreground)"""
        if self.is_app_foreground(package_name):
            print(f"[ADB {self.device}] 📱 Game đã đang mở sẵn trên màn hình, không cần khởi động lại.")
            return

        target_pkg = package_name
        if target_pkg == "com.lori.app":
            # Tự động quét gói PVTK thực tế đang cài trên giả lập
            try:
                pkgs = self._run_cmd(["-s", self.device, "shell", "pm", "list", "packages", "-3"])
                candidates = [
                    "cmn.phongvan.truyenky.v3.ss",
                    "cmn.phongvan.truyenky.v3.p10001",
                    "cmn.phongvan.truyenky.v3.p10002",
                    "cmn.phongvan.truyenky.v3.p10003",
                    "cmn.phongvan.truyenky.v2",
                    "cmn.phongvan.truyenky.v3",
                    "vn.cmn.bokinhvan"
                ]
                for cand in candidates:
                    if cand in pkgs:
                        target_pkg = cand
                        break
            except Exception:
                pass

        print(f"[ADB {self.device}] 🚀 Khởi động Game ({target_pkg})...")
        self._run_cmd(["-s", self.device, "shell", "monkey", "-p", target_pkg, "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(delay)

    def stop_app(self, package_name: str = "com.lori.app", delay: float = 1.0):
        """Đóng ứng dụng game tự động bằng ADB am force-stop"""
        target_pkg = package_name
        if target_pkg == "com.lori.app":
            # Tự động quét gói PVTK thực tế đang cài trên giả lập
            try:
                pkgs = self._run_cmd(["-s", self.device, "shell", "pm", "list", "packages", "-3"])
                candidates = [
                    "cmn.phongvan.truyenky.v3.ss",
                    "cmn.phongvan.truyenky.v3.p10001",
                    "cmn.phongvan.truyenky.v3.p10002",
                    "cmn.phongvan.truyenky.v3.p10003",
                    "cmn.phongvan.truyenky.v2",
                    "cmn.phongvan.truyenky.v3",
                    "vn.cmn.bokinhvan"
                ]
                for cand in candidates:
                    if cand in pkgs:
                        target_pkg = cand
                        break
            except Exception:
                pass
        
        print(f"[ADB {self.device}] 🛑 Đóng Game ({target_pkg})...")
        self._run_cmd(["-s", self.device, "shell", "am", "force-stop", target_pkg])
        time.sleep(delay)

    def tap(self, x: int, y: int, delay: float = 0.5):
        """Click tọa độ (X, Y)"""
        print(f"[ADB {self.device}] 👉 Tap tại ({x}, {y})")
        self._run_cmd(["-s", self.device, "shell", "input", "tap", str(x), str(y)])
        time.sleep(delay)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500, delay: float = 0.5):
        """Vuốt từ (X1, Y1) sang (X2, Y2)"""
        self._run_cmd(["-s", self.device, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])
        time.sleep(delay)

    def input_text(self, text: str, delay: float = 0.5):
        """Nhập văn bản. Tránh dùng ký tự đặc biệt như khoảng trắng vì adb input text không hỗ trợ tốt."""
        # ADB shell input text doesn't like spaces natively, need to replace with %s
        safe_text = text.replace(" ", "%s")
        print(f"[ADB {self.device}] ⌨️ Nhập Text: {text}")
        self._run_cmd(["-s", self.device, "shell", "input", "text", safe_text])
        time.sleep(delay)

    def send_chat_command(self, cmd: str, delay: float = 0.5):
        """Mở khung chat, nhập lệnh và gửi thông qua ADB để Game Client tự xử lý (an toàn 100%)"""
        print(f"[ADB {self.device}] 💬 Nhập lệnh chat: {cmd}")
        # 1. Click biểu tượng Chat (góc trái dưới)
        self.tap(50, 915, delay=1.5)
        # 2. Click vào ô nhập liệu
        self.tap(250, 915, delay=1.0)
        # Xóa chữ cũ (nếu có)
        self.clear_text(backspace_count=15, delay=0.5)
        # 3. Gõ chữ
        self.input_text(cmd, delay=0.5)
        # 4. Ấn Enter / Phím Send
        self._run_cmd(["-s", self.device, "shell", "input", "keyevent", "66"])
        time.sleep(1.0)
        # Ấn nút Gửi trong game (nếu Enter không ăn)
        self.tap(490, 915, delay=1.0)
        # 5. Dùng phím Back (Android) để thoát khung Chat an toàn
        self.press_back(delay=1.0)

    def clear_text(self, backspace_count: int = 30, delay: float = 0.5):
        """Xóa văn bản trong ô hiện tại bằng cách đưa con trỏ về cuối rồi gửi nút Backspace (KEYCODE_DEL = 67) và Forward Delete (112)"""
        print(f"[ADB {self.device}] 🧹 Xóa sạch ô chữ ({backspace_count} lần)")
        events = ["123"] + ["67"] * backspace_count + ["122"] + ["112"] * 10
        self._run_cmd(["-s", self.device, "shell", "input", "keyevent"] + events)
        time.sleep(delay)
        
    def press_back(self, delay: float = 0.3):
        """Nhấn phím Back (KEYCODE_BACK = 4) trên Android để đóng popup hoặc lùi màn hình"""
        self._run_cmd(["-s", self.device, "shell", "input", "keyevent", "4"])
        time.sleep(delay)

    def dismiss_popup(self, delay: float = 0.3):
        """Tự động đóng popup thông báo/lỗi bằng cách nhấn phím Back và click nút mũi tên Back trên popup"""
        self._run_cmd(["-s", self.device, "shell", "input", "keyevent", "4"])
        # Tap phụ trợ vị trí nút Back (mũi tên góc trên bên phải popup 450, 410)
        self._run_cmd(["-s", self.device, "shell", "input", "tap", "450", "410"])
        time.sleep(delay)

