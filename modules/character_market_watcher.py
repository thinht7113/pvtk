"""
Sở Giao Dịch PVTK - Multi-Worker Market Watcher & Auto Lucky Money Snatcher
Hỗ trợ đa tài khoản (tối đa 4 tài khoản từ accountsNV.txt):
- Acc 1: Giám sát Sở Giao Dịch Nhân Vật (chu kỳ 30s) + Đoạt Lì Xì.
- Acc 2: Giám sát Sở Giao Dịch Vật Phẩm (chu kỳ 30s - Săn đồ rẻ) + Đoạt Lì Xì.
- Acc 3 & 4: Treo game trực tuyến (Keep-Alive) + Đoạt Lì Xì.
- Cả 4 Acc: Sử dụng IP chính trực tiếp (Tốc độ tối đa) và ĐỒNG LOẠT ĐOẠT LÌ XÌ khi có người phát.
- CHỈ HIỂN THỊ KHI ĐOẠT THÀNH CÔNG: Ghi rõ tài khoản/nhân vật nào giật được bao nhiêu vàng.
"""

import sys
import os
import re
import time
import json
import base64
import random
import subprocess
import threading
import select
from typing import Dict, Any, Optional, Tuple, List, Set
import html
import queue
from core.tele_notifier import send_telegram_html_safe

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)

        try:
            kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass

        try:
            h_in = kernel32.GetStdHandle(-10)
            mode_in = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h_in, ctypes.byref(mode_in)):
                new_mode_in = (mode_in.value & ~0x0040 & ~0x0010) | 0x0080
                kernel32.SetConsoleMode(h_in, new_mode_in)
        except Exception:
            pass

        for h_id in (-11, -12):
            h = kernel32.GetStdHandle(h_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0004 | 0x0008)

        h_con = kernel32.CreateFileW("CONOUT$", 0x40000000 | 0x80000000, 1 | 2, None, 3, 0, None)
        if h_con != -1:
            class COORD(ctypes.Structure):
                _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]
            class CONSOLE_FONT_INFOEX(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.ULONG),
                    ("nFont", wintypes.DWORD),
                    ("dwFontSize", COORD),
                    ("FontFamily", wintypes.UINT),
                    ("FontWeight", wintypes.UINT),
                    ("FaceName", ctypes.c_wchar * 32)
                ]
            for font_name in ["Cascadia Mono", "Consolas", "Lucida Console"]:
                new_font = CONSOLE_FONT_INFOEX()
                new_font.cbSize = ctypes.sizeof(CONSOLE_FONT_INFOEX)
                new_font.nFont = 0
                new_font.dwFontSize = COORD(0, 16)
                new_font.FontFamily = 54
                new_font.FontWeight = 400
                new_font.FaceName = font_name
                if kernel32.SetCurrentConsoleFontEx(h_con, False, ctypes.byref(new_font)):
                    break
            kernel32.CloseHandle(h_con)
    except Exception:
        pass

from datetime import datetime, timezone, timedelta

from core.config import settings
from core.message import Message
from core.network import PVTKConnection
import core.protocol as proto
from modules.auth import AuthManager, CharacterInfo
from modules.exp_ticket_sender import Item
from modules.item_price_tracker import ItemPriceTracker, normalize_item_name, infer_enhance_tier
from modules.free_proxy import FreeProxyManager
from modules.teleport import MapTeleporter, QUANG_TRUONG_CHILD_MAP_ID
from modules.daily_history import DailyHistoryManager
from modules.lottery_runner import LotteryManager
from modules.quiz_solver import QuizManager

VN_TZ = timezone(timedelta(hours=7))

JOB_NAMES = {
    1: "Hiệp Khách",
    2: "Tu Chân",
    3: "Chiến Sĩ",
    4: "Pháp Sư",
    5: "Hiền Giả",
    6: "Võ Thánh",
    10: "Vua Súng"
}

GRADE_COLORS = {
    0: {"name": "Thường", "icon": "⚪", "ansi": "\033[97m"},
    1: {"name": "Tinh chế", "icon": "🟢", "ansi": "\033[92m"},
    2: {"name": "Hiếm có", "icon": "🔵", "ansi": "\033[96m"},
    3: {"name": "Sử thi", "icon": "🟠", "ansi": "\033[38;5;208m"},
    4: {"name": "Truyền thuyết", "icon": "🟣", "ansi": "\033[95m"},
}

CHAT_OPCODES = {
    getattr(proto, 'CG_CHAT_MAP', 0x34C6),
    getattr(proto, 'CG_CHAT_WORLD', 0x34C7),
    getattr(proto, 'CG_CHAT_COUNTRY', 0x34C8),
    getattr(proto, 'CG_CHAT_TEAM', 0x34C9),
    getattr(proto, 'CG_CHAT_PM', 0x34CA),
    getattr(proto, 'CG_CHAT_BROADCAST', 0x34CB),
    getattr(proto, 'CG_CHAT_OTHER', 0x34EE),
    getattr(proto, 'CG_CHAT_NOTICE', 0x34F2),
}


def get_grade_info(grade: int) -> Dict[str, str]:
    return GRADE_COLORS.get(grade, {"name": f"Phẩm chất {grade}", "icon": "⚪", "ansi": "\033[97m"})


def format_colored_item_name(name: str, grade: int) -> str:
    info = get_grade_info(grade)
    return f"{info['ansi']}{name}\033[0m"


def get_sgd_broad_category(item_type: int) -> str:
    """Xác định thể loại hiển thị theo đúng bảng thể loại Sở Giao Dịch của PVTK."""
    if item_type == 13:
        return "Kiếm"
    if item_type == 36:
        return "Súng"
    if item_type in (14, 15, 16):
        return "Đao"
    if item_type == 19:
        return "Trượng"
    if item_type in (21, 22, 23):
        return "Cung"
    if item_type == 18:
        return "Nặng"
    if item_type == 20:
        return "Trường Thương"
    if item_type == 34:
        return "PhápKhí"
    if 0 <= item_type <= 7:
        return "Hộ giáp"
    if item_type in (8, 9, 10):
        return "SứcPhẩm"
    if item_type in (11, 32):
        return "Pet"
    if item_type == 30:
        return "Khảm"
    if item_type == 29:
        return "Vật liệu"
    return "Khác"


def get_category_icon(category: str) -> str:
    """Emoji đại diện cho từng thể loại trên sàn."""
    icons = {
        "Kiếm": "🗡️",
        "Súng": "🔫",
        "Đao": "⚔️",
        "Trượng": "🪄",
        "Cung": "🏹",
        "Nặng": "🔨",
        "Trường Thương": "🔱",
        "PhápKhí": "📖",
        "Hộ giáp": "🛡️",
        "SứcPhẩm": "💍",
        "Pet": "🐾",
        "Khảm": "💎",
        "Vật liệu": "🧪",
        "Khác": "📦",
    }
    return icons.get(category, "📦")


def format_price_str(gold: int, silver: int) -> str:
    parts = []
    if gold > 0:
        parts.append(f"{gold:,} Vàng")
    if silver > 0:
        parts.append(f"{silver:,} Bạc")
    if not parts:
        return "0 Đồng"
    return " + ".join(parts)


EQUIP_CATEGORIES = {
    "Kiếm", "Đao", "Súng", "Trượng", "Cung",
    "Nặng", "Trường Thương", "PhápKhí", "Hộ giáp", "SứcPhẩm"
}


def _get_equipment_enhance_tier(it_dict: Dict[str, Any]) -> int:
    """Phân tách trang bị đã cường hóa/khảm (+CH) khỏi đồ trắng."""
    return infer_enhance_tier(it_dict)


def format_enhance_badge(it_dict: Dict[str, Any]) -> str:
    """Hiển thị nhãn cường hóa/khảm/phù ma ngắn gọn cho trang bị."""
    if it_dict.get("category", "") not in EQUIP_CATEGORIES:
        return ""
    parts = []
    star = max(0, int(it_dict.get("upgrade_star", 0) or 0), int(it_dict.get("star", 0) or 0))
    asc = max(0, int(it_dict.get("ascension_star", 0) or 0))
    attach = max(0, int(it_dict.get("attach_done", 0) or 0))
    if asc > 0:
        parts.append(f"Thăng Tinh +{asc}")
    elif star > 0:
        parts.append(f"⭐+{star}")
    if attach > 0:
        parts.append(f"Khảm {attach}")
    if it_dict.get("has_enchant"):
        parts.append("Phù Ma")
    if not parts:
        return ""
    return f" [{' | '.join(parts)}]"


def check_item_snipe_criteria(grade: int, gold: int, silver: int) -> Tuple[bool, str]:
    """Giữ chữ ký tương thích ngược; đánh giá kèo rẻ hiện do ItemPriceTracker đảm nhiệm."""
    return False, ""


def group_identical_items(items_dict: Dict[int, Dict[str, Any]], item_ids: Set[int]) -> List[Dict[str, Any]]:
    """Gộp các vật phẩm giống hệt nhau (tên chuẩn hóa, phẩm chất, tier cường hóa, số lượng, giá bán)."""
    groups: Dict[Tuple, Dict[str, Any]] = {}
    for gid in item_ids:
        if gid not in items_dict:
            continue
        it = items_dict[gid]
        norm_name = normalize_item_name(it.get("name", ""))
        tier = _get_equipment_enhance_tier(it)
        key = (norm_name, it["grade"], tier, it["quantity"], it["gold"], it["silver"])
        if key not in groups:
            groups[key] = {
                "sample": it,
                "count": 0,
                "gids": []
            }
        groups[key]["count"] += 1
        groups[key]["gids"].append(gid)
    return list(groups.values())


def assign_proxies_for_accounts(raw_accounts: List[Tuple[str, str, Optional[str]]]) -> List[Tuple[str, str, Optional[str]]]:
    """
    Cấu hình kết nối mạng cho các tài khoản:
    - Sử dụng IP chính (trực tiếp, không qua Proxy) để đạt tốc độ giật Lì Xì tối đa và kết nối ổn định nhất.
    - Chỉ dùng Proxy nếu người dùng cố ý nhập thông tin proxy ở cột 3 trong accountsNV.txt (dạng user|pass|ip:port).
    """
    assigned = []
    for user, pwd, proxy in raw_accounts:
        chosen_proxy = proxy.strip() if proxy and proxy.strip() else None
        assigned.append((user, pwd, chosen_proxy))
    return assigned


class AppNotifier:
    """
    Bộ thông báo thuần Telegram:
    - Gửi toàn bộ thông báo về Telegram Bot an toàn, có hàng đợi tránh tràn rate-limit.
    - Tự động fallback plain-text nếu có lỗi thực thể HTML.
    """
    _tg_config = None
    _bot_instance = None
    _queue = None
    _worker_thread = None
    _lock = threading.Lock()

    @classmethod
    def set_bot(cls, bot_instance):
        """Liên kết bot_instance để gửi trực tiếp và đồng bộ rate-limit."""
        cls._bot_instance = bot_instance

    @classmethod
    def _load_tg_config(cls):
        if cls._tg_config:
            return cls._tg_config
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_paths = [
            os.path.join(base_dir, "config_247.json"),
            os.path.join(base_dir, "storage", "telegram_notify.json"),
            os.path.join(base_dir, "telegram_notify.json"),
            "config_247.json",
            "storage/telegram_notify.json"
        ]
        for cp in config_paths:
            if os.path.exists(cp):
                try:
                    import json
                    with open(cp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    tg = data.get("telegram", data)
                    if tg.get("enabled", True) and tg.get("bot_token") and tg.get("chat_id"):
                        cls._tg_config = tg
                        return cls._tg_config
                except Exception:
                    pass
        return None

    @classmethod
    def _ensure_worker(cls):
        with cls._lock:
            if cls._queue is None:
                import queue
                cls._queue = queue.Queue(maxsize=1000)
            if cls._worker_thread is None or not cls._worker_thread.is_alive():
                cls._worker_thread = threading.Thread(
                    target=cls._worker_loop,
                    daemon=True,
                    name="AppNotifierWorker"
                )
                cls._worker_thread.start()

    @classmethod
    def _worker_loop(cls):
        while True:
            try:
                item = cls._queue.get()
                if item is None:
                    break
                title, line1, line2 = item
                try:
                    cls._send_telegram(title, line1, line2)
                finally:
                    cls._queue.task_done()
                time.sleep(0.4)
            except Exception:
                time.sleep(1.0)

    @classmethod
    def _send_telegram(cls, title: str, line1: str, line2: str = ""):
        lines = [f"<b>🔔 {title}</b>", line1]
        if line2:
            lines.append(line2)
        text = "\n".join(lines)

        # 1. Thử gửi qua TelegramBot instance nếu có
        if cls._bot_instance and hasattr(cls._bot_instance, "send_message"):
            try:
                ok = cls._bot_instance.send_message(text)
                if ok:
                    return
            except Exception:
                pass

        # 2. Fallback sang send_telegram_html_safe
        cfg = cls._load_tg_config()
        if not cfg:
            return
        token = str(cfg.get("bot_token", "")).strip()
        chat_id = str(cfg.get("chat_id", "")).strip()
        if token and chat_id:
            send_telegram_html_safe(token, chat_id, text, timeout=10)

    @classmethod
    def send_toast(cls, title: str, line1: str, line2: str = ""):
        """Đưa thông báo vào hàng đợi gửi tuần tự (tránh tạo quá nhiều luồng song song gây 429)."""
        cls._ensure_worker()
        try:
            cls._queue.put_nowait((title, line1, line2))
        except Exception:
            pass


# Giữ alias WindowsNotifier để tương thích 100% với các hàm gọi cũ
WindowsNotifier = AppNotifier



class WatcherWorker:
    """
    Mỗi Worker đại diện cho 1 tài khoản chạy trên 1 luồng riêng biệt:
    - Acc 1: role = 'char' (Quét nhân vật 30s)
    - Acc 2: role = 'item' (Quét vật phẩm 30s)
    - Acc 3, 4: role = 'idle' (Treo trực tuyến & bắt Lì Xì)
    - role = 'both' (Chế độ dự phòng nếu chỉ có 1 tài khoản trong accountsNV.txt)
    """
    def __init__(self, idx: int, user: str, pwd: str, proxy: Optional[str], role: str, manager: 'MultiAccountMarketWatcher'):
        self.idx = idx
        self.user = user
        self.pwd = pwd
        self.proxy = proxy
        self.use_direct_ip = (proxy is None or proxy == "" or proxy == "None")
        self.role = role
        self.manager = manager

        self.conn: Optional[PVTKConnection] = None
        self.char_info: Optional[CharacterInfo] = None
        self.is_connected = False
        self.is_running = False
        self.last_heartbeat = 0.0

        self.packet_queue: List[Message] = []
        self.claimed_luck_money_ids: Set[int] = set()
        self.total_won_gold = 0
        self.thread: Optional[threading.Thread] = None
        self.next_reward_check_time = 0.0
        self.claimed_rewards_count = 0
        self.last_npc_error_notify = 0.0
        self.is_event_busy = False
        self.event_lock = threading.RLock()
        self.last_map_check_time = 0.0
        self.characters: List[CharacterInfo] = []
        self.login_setting: int = 0
        self.consecutive_failures = 0
        self.consecutive_scan_errors = 0
        self.is_phone_active = False
        self.phone_active_until = 0.0

    def _close_conn(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        self.is_connected = False

    def get_role_display(self) -> str:
        scan_sec = int(getattr(self.manager, "scan_interval", 15.0))
        if self.role == "char":
            return f"Theo Dõi Nhân Vật ({scan_sec}s) + Đoạt Lì Xì + Quà Online"
        elif self.role == "item":
            return f"Theo Dõi Vật Phẩm ({scan_sec}s - Săn Đồ) + Đoạt Lì Xì + Quà Online"
        elif self.role == "idle":
            return "Treo Trực Tuyến + Đoạt Lì Xì + Quà Online"
        elif self.role == "both":
            return f"Theo Dõi Kép (NV {scan_sec}s + Đồ {scan_sec}s) + Đoạt Lì Xì + Quà Online"
        return "Đoạt Lì Xì + Quà Online"

    def is_lottery_due(self) -> bool:
        # Tài khoản accountsNV.txt được cấu hình cố định treo online 24/24, không ngắt phiên để rút thưởng
        return False

    def is_quiz_due(self) -> bool:
        # Tài khoản accountsNV.txt được cấu hình cố định treo online 24/24, không ngắt phiên để vấn đáp
        return False

    def execute_in_session_lottery(self):
        """Tạm dừng theo dõi để thực hiện Rút Thưởng trên tất cả nhân vật Lv >= 30, sau đó quay về Tân Nguyệt Trấn tiếp tục theo dõi."""
        if not self.conn or not self.conn.is_connected or not self.char_info:
            return

        with self.event_lock:
            self.is_event_busy = True
            all_chars = self.characters or [self.char_info]

            # Thử lấy danh sách nhân vật mới nhất nếu có
            try:
                latest_chars = AuthManager.fetch_character_list(self.conn)
                if latest_chars:
                    all_chars = latest_chars
                    self.characters = latest_chars
            except Exception:
                pass

            curr = self.char_info
            print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[93m[ACC {self.idx}: {self.user}]\033[0m 🎲 [RÚT THƯỞNG LUÂN PHIÊN] Bắt đầu duyệt toàn bộ {len(all_chars)} nhân vật trên tài khoản...", flush=True)

            for char in all_chars:
                if not self.is_running:
                    break

                char_tag = f"[ACC {self.idx}: {self.user} | {char.name}]"

                if getattr(char, "level", 0) < 30:
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] {char_tag} \033[90m[BỎ QUA RÚT THƯỞNG]\033[0m Cấp độ {char.level} < 30.", flush=True)
                    continue

                if DailyHistoryManager.is_lottery_done_today(self.user, char.name):
                    rec = DailyHistoryManager.get_lottery_record(self.user, char.name)
                    best_num = rec.get("best_number", 0) if rec else 0
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] {char_tag} \033[90m[BỎ QUA RÚT THƯỞNG]\033[0m Đã hoàn thành hôm nay (Điểm: {best_num}).", flush=True)
                    continue

                # Đổi nhân vật nếu cần
                if curr.id != char.id:
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] [ACC {self.idx}: {self.user}] 🔄 [ĐỔI NV RÚT THƯỞNG] Chuyển từ '{curr.name}' sang '{char.name}' (Lv.{char.level})...", flush=True)
                    try:
                        AuthManager.leave_character(self.conn)
                    except Exception:
                        pass
                    time.sleep(0.5)

                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(self.conn, char.id, self.login_setting, char_info=char)
                    except Exception:
                        enter_ok = False

                    if not enter_ok:
                        self._close_conn()
                        new_conn, new_login = AuthManager.reconnect_game_session(
                            settings.game_host, settings.game_port,
                            settings.master_host, settings.master_port,
                            self.user, self.pwd, proxy=self.proxy
                        )
                        if new_conn:
                            self.conn = new_conn
                            self.is_connected = True
                            self.login_setting = new_login
                            try:
                                enter_ok = AuthManager.enter_character(self.conn, char.id, self.login_setting, char_info=char)
                            except Exception:
                                enter_ok = False

                    if not enter_ok:
                        print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] {char_tag} \033[91m[LỖI ĐỔI NV]\033[0m Không vào được nhân vật {char.name}.", flush=True)
                        continue

                    curr = char
                    self.char_info = curr

                # Đảm bảo đang ở Quảng Trường Tân Nguyệt (Map 38) để Rút Thưởng
                curr_map = (curr.map_name or "").strip()
                if not ("quảng trường" in curr_map.lower() or "quang truong" in curr_map.lower()):
                    MapTeleporter.teleport_to_quang_truong(self.conn)
                    curr.map_name = "Quảng Trường Tân Nguyệt"
                    time.sleep(1.0)
                    while self.conn and self.conn.receive_message(timeout=0.05):
                        pass

                print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[93m{char_tag}\033[0m 🎲 Đang thực hiện Rút Thưởng May Mắn...", flush=True)
                try:
                    res = LotteryManager.execute_lottery_for_character(self.conn, char_tag=char_tag, delay_sec=0.25)
                    best_num = res.get("best_number", 0) if res else 0
                    hit_target = res.get("hit_target", False) if res else False
                    hit_max = res.get("hit_max_1000", False) if res else False
                    rolls_done = res.get("rolls_done", 0) if res else 0
                    status = res.get("status", "COMPLETED") if res else "COMPLETED"
                    msg = res.get("message", "") if res else ""

                    DailyHistoryManager.save_lottery_done(
                        username=self.user,
                        char_name=curr.name,
                        level=curr.level,
                        best_number=best_num,
                        rolls_done=rolls_done,
                        hit_target=hit_target,
                        hit_max_1000=hit_max,
                        status=status,
                        message=msg
                    )
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[92m{char_tag}\033[0m 🎲 [RÚT THƯỞNG XONG] Điểm: \033[93m{best_num}\033[0m. Đã lưu lịch sử hôm nay!", flush=True)
                except Exception as e:
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[91m{char_tag} [LỖI RÚT THƯỞNG]\033[0m {e}", flush=True)

            # Sau khi luân phiên xong: Trở lại nhân vật chính (all_chars[0])
            try:
                target_main = all_chars[0]
                if self.char_info and self.char_info.id != target_main.id:
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] [ACC {self.idx}: {self.user}] 🔄 Đang chuyển lại nhân vật chính '{target_main.name}' để tiếp tục theo dõi...", flush=True)
                    try:
                        AuthManager.leave_character(self.conn)
                    except Exception:
                        pass
                    time.sleep(0.5)

                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(self.conn, target_main.id, self.login_setting, char_info=target_main)
                    except Exception:
                        enter_ok = False

                    if not enter_ok:
                        self._close_conn()
                        new_conn, new_login = AuthManager.reconnect_game_session(
                            settings.game_host, settings.game_port,
                            settings.master_host, settings.master_port,
                            self.user, self.pwd, proxy=self.proxy
                        )
                        if new_conn:
                            self.conn = new_conn
                            self.is_connected = True
                            self.login_setting = new_login
                            AuthManager.enter_character(self.conn, target_main.id, self.login_setting, char_info=target_main)

                    self.char_info = target_main

                # Dịch chuyển lại về Tân Nguyệt Trấn (Map 37)
                MapTeleporter.teleport_to_tan_nguyet_tran(self.conn)
                if self.char_info:
                    self.char_info.map_name = "Tân Nguyệt Trấn"
                time.sleep(1.0)
                while self.conn and self.conn.receive_message(timeout=0.05):
                    pass
            except Exception as ex:
                print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] [ACC {self.idx}: {self.user}] Lỗi hoàn nguyên nhân vật chính: {ex}", flush=True)
            finally:
                self.ensure_npc_and_map()
                self.is_event_busy = False

    def execute_in_session_quiz(self):
        """Tạm dừng theo dõi để thực hiện Vấn Đáp trên tất cả nhân vật (mọi cấp độ), sau đó quay về Tân Nguyệt Trấn tiếp tục theo dõi."""
        if not self.conn or not self.conn.is_connected or not self.char_info:
            return

        with self.event_lock:
            self.is_event_busy = True
            all_chars = self.characters or [self.char_info]

            # Thử lấy danh sách nhân vật mới nhất nếu có
            try:
                latest_chars = AuthManager.fetch_character_list(self.conn)
                if latest_chars:
                    all_chars = latest_chars
                    self.characters = latest_chars
            except Exception:
                pass

            curr = self.char_info
            print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[93m[ACC {self.idx}: {self.user}]\033[0m 🎓 [VẤN ĐÁP LUÂN PHIÊN] Bắt đầu duyệt toàn bộ {len(all_chars)} nhân vật trên tài khoản...", flush=True)

            for char in all_chars:
                if not self.is_running:
                    break

                char_tag = f"[ACC {self.idx}: {self.user} | {char.name}]"

                if DailyHistoryManager.is_quiz_done_today(self.user, char.name):
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] {char_tag} \033[90m[BỎ QUA VẤN ĐÁP]\033[0m Đã hoàn thành hôm nay.", flush=True)
                    continue

                # Đổi nhân vật nếu cần
                if curr.id != char.id:
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] [ACC {self.idx}: {self.user}] 🔄 [ĐỔI NV VẤN ĐÁP] Chuyển từ '{curr.name}' sang '{char.name}' (Lv.{char.level})...", flush=True)
                    try:
                        AuthManager.leave_character(self.conn)
                    except Exception:
                        pass
                    time.sleep(0.5)

                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(self.conn, char.id, self.login_setting, char_info=char)
                    except Exception:
                        enter_ok = False

                    if not enter_ok:
                        self._close_conn()
                        new_conn, new_login = AuthManager.reconnect_game_session(
                            settings.game_host, settings.game_port,
                            settings.master_host, settings.master_port,
                            self.user, self.pwd, proxy=self.proxy
                        )
                        if new_conn:
                            self.conn = new_conn
                            self.is_connected = True
                            self.login_setting = new_login
                            try:
                                enter_ok = AuthManager.enter_character(self.conn, char.id, self.login_setting, char_info=char)
                            except Exception:
                                enter_ok = False

                    if not enter_ok:
                        print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] {char_tag} \033[91m[LỖI ĐỔI NV]\033[0m Không vào được nhân vật {char.name}.", flush=True)
                        continue

                    curr = char
                    self.char_info = curr

                # Thực hiện Vấn Đáp cho nhân vật hiện tại ngay tại vị trí hiện tại (Không chuyển map)
                print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[93m{char_tag}\033[0m 🎓 Đang giải 20 câu hỏi Vấn Đáp tại vị trí hiện tại...", flush=True)
                try:
                    while self.conn and self.conn.receive_message(timeout=0.05):
                        pass

                    is_done, q_cnt, c_cnt, msg = QuizManager.solve_character_quiz(self.conn, char_tag=char_tag, delay_sec=0.05)

                    DailyHistoryManager.save_quiz_done(
                        username=self.user,
                        char_name=curr.name,
                        level=curr.level,
                        questions=q_cnt,
                        correct=c_cnt,
                        is_done=is_done,
                        message=msg
                    )
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[92m{char_tag}\033[0m 🎓 [VẤN ĐÁP XONG] Đúng: \033[92m{c_cnt}/{q_cnt}\033[0m câu. Đã lưu lịch sử hôm nay!", flush=True)
                except Exception as e:
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] \033[91m{char_tag} [LỖI VẤN ĐÁP]\033[0m {e}", flush=True)

            # Sau khi luân phiên xong: Trở lại nhân vật chính (all_chars[0])
            try:
                target_main = all_chars[0]
                if self.char_info and self.char_info.id != target_main.id:
                    print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] [ACC {self.idx}: {self.user}] 🔄 Đang chuyển lại nhân vật chính '{target_main.name}' để tiếp tục theo dõi...", flush=True)
                    try:
                        AuthManager.leave_character(self.conn)
                    except Exception:
                        pass
                    time.sleep(0.5)

                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(self.conn, target_main.id, self.login_setting, char_info=target_main)
                    except Exception:
                        enter_ok = False

                    if not enter_ok:
                        self._close_conn()
                        new_conn, new_login = AuthManager.reconnect_game_session(
                            settings.game_host, settings.game_port,
                            settings.master_host, settings.master_port,
                            self.user, self.pwd, proxy=self.proxy
                        )
                        if new_conn:
                            self.conn = new_conn
                            self.is_connected = True
                            self.login_setting = new_login
                            AuthManager.enter_character(self.conn, target_main.id, self.login_setting, char_info=target_main)

                    self.char_info = target_main

                # Dịch chuyển lại về Tân Nguyệt Trấn (Map 37)
                MapTeleporter.teleport_to_tan_nguyet_tran(self.conn)
                if self.char_info:
                    self.char_info.map_name = "Tân Nguyệt Trấn"
                time.sleep(1.0)
                while self.conn and self.conn.receive_message(timeout=0.05):
                    pass
            except Exception as ex:
                print(f"[{datetime.now(VN_TZ).strftime('%H:%M:%S')}] [ACC {self.idx}: {self.user}] Lỗi hoàn nguyên nhân vật chính: {ex}", flush=True)
            finally:
                self.ensure_npc_and_map()
                self.is_event_busy = False

    def _rotate_proxy(self):
        """Tự động đổi sang proxy mới khi gặp lỗi mạng."""
        if getattr(self, "use_direct_ip", False):
            # Acc sử dụng IP chính trực tiếp, không bao giờ tự động gán proxy
            return
        try:
            from modules.free_proxy import FreeProxyManager
            new_prx = FreeProxyManager.acquire_proxy(exclude_proxy=self.proxy)
            if new_prx and new_prx != self.proxy:
                print(f"[ACC {self.idx}: {self.user}] \033[93m[ĐỔI PROXY]\033[0m Chuyển sang proxy: {new_prx}")
                self.proxy = new_prx
        except Exception:
            pass

    def check_and_claim_online_reward(self):
        """Tự động kiểm tra và nhận mốc quà online (Opcode 0x2B65)."""
        if not self.conn or not self.conn.is_connected:
            return
        try:
            msg = Message(0x2B65)
            if not self.conn.send_message(msg):
                self.next_reward_check_time = time.time() + 30.0
                return
            res = self.wait_for_packet(0x2B65, timeout=2.5)
            if not res:
                self.next_reward_check_time = time.time() + 30.0
                return
            rs = res.getByte()
            if rs == 0:
                self.claimed_rewards_count += 1
                char_name = self.char_info.name if self.char_info else self.user
                print(f"[{time.strftime('%H:%M:%S')}] \033[92m[ACC {self.idx}: {self.user} | {char_name}]\033[0m 🎁 Nhận quà online thành công (Mốc {self.claimed_rewards_count})!")
                self.next_reward_check_time = time.time() + 5.0
            elif rs == 1:
                next_ms = res.getLong() if res.hasRemaining() else 0
                countdown_sec = max(5, int(next_ms / 1000)) if next_ms > 0 else 30
                self.next_reward_check_time = time.time() + countdown_sec
            else:
                self.next_reward_check_time = time.time() + 3600.0
        except Exception:
            self.next_reward_check_time = time.time() + 30.0

    def login_and_enter(self) -> bool:
        self._close_conn()
        if time.time() < self.phone_active_until:
            rem = int(self.phone_active_until - time.time())
            print(f"[{time.strftime('%H:%M:%S')}] \033[93m[ACC {self.idx}: {self.user}]\033[0m Đang nhường phiên chơi trên điện thoại (còn {rem}s)...", flush=True)
            return False

        proxy_desc = f"Proxy: {self.proxy}" if self.proxy else "IP Chính (Trực tiếp)"
        print(f"\033[96m[ACC {self.idx}: {self.user}]\033[0m Đang kết nối ({proxy_desc})...", flush=True)

        res, err = AuthManager.master_login(
            settings.master_host,
            settings.master_port,
            self.user,
            self.pwd,
            proxy=self.proxy
        )
        if not res:
            self.consecutive_failures += 1
            print(f"\033[91m[ACC {self.idx}: {self.user} - LỖI ĐĂNG NHẬP (Lần {self.consecutive_failures})]\033[0m {err}", flush=True)
            if self.consecutive_failures >= 5:
                try:
                    from core.tele_notifier import TeleNotifier
                    TeleNotifier.get_instance().send_error_alert(f"Lỗi Đăng Nhập Watcher (Acc {self.idx})", f"Tài khoản: {self.user}\nLỗi liên tiếp: {self.consecutive_failures} lần\nChi tiết: {err}", source=f"Watcher Acc {self.idx}", cooldown=300.0, dedup_key=f"watcher_{self.idx}_login")
                except Exception:
                    pass
            self._rotate_proxy()
            return False

        ukey, session_id, login_setting = res

        conn = AuthManager.connect_game_server(
            settings.game_host,
            settings.game_port,
            ukey,
            session_id,
            proxy=self.proxy
        )
        if not conn:
            self.consecutive_failures += 1
            print(f"\033[91m[ACC {self.idx}: {self.user} - LỖI KẾT NỐI (Lần {self.consecutive_failures})]\033[0m Không thể kết nối tới Game Server!", flush=True)
            if self.consecutive_failures >= 5:
                try:
                    from core.tele_notifier import TeleNotifier
                    TeleNotifier.get_instance().send_error_alert(f"Lỗi Kết Nối Server Watcher (Acc {self.idx})", f"Tài khoản: {self.user}\nLỗi liên tiếp: {self.consecutive_failures} lần\nKhông thể kết nối tới Game Server ({settings.game_host}:{settings.game_port})", source=f"Watcher Acc {self.idx}", cooldown=300.0, dedup_key=f"watcher_{self.idx}_conn")
                except Exception:
                    pass
            self._rotate_proxy()
            return False

        try:
            chars = AuthManager.fetch_character_list(conn)
        except Exception:
            conn.close()
            self.consecutive_failures += 1
            self._rotate_proxy()
            return False
        if not chars:
            self.consecutive_failures += 1
            print(f"\033[91m[ACC {self.idx}: {self.user} - LỖI]\033[0m Chưa có nhân vật nào trong game!", flush=True)
            if self.consecutive_failures >= 5:
                try:
                    from core.tele_notifier import TeleNotifier
                    TeleNotifier.get_instance().send_error_alert(f"Lỗi Nhân Vật Watcher (Acc {self.idx})", f"Tài khoản: {self.user}\nChưa có nhân vật nào trong game!", source=f"Watcher Acc {self.idx}", cooldown=300.0, dedup_key=f"watcher_{self.idx}_char")
                except Exception:
                    pass
            conn.close()
            return False

        self.characters = chars
        self.login_setting = login_setting
        char = chars[0]
        self.char_info = char
        map_display = char.map_name or "Tân Nguyệt Trấn"

        try:
            ok = AuthManager.enter_character(conn, char.id, login_setting=login_setting, char_info=char)
        except Exception:
            ok = False
        if not ok:
            err_msg = getattr(conn, "last_enter_error", "") or "Server từ chối vào game!"
            err_lower = err_msg.lower()
            is_server_busy = ("làm mới đang bận" in err_lower or "thử lại sau" in err_lower)
            is_conflict = (not is_server_busy) and any(
                kw in err_lower for kw in ("client khác", "nơi khác", "đang đăng nhập", "đang online", "kẹt")
            )
            if is_server_busy:
                self.consecutive_failures += 1
                print(f"\033[93m[ACC {self.idx}: {self.user}]\033[0m Server tạm bận ({err_msg}), sẽ thử lại...", flush=True)
            elif is_conflict:
                self.is_phone_active = True
                self.phone_active_until = time.time() + 60.0
                print(f"[{time.strftime('%H:%M:%S')}] \033[93m[ACC {self.idx}: {self.user}]\033[0m Phát hiện đang chơi trên điện thoại hoặc phiên bận ({err_msg}). Tạm lùi bước nhường phiên trong 60 giây...", flush=True)
            else:
                self.consecutive_failures += 1
                print(f"\033[91m[ACC {self.idx}: {self.user} - LỖI VÀO GAME (Lần {self.consecutive_failures})]\033[0m {err_msg}", flush=True)
                if self.consecutive_failures >= 5:
                    try:
                        from core.tele_notifier import TeleNotifier
                        TeleNotifier.get_instance().send_error_alert(f"Lỗi Vào Game Watcher (Acc {self.idx})", f"Tài khoản: {self.user}\nNhân vật: {char.name}\nLỗi liên tiếp: {self.consecutive_failures} lần\nChi tiết: {err_msg}", source=f"Watcher Acc {self.idx}", cooldown=300.0, dedup_key=f"watcher_{self.idx}_enter")
                    except Exception:
                        pass
            conn.close()
            self._rotate_proxy()
            return False

        self.consecutive_failures = 0
        self.consecutive_scan_errors = 0
        self.is_phone_active = False
        self.phone_active_until = 0.0
        self.conn = conn
        self.is_connected = True
        self.packet_queue.clear()
        self.last_heartbeat = time.time()
        self.next_reward_check_time = 0.0

        # Xả gói tin ban đầu
        time.sleep(0.3)
        while self.conn.receive_message(timeout=0.05):
            pass

        # Cập nhật số dư tiền tệ thực tế từ Server
        self.current_gold = getattr(char, "gold", 0)
        self.current_silver = getattr(char, "silver", 0)

        print(f"\033[92m[ACC {self.idx}: {self.user}]\033[0m Đã vào game: \033[93m{char.name}\033[0m (Cấp {char.level}) | Map: \033[95m{map_display}\033[0m | Vai trò: \033[96m{self.get_role_display()}\033[0m", flush=True)

        # Tự động kiểm tra vị trí map và NPC, nếu chưa ở Tân Nguyệt Trấn thì dịch chuyển sang
        self.ensure_npc_and_map()

        return True

    def ensure_npc_and_map(self, force: bool = False) -> bool:
        """
        Kiểm tra và đảm bảo tất cả các Worker (Acc 1, 2, 3) có mặt tại map Tân Nguyệt Trấn (ChildMapID 37):
        - Acc 1 (role='char'): Cần ở Tân Nguyệt Trấn & kết nối NPC 'Sở Nhân Vật'
        - Acc 2 (role='item'): Cần ở Tân Nguyệt Trấn & kết nối NPC 'Sở G.Dịch'
        - role='both': Cần ở Tân Nguyệt Trấn & kết nối cả 2 NPC
        - role='idle': Cần ở Tân Nguyệt Trấn (để sẵn sàng nhận quà, rút may mắn & đoạt lì xì)

        Nếu map hiện tại không phải Tân Nguyệt Trấn (ChildMapID 37) hoặc force=True:
        -> Dịch chuyển ngay đến Tân Nguyệt Trấn (ChildMapID 37).
        Sau đó kiểm tra xác nhận NPC và kết nối Sở Giao Dịch tương ứng.
        Nếu sau khi dịch chuyển vẫn không tìm thấy NPC / không kết nối được:
        -> Báo lỗi lên Telegram.
        """
        if not self.conn or not self.conn.is_connected:
            return False

        target_npc = "Sở G.Dịch (Vật Phẩm)" if self.role == "item" else ("Sở Nhân Vật" if self.role == "char" else ("Sở G.Dịch & Sở Nhân Vật" if self.role == "both" else "Tân Nguyệt Trấn"))
        curr_map = (self.char_info.map_name or "").strip() if self.char_info else ""

        is_at_tan_nguyet = ("tân nguyệt trấn" in curr_map.lower() or "tan nguyet tran" in curr_map.lower() or curr_map.lower() == "tân nguyệt trấn") and "quảng trường" not in curr_map.lower() and not force

        if not is_at_tan_nguyet:
            print(f"[{time.strftime('%H:%M:%S')}] \033[93m[ACC {self.idx}: {self.user}]\033[0m Nhân vật đang ở map '{curr_map}'. Đang tự động dịch chuyển về đúng map Tân Nguyệt Trấn...", flush=True)

            teleport_ok = MapTeleporter.teleport_to_tan_nguyet_tran(self.conn)
            time.sleep(1.2)

            # Xả các gói tin sau khi dịch chuyển
            while self.conn and self.conn.is_connected and self.conn.receive_message(timeout=0.1):
                pass

            if teleport_ok:
                if self.char_info:
                    self.char_info.map_name = "Tân Nguyệt Trấn"
                print(f"[{time.strftime('%H:%M:%S')}] \033[92m[ACC {self.idx}: {self.user}]\033[0m Đã dịch chuyển đến map Tân Nguyệt Trấn thành công!", flush=True)
            else:
                err_msg = f"Dịch chuyển từ '{curr_map}' sang map Tân Nguyệt Trấn thất bại!"
                print(f"[{time.strftime('%H:%M:%S')}] \033[91m[ACC {self.idx}: {self.user} - LỖI DỊCH CHUYỂN]\033[0m {err_msg}", flush=True)
                if self.conn and self.conn.is_connected:
                    self.manager.notify_npc_error_telegram(self, target_npc, err_msg)
                else:
                    self._close_conn()
                return False

        # Nếu role là idle thì không cần kiểm tra NPC chợ
        if self.role == "idle":
            return True

        # Xác thực kết nối NPC / Sở Giao Dịch tại map Tân Nguyệt Trấn
        verify_ok = True
        err_detail = ""

        if self.role in ("char", "both"):
            ok, data, err = self.fetch_market_characters()
            if not ok:
                verify_ok = False
                err_detail = f"Không tìm thấy NPC 'Sở Nhân Vật' hoặc lỗi kết nối Sở Nhân Vật: {err}"

        if verify_ok and self.role in ("item", "both"):
            ok, data, err = self.fetch_market_items()
            if not ok:
                verify_ok = False
                err_detail = f"Không tìm thấy NPC 'Sở G.Dịch' hoặc lỗi kết nối Sở Giao Dịch: {err}"

        if not verify_ok:
            print(f"[{time.strftime('%H:%M:%S')}] \033[91m[ACC {self.idx}: {self.user} - LỖI NPC]\033[0m {err_detail}", flush=True)
            if self.conn and self.conn.is_connected:
                self.manager.notify_npc_error_telegram(self, target_npc, err_detail)
            else:
                self._close_conn()
            return False

        return True

    def send_heartbeat(self):
        """Gửi gói tin nhịp tim CG_SYSTEM_HEART (0x3E81) định kỳ mỗi 8s."""
        if not self.conn or not self.conn.is_connected or not self.conn.sock:
            return
        try:
            msg = Message(0x3E81)
            map_id = 37
            if self.char_info and hasattr(self.char_info, "map_id") and self.char_info.map_id:
                map_id = self.char_info.map_id
            msg.putShort(map_id)
            self.conn.send_message(msg)
            self.last_heartbeat = time.time()
        except Exception:
            pass

    def drain_socket(self, max_count: int = 20):
        """Đọc và xả bộ đệm socket, bắt tin nhắn chat lì xì tức thì."""
        if not self.conn or not self.conn.sock or not self.conn.is_connected:
            return
        count = 0
        while count < max_count and self.conn and self.conn.is_connected and self.conn.sock:
            try:
                r, _, _ = select.select([self.conn.sock], [], [], 0)
                if not r:
                    break
                m = self.conn.receive_message(timeout=0.05)
                if not m:
                    break
                if m.type in CHAT_OPCODES:
                    self.parse_and_handle_chat_packet(m)
                elif m.type == 0x2B64:
                    self.next_reward_check_time = 0.0
                elif m.type == 0x0005:
                    pass
                else:
                    self.packet_queue.append(m)
                    if len(self.packet_queue) > 200:
                        self.packet_queue.pop(0)
                count += 1
            except Exception:
                break

    def wait_for_packet(self, expected_type: int, timeout: float = 4.0) -> Optional[Message]:
        """Chờ gói tin cụ thể và bảo toàn các gói tin khác trong packet_queue."""
        if not self.conn or not self.conn.is_connected:
            return None

        for i, pkt in enumerate(self.packet_queue):
            if pkt.type == expected_type:
                return self.packet_queue.pop(i)

        t_end = time.time() + timeout
        while time.time() < t_end and self.conn and self.conn.is_connected:
            for i, pkt in enumerate(self.packet_queue):
                if pkt.type == expected_type:
                    return self.packet_queue.pop(i)

            pkt = self.conn.receive_message(timeout=0.3)
            if not pkt:
                continue

            if pkt.type == 0x0005:
                continue

            if pkt.type == expected_type:
                return pkt

            if pkt.type in CHAT_OPCODES:
                self.parse_and_handle_chat_packet(pkt)
                for i, q_pkt in enumerate(self.packet_queue):
                    if q_pkt.type == expected_type:
                        return self.packet_queue.pop(i)
                continue

            self.packet_queue.append(pkt)
            if len(self.packet_queue) > 200:
                self.packet_queue.pop(0)

        return None

    def parse_and_handle_chat_packet(self, pkt: Message):
        """Giải mã gói tin chat, nếu có Lì Xì (flag 0x8000) thì kích hoạt đoạt Lì Xì toàn bộ Acc."""
        try:
            if not pkt or not pkt.data or len(pkt.data) == 0:
                return
            size = pkt.getByte() & 0xFF
            for _ in range(size):
                if not pkt.hasRemaining():
                    break
                player_id = pkt.getInt()
                player_name = pkt.getString()
                flag = pkt.getShort() & 0xFFFF

                vip_level = 0
                if (flag & 0x100) or (flag & 0x1000):
                    vip_level = pkt.getByte()

                content = pkt.getString()
                vip_level2 = pkt.getByte()
                remark = pkt.getString()

                if flag & 0x04:
                    num_items = pkt.getByte()
                    for _ in range(num_items):
                        pkt.getByte()

                # Kiểm tra Lì Xì (flag 0x8000)
                if flag & 0x8000:
                    luck_money_id = pkt.getLong()
                    if luck_money_id:
                        self.manager.broadcast_luck_money(luck_money_id, player_name, player_id, content)

                if flag & 0x08:
                    num_missions = pkt.getByte()
                    for _ in range(num_missions):
                        pkt.getShort()

                if flag & 0x80:
                    pkt.getInt()

        except Exception:
            pass

    def snatch_luck_money(self, luck_id: int, sender_name: str, sender_id: int, note: str):
        """
        Gửi yêu cầu đoạt lì xì (Opcode 0x34F6 action 2).
        CHỈ IN RA MÀN HÌNH KHI ĐOẠT THÀNH CÔNG (res_code >= 0) theo đúng yêu cầu!
        """
        if luck_id in self.claimed_luck_money_ids:
            return
        self.claimed_luck_money_ids.add(luck_id)

        if not self.conn or not self.conn.is_connected:
            return

        if getattr(self, "is_event_busy", False):
            return

        try:
            msg = Message(0x34F6)
            msg.putByte(2)
            msg.putLong(luck_id)

            if not self.conn.send_message(msg):
                return

            resp = self.wait_for_packet(0x34F6, timeout=2.5)
            if not resp:
                return

            res_code = resp.getByte()
            if res_code >= 0:
                gold_won = resp.getInt()
                new_total_gold = resp.getInt()
                self.total_won_gold += gold_won
                self.current_gold = new_total_gold
                self.manager.on_luck_money_won(self, gold_won, new_total_gold, sender_name, note)
            else:
                pass
        except Exception:
            pass

    def query_current_gold(self) -> int:
        """Truy vấn số KNB (vàng) thực tế hiện có của nhân vật."""
        gold = getattr(self, "current_gold", 0)
        if gold <= 0 and self.char_info:
            gold = getattr(self.char_info, "gold", 0)
            self.current_gold = gold
        return gold

    def send_gold_to_target(self, target_str: str, amount: Optional[int] = None) -> Tuple[bool, int, str]:
        """
        Gửi KNB (vàng) qua hòm thư in-game (Opcode 0x2B01) đến target_str (Player ID hoặc Tên).
        Nếu amount is None: tự động chuyển TOÀN BỘ KNB hiện có.
        Returns: (success: bool, amount_sent: int, message: str)
        """
        if not self.conn or not self.conn.is_connected:
            return False, 0, f"Acc {self.user} không có kết nối in-game"

        with self.event_lock:
            self.is_event_busy = True
            try:
                if amount is not None and amount > 0:
                    gold_balance = getattr(self, "current_gold", 0)
                    if gold_balance < amount:
                        gold_balance = self.query_current_gold()
                else:
                    gold_balance = self.query_current_gold()

                to_send = gold_balance if amount is None else min(amount, gold_balance)
                if to_send <= 0:
                    return True, 0, f"Acc {self.user} không có KNB (0 KNB)"

                from modules.mail_sender import parse_player_id
                target_pid = parse_player_id(target_str)
                if target_pid is None:
                    return False, 0, f"Từ chối gửi: '{target_str}' không phải là Player ID hợp lệ. Hệ thống chỉ cho phép gửi qua ID để đảm bảo an toàn tuyệt đối!"

                self.drain_socket()
                self.packet_queue = [p for p in self.packet_queue if p.type != 0x2B01]

                msg = Message(0x2B01)
                mail_title = f"Test 1 KNB tu {self.user}" if (amount is not None and amount == 1) else f"KNB tu {self.user}"
                msg.putString(mail_title)
                msg.putInt(to_send)  # money1 = Vàng / KNB tặng
                msg.putInt(0)        # money2 = Kim diệp tặng
                msg.putInt(0)        # money3 = Bạc tặng
                msg.putInt(0)        # reqMoney1 = Vàng nhận (0 = Tặng)
                msg.putInt(0)        # reqMoney2 = Kim diệp nhận
                msg.putInt(0)        # reqMoney3 = Bạc nhận
                msg.putByte(0)       # itemCount = 0

                # BẮT BUỘC chỉ gửi theo Player ID (Mode 1), tuyệt đối không gửi theo Tên
                msg.putByte(1)       # 1 = Gửi theo Player ID
                msg.putInt(target_pid)

                if not self.conn.send_message(msg):
                    return False, 0, "Gửi gói tin 0x2B01 thất bại"

                res = self.wait_for_packet(0x2B01, timeout=5.0)
                if not res:
                    return False, 0, "Không nhận được phản hồi từ Server"

                res_code = res.getByte()
                if res_code < 0:
                    err_text = res.getString() if res.hasRemaining() else f"Mã lỗi {res_code}"
                    return False, 0, err_text

                self.current_gold = max(0, gold_balance - to_send)
                return True, to_send, "Thành công"
            except Exception as e:
                return False, 0, str(e)
            finally:
                self.is_event_busy = False

    def fetch_market_characters(self) -> Tuple[bool, Dict[int, Dict[str, Any]], str]:
        """Tải toàn bộ danh sách nhân vật bán trên sàn (Opcode 0x2B22)."""
        if not self.conn or not self.conn.is_connected:
            return False, {}, "Mất kết nối game"

        characters: Dict[int, Dict[str, Any]] = {}
        page = 1
        page_size = 100
        total_market_size = None

        try:
            self.drain_socket()
            self.packet_queue = [p for p in self.packet_queue if p.type != 0x2B22]

            while True:
                msg = Message(0x2B22)
                msg.putByte(1)          # actorSearchType = 1
                msg.putByte(-1)         # jobID = -1 (All)
                msg.putByte(-1)         # sexID = -1 (All)
                msg.putByte(-1)         # sortID = -1 (Default sort)
                msg.putByte(page_size)  # pageSize
                msg.putShort(page)      # pageNum

                res = None
                for attempt in range(2):
                    if not self.conn.send_message(msg):
                        return False, {}, "Gửi gói tin 0x2B22 thất bại"

                    res = self.wait_for_packet(0x2B22, timeout=6.0)
                    if res:
                        break
                    time.sleep(0.2)

                if not res:
                    return False, {}, f"Không nhận được phản hồi trang {page} (0x2B22) từ Server"

                all_size = res.getShort()
                size = res.getByte()
                if total_market_size is None:
                    total_market_size = all_size

                for _ in range(size):
                    if not res.hasRemaining():
                        break
                    actor_id = res.getInt()
                    actor_name = res.getString()
                    sex = res.getByte()
                    race = res.getByte()
                    job = res.getByte()
                    level = res.getByte()
                    icon1 = res.getInt()
                    icon2 = res.getInt()
                    icon3 = res.getInt()
                    sell_price = res.getInt()
                    owner_id = res.getInt()

                    job_str = JOB_NAMES.get(job, f"Phái {job}")
                    characters[actor_id] = {
                        "id": actor_id,
                        "name": actor_name,
                        "level": level,
                        "job": job,
                        "job_name": job_str,
                        "sex": "Nữ" if sex == 1 else "Nam",
                        "price": sell_price,
                        "owner_id": owner_id,
                        "first_seen": time.time()
                    }

                if len(characters) >= total_market_size or size < page_size or size == 0:
                    break

                page += 1
                time.sleep(0.2)

            return True, characters, ""
        except Exception as e:
            return False, {}, str(e)

    def fetch_market_items(self) -> Tuple[bool, Dict[int, Dict[str, Any]], str]:
        """Tải danh sách vật phẩm bán trên sàn (Opcode 0x34CC)."""
        if not self.conn or not self.conn.is_connected:
            return False, {}, "Mất kết nối game"

        items: Dict[int, Dict[str, Any]] = {}
        page = 1
        page_size = 100
        total_market_size = None

        try:
            self.drain_socket()
            self.packet_queue = [p for p in self.packet_queue if p.type != 0x34CC]

            while True:
                msg = Message(0x34CC)
                msg.putByte(0)    # type: Tất cả (0) - chuẩn Define.goodsType
                msg.putByte(-1)   # grade: Tất cả (-1)
                msg.putByte(0)    # level: Tất cả (0) - không để -1 vì server sẽ lọc mất vũ khí/trang bị/pet
                msg.putByte(2)    # sortObj: CREATETIME (2)
                msg.putByte(-1)   # sortType: DESC (-1)
                msg.putString("") # name: Tất cả ("")
                msg.putShort(page_size)
                msg.putInt(page)

                res = None
                for attempt in range(2):
                    if not self.conn.send_message(msg):
                        return False, {}, "Gửi gói tin 0x34CC thất bại"

                    res = self.wait_for_packet(0x34CC, timeout=6.0)
                    if res:
                        break
                    time.sleep(0.2)

                if not res:
                    return False, {}, f"Không nhận được phản hồi trang {page} (0x34CC) từ Server"

                all_size = res.getInt()
                size = res.getShort()
                if total_market_size is None:
                    total_market_size = all_size

                for _ in range(size):
                    if not res.hasRemaining():
                        break
                    it = Item.from_bytes(res)
                    good_id = res.getLong()
                    qty = res.getByte() & 0xFF
                    gold = max(0, res.getInt())
                    silver = max(0, res.getInt())
                    exp_time = res.getLong()

                    if not it.name or '\x00' in it.name or len(it.name) > 60 or qty <= 0 or good_id <= 0:
                        continue

                    cat = get_sgd_broad_category(it.type)
                    clean_name = normalize_item_name(it.name)
                    if not clean_name:
                        continue
                    item_record: Dict[str, Any] = {
                        "good_id": good_id,
                        "item_id": it.id,
                        "name": clean_name,
                        "grade": it.grade,
                        "type": it.type,
                        "category": cat,
                        "req_lv": max(0, int(getattr(it, "reqLv", 0) or 0)),
                        "star": max(0, int(getattr(it, "star", 0) or 0)),
                        "ascension_star": max(0, int(getattr(it, "ascensionStar", 0) or 0)),
                        "upgrade_star": max(0, int(getattr(it, "upgradeStar", 0) or 0)),
                        "attach_done": max(0, int(getattr(it, "attachDone", 0) or 0)),
                        "is_upgrade": bool(getattr(it, "isUpgradeItem", False)),
                        "has_enchant": bool((getattr(it, "enchantPower1", 0) or 0) > 0 or (getattr(it, "enchantPower2", 0) or 0) > 0),
                        "quantity": qty,
                        "gold": gold,
                        "silver": silver,
                        "first_seen": time.time()
                    }
                    item_record["enhance_tier"] = _get_equipment_enhance_tier(item_record)
                    items[good_id] = item_record

                if len(items) >= total_market_size or size < page_size or size == 0:
                    break

                page += 1
                time.sleep(0.2)

            return True, items, ""
        except Exception as e:
            return False, {}, str(e)

    def sleep_worker(self, duration: float):
        """Vòng lặp ngủ của worker: gửi heartbeat 8s, kiểm tra nhận quà online, và drain socket 50ms."""
        t_end = time.time() + duration
        while time.time() < t_end and self.is_running and self.manager.is_running:
            if getattr(self, "is_event_busy", False):
                time.sleep(0.05)
                continue
            now = time.time()
            if now - self.last_heartbeat >= 8.0:
                self.send_heartbeat()
            if now >= self.next_reward_check_time and self.is_connected:
                self.check_and_claim_online_reward()
            self.drain_socket()
            time.sleep(0.05)

    def run_worker_loop(self):
        """Vòng lặp nghiệp vụ riêng của từng Worker."""
        reconnect_attempts = 0
        while self.is_running and self.manager.is_running:
            try:
                if not self.conn or not self.conn.is_connected:
                    if self.is_phone_active and time.time() < self.phone_active_until:
                        rem = int(self.phone_active_until - time.time())
                        print(f"[{time.strftime('%H:%M:%S')}] \033[93m[ACC {self.idx}: {self.user}]\033[0m Đang nhường phiên chơi trên điện thoại (còn {rem}s)...", flush=True)
                        sleep_time = min(10.0, max(1.0, self.phone_active_until - time.time()))
                        time.sleep(sleep_time)
                        continue
                    self.is_phone_active = False

                    reconnect_attempts += 1
                    backoff = min(60.0, 3.0 * (1.8 ** min(reconnect_attempts - 1, 5)))
                    time.sleep(backoff)

                    if not self.login_and_enter():
                        if reconnect_attempts >= 5:
                            if not getattr(self, "use_direct_ip", False):
                                print(f"\033[91m[ACC {self.idx}: {self.user}]\033[0m Đổi proxy do kết nối lại thất bại ({reconnect_attempts})...")
                                self._rotate_proxy()
                            else:
                                print(f"\033[93m[ACC {self.idx}: {self.user}]\033[0m Kết nối lại bằng IP Chính ({reconnect_attempts})...")
                            reconnect_attempts = 0
                        continue
                    reconnect_attempts = 0

                # Kiểm tra nhận quà online nếu đến mốc
                if time.time() >= self.next_reward_check_time and self.is_connected:
                    self.check_and_claim_online_reward()

                # Kiểm tra định kỳ mỗi 60s để đảm bảo nhân vật luôn ở Tân Nguyệt Trấn (Map 37)
                if time.time() - getattr(self, "last_map_check_time", 0.0) >= 60.0 and self.is_connected:
                    self.last_map_check_time = time.time()
                    curr_map = (self.char_info.map_name or "").strip() if self.char_info else ""
                    if not (("tân nguyệt trấn" in curr_map.lower() or "tan nguyet tran" in curr_map.lower() or curr_map.lower() == "tân nguyệt trấn") and "quảng trường" not in curr_map.lower()):
                        print(f"[{time.strftime('%H:%M:%S')}] \033[93m[ACC {self.idx}: {self.user}]\033[0m Nhân vật đang ở map '{curr_map}'. Tự động dịch chuyển về đúng map Tân Nguyệt Trấn...", flush=True)
                        self.ensure_npc_and_map()

                # Kiểm tra và tự động thực hiện Rút Thưởng hoặc Vấn Đáp nếu đến lịch
                if not self.is_event_busy:
                    if self.is_lottery_due():
                        self.execute_in_session_lottery()
                    elif self.is_quiz_due():
                        self.execute_in_session_quiz()

                if self.is_event_busy:
                    time.sleep(0.5)
                    continue

                scan_int = getattr(self.manager, "scan_interval", 15.0)
                if self.role == "char":
                    self.manager.do_character_scan_step(self)
                    self.sleep_worker(scan_int)

                elif self.role == "item":
                    self.manager.do_item_scan_step(self)
                    self.sleep_worker(scan_int)

                elif self.role == "both":
                    self.manager.do_character_scan_step(self)
                    self.sleep_worker(min(2.0, scan_int / 2))
                    self.manager.do_item_scan_step(self)
                    self.sleep_worker(max(1.0, scan_int - 2.0))

                else:
                    self.sleep_worker(1.0)
            except Exception as e:
                print(f"[ACC {self.idx}: {self.user}] [LỖI NGOẠI LỆ WORKER] {e}", flush=True)
                try:
                    from core.tele_notifier import TeleNotifier
                    TeleNotifier.get_instance().send_error_alert(f"Ngoại Lệ Worker (Acc {self.idx})", f"Tài khoản: {self.user}\nLỗi: {e}", source=f"Watcher Worker {self.idx}", cooldown=60.0)
                except Exception:
                    pass
                time.sleep(2.0)


class MultiAccountMarketWatcher:
    """
    Quản lý đồng thời tối đa 4 tài khoản giám sát và đoạt lì xì:
    - Acc 1: Quét Nhân vật (30s)
    - Acc 2: Quét Vật phẩm (30s)
    - Acc 3 & 4: Treo game trực tuyến & Bắt lì xì
    - Cả 4 Acc: Tự động đoạt Lì Xì đồng thời khi có người phát.
    """
    def __init__(
        self,
        accounts_file: str = "accountsNV.txt",
        storage_char_file: str = "storage/market_characters.json",
        storage_item_file: str = "storage/market_items.json",
        telegram_bot = None
    ):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.accounts_file = self._resolve_path(accounts_file)
        self.storage_char_file = self._resolve_path(storage_char_file)
        self.storage_item_file = self._resolve_path(storage_item_file)
        self.storage_price_file = self._resolve_path("storage/item_price_history.json")
        self.config_path = self._resolve_path("config_247.json")
        self._cached_mkt_cfg: Optional[Dict[str, Any]] = None
        self._cached_cfg_mtime: float = 0.0

        self.price_tracker = ItemPriceTracker(db_path=self.storage_price_file)
        self.telegram_bot = telegram_bot
        if telegram_bot:
            AppNotifier.set_bot(telegram_bot)

        self.workers: List[WatcherWorker] = []
        self.is_running = False

        self.known_characters: Dict[int, Dict[str, Any]] = {}
        self.known_items: Dict[int, Dict[str, Any]] = {}
        self.initialized_chars = False
        self.initialized_items = False
        self.has_saved_chars = False
        self.has_saved_items = False

        self.in_place_active = False
        self.print_lock = threading.Lock()
        self.char_lock = threading.Lock()
        self.item_lock = threading.Lock()

        mkt_cfg = self._load_market_config()
        self.scan_interval = float(mkt_cfg.get("scan_interval_sec", 15.0))

        self.next_char_scan_time = time.time() + self.scan_interval
        self.next_item_scan_time = time.time() + self.scan_interval

    def _resolve_path(self, path_str: str) -> str:
        if not path_str or os.path.isabs(path_str):
            return path_str
        return os.path.join(self.base_dir, path_str)

    def _load_market_config(self) -> Dict[str, Any]:
        """Nạp cấu hình market_watcher từ config_247.json (cache theo mtime)."""
        defaults: Dict[str, Any] = {
            "scan_interval_sec": 15.0,
            "item_notify": True,
            "notify_mode": "deals_only",
            "item_min_grade": 0,
            "tracked_categories": ["Tất cả"],
            "min_discount_pct": 30.0,
            "min_floor_discount_pct": 15.0,
            "min_profit_gold": 10.0,
            "min_samples": 3,
            "silver_per_gold": 50000.0,
            "watchlist_keywords": [],
            "excluded_categories": [],
            "excluded_category_exceptions": [],
            "category_min_grade": {}
        }
        try:
            if os.path.exists(self.config_path):
                mtime = os.path.getmtime(self.config_path)
                if self._cached_mkt_cfg is not None and mtime == self._cached_cfg_mtime:
                    return self._cached_mkt_cfg
                with open(self.config_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                mw = raw.get("market_watcher", {})
                merged = dict(defaults)
                if isinstance(mw, dict):
                    merged.update(mw)
                self._cached_mkt_cfg = merged
                self._cached_cfg_mtime = mtime
                return merged
        except Exception:
            pass
        return defaults

    def load_saved_data(self, file_path: str) -> Dict[int, Dict[str, Any]]:
        if not os.path.exists(file_path):
            return {}
        try:
            import json
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {int(k): v for k, v in data.items()}
        except Exception:
            return {}

    def save_data(self, file_path: str, data_dict: Dict[int, Dict[str, Any]]):
        try:
            import json
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            tmp_file = f"{file_path}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, ensure_ascii=False, indent=2)
            if os.path.exists(file_path):
                os.replace(tmp_file, file_path)
            else:
                os.rename(tmp_file, file_path)
        except Exception:
            pass

    def load_accounts(self) -> List[Tuple[str, str, Optional[str]]]:
        if not os.path.exists(self.accounts_file):
            print(f"\033[91m[LỖI]\033[0m Không tìm thấy file '{self.accounts_file}'!")
            return []

        raw_accounts = []
        with open(self.accounts_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) >= 2:
                    u = parts[0].strip()
                    p = parts[1].strip()
                    prx = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
                    raw_accounts.append((u, p, prx))
                if len(raw_accounts) >= 5:
                    break

        if not raw_accounts:
            print(f"\033[91m[LỖI]\033[0m File '{self.accounts_file}' không có tài khoản hợp lệ!")
            return []

        accounts_with_proxy = assign_proxies_for_accounts(raw_accounts)
        return accounts_with_proxy

    def broadcast_luck_money(self, luck_id: int, sender_name: str, sender_id: int, note: str):
        """Kích hoạt tất cả Worker đang online cùng phóng gói tin đoạt Lì Xì ngay lập tức!"""
        for w in self.workers:
            if w.is_connected and w.conn:
                threading.Thread(
                    target=w.snatch_luck_money,
                    args=(luck_id, sender_name, sender_id, note),
                    daemon=True
                ).start()

    def clear_in_place_line(self):
        """Xóa sạch dòng trạng thái tại chỗ trước khi in dòng log mới."""
        if self.in_place_active:
            try:
                import shutil
                cols = shutil.get_terminal_size((80, 20)).columns
                sys.stdout.write("\r" + " " * (cols - 1) + "\r")
                sys.stdout.flush()
            except Exception:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
            self.in_place_active = False

    def on_luck_money_won(self, worker: WatcherWorker, gold_won: int, new_total_gold: int, sender_name: str, note: str):
        """CHỈ HIỂN THỊ KHI ĐOẠT THÀNH CÔNG VÀ NÊU RÕ TÀI KHOẢN NÀO ĐÃ ĐOẠT ĐƯỢC."""
        with self.print_lock:
            self.clear_in_place_line()

            curr_time = time.strftime("%H:%M:%S")
            char_name = worker.char_info.name if worker.char_info else f"Acc {worker.idx}"
            sender_display = sender_name if sender_name else "Người chơi"
            note_display = note if note else "Không có lời nhắn"

            print(f"[{curr_time}] \033[92m🎉 [ACC {worker.idx}: {worker.user} | {char_name}] ĐOẠT LÌ XÌ THÀNH CÔNG!\033[0m "
                  f"Nhận được: \033[93m+{gold_won:,} VÀNG\033[0m (Tổng hiện có: \033[93m{new_total_gold:,} Vàng\033[0m) | "
                  f"Người phát: \033[96m{sender_display}\033[0m | Lời nhắn: '\033[95m{note_display}\033[0m'", flush=True)

            s_char = html.escape(str(char_name))
            s_user = html.escape(str(worker.user))
            s_sender = html.escape(str(sender_display))
            s_note = html.escape(str(note_display))
            WindowsNotifier.send_toast(
                f"🧧 [{s_char}] ĐOẠT LÌ XÌ THÀNH CÔNG!",
                f"• Nhân vật: <b>{s_char}</b> (Acc: <code>{s_user}</code>)\n"
                f"• Số Vàng nhận: <b>+{gold_won:,} VÀNG</b>\n"
                f"• Tổng Vàng hiện tại: <b>{new_total_gold:,} Vàng</b>\n"
                f"• Người phát: <code>{s_sender}</code>\n"
                f"• Lời chúc: <i>'{s_note}'</i>"
            )

    def notify_npc_error_telegram(self, worker: WatcherWorker, target_npc: str, error_detail: str):
        """Gửi thông báo lỗi lên Telegram khi không tìm thấy NPC tại Tân Nguyệt Trấn."""
        now = time.time()
        if now - getattr(worker, "last_npc_error_notify", 0.0) < 300.0:
            return  # Cooldown 5 phút giữa các lần cảnh báo của cùng 1 worker để tránh spam
        worker.last_npc_error_notify = now

        char_name = worker.char_info.name if worker.char_info else f"Acc {worker.idx}"
        char_lv = getattr(worker.char_info, "level", 0) if worker.char_info else 0
        curr_time = datetime.now(VN_TZ).strftime("%H:%M:%S %d/%m/%Y")

        s_user = html.escape(str(worker.user))
        s_char = html.escape(str(char_name))
        s_npc = html.escape(str(target_npc))
        s_err = html.escape(str(error_detail))

        msg = (
            f"⚠️ <b>[CẢNH BÁO THEO DÕI SỞ GIAO DỊCH]</b>\n"
            f"⏱ <i>Thời gian: {curr_time}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Tài khoản:</b> <code>{s_user}</code> (Acc {worker.idx})\n"
            f"🎮 <b>Nhân vật:</b> <b>{s_char}</b> (Cấp {char_lv})\n"
            f"🎯 <b>Mục tiêu:</b> NPC <b>{s_npc}</b>\n"
            f"📍 <b>Bản đồ:</b> Tân Nguyệt Trấn (Quảng Trường Tân Nguyệt)\n"
            f"❌ <b>Trạng thái lỗi:</b> <i>{s_err}</i>\n\n"
            f"💡 <i>Tool đã tự động dịch chuyển nhân vật đến map Tân Nguyệt Trấn nhưng vẫn không tìm thấy NPC hoặc không mở được Sở Giao Dịch!</i>"
        )

        sent = False
        if getattr(self, "telegram_bot", None):
            try:
                self.telegram_bot.send_message(msg)
                sent = True
            except Exception:
                pass

        if not sent:
            try:
                AppNotifier._send_telegram(
                    f"CẢNH BÁO: KHÔNG TÌM THẤY {target_npc}",
                    f"Acc {worker.idx} ({worker.user} | {char_name}) tại Tân Nguyệt Trấn không tìm thấy NPC {target_npc}.",
                    f"Chi tiết: {error_detail}"
                )
            except Exception:
                pass

    def _format_single_deal_toast(self, grp: Dict[str, Any], silver_per_gold: float) -> Tuple[str, str, str]:
        it = grp["sample"]
        cnt = int(grp.get("count", 1) or 1)
        lot_toast = f" ({cnt} lô)" if cnt > 1 else ""
        grade_info = get_grade_info(it["grade"])
        icon = grade_info.get("icon", "⚪")
        cat = it.get("category", get_sgd_broad_category(it.get("type", -1)))
        cat_icon = get_category_icon(cat)
        cat_tag = f"[{cat_icon} {cat}]"
        price_str = format_price_str(it["gold"], it["silver"])
        analysis = grp.get("analysis", {})
        enh_badge = format_enhance_badge(it)

        unit_info = ""
        if it["quantity"] > 1:
            if it["gold"] > 0:
                u_gold = round(it["gold"] / it["quantity"], 1)
                unit_info = f" <i>(Đơn giá: {u_gold:,} Vàng/món)</i>"
            elif it["silver"] > 0:
                u_silver = round(it["silver"] / it["quantity"])
                unit_info = f" <i>(Đơn giá: {u_silver:,} Bạc/món)</i>"

        currency = analysis.get("currency", "gold" if it.get("gold", 0) > 0 else "silver")
        market_context = []
        ref_samples = int(analysis.get("ref_samples", 0) or 0)

        if currency == "gold":
            m_med_g = analysis.get("market_gold_median", 0.0)
            live_fl_g = analysis.get("live_gold_floor")
            if m_med_g and m_med_g > 0:
                market_context.append(f"Trung vị: <b>{m_med_g:,.1f} V/món</b>")
            if live_fl_g is not None and live_fl_g > 0:
                market_context.append(f"Sàn đang bán: <b>{live_fl_g:,.1f} V/món</b>")
        else:
            m_med_g = analysis.get("market_gold_median", 0.0)
            live_fl_g = analysis.get("live_gold_floor")
            if m_med_g and m_med_g >= 20.0:
                u_s = analysis.get("unit_silver", 0.0)
                eq_g = round(u_s / silver_per_gold, 1) if (u_s and silver_per_gold > 0) else 0.0
                if eq_g > 0:
                    market_context.append(f"Quy đổi: <b>~{eq_g:,.1f} V/món</b>")
                market_context.append(f"Trung vị Vàng: <b>{m_med_g:,.1f} V/món</b>")
                if live_fl_g is not None and live_fl_g > 0:
                    market_context.append(f"Sàn Vàng: <b>{live_fl_g:,.1f} V/món</b>")
            else:
                m_med_s = analysis.get("market_silver_median", 0.0)
                live_fl_s = analysis.get("live_silver_floor")
                if m_med_s and m_med_s > 0:
                    market_context.append(f"Trung vị: <b>{m_med_s:,.0f} Bạc/món</b>")
                if live_fl_s is not None and live_fl_s > 0:
                    market_context.append(f"Sàn đang bán: <b>{live_fl_s:,.0f} Bạc/món</b>")

        if ref_samples > 0:
            market_context.append(f"Mẫu: {ref_samples}")

        is_bargain = bool(grp.get("is_bargain") or analysis.get("is_bargain"))
        is_watchlist = bool(grp.get("is_watchlist"))
        disc = float(grp.get("discount_percent", 0.0) or 0.0)
        disc_tag = f" (-{disc:.0f}%)" if disc > 0 else ""

        if is_bargain and is_watchlist:
            title = f"🔥🎯 SĂN KÈO RẺ WATCHLIST!{disc_tag}"
        elif is_bargain:
            title = f"🔥 SĂN ĐỒ RẺ THỊ TRƯỜNG!{disc_tag}"
        elif is_watchlist:
            title = f"🎯 WATCHLIST: {cat_tag} VẬT PHẨM QUAN TÂM!"
        else:
            title = f"SỞ GIAO DỊCH: {cat_tag} VẬT PHẨM MỚI!"

        s_itname = html.escape(normalize_item_name(str(it["name"])))
        s_enh = html.escape(enh_badge)
        line1 = f"{icon} <b>{s_itname}</b>{s_enh} x{it['quantity']}{lot_toast} • <i>{html.escape(cat_tag)}</i>"
        lines2 = [f"💰 <b>Giá bán:</b> <b>{price_str}</b>{unit_info}"]

        if market_context:
            lines2.append("📊 " + " | ".join(market_context))

        est_g = float(grp.get("estimated_profit_gold", 0.0) or analysis.get("estimated_profit_gold", 0.0) or 0.0)
        est_s = float(grp.get("estimated_profit_silver", 0.0) or analysis.get("estimated_profit_silver", 0.0) or 0.0)
        if is_bargain:
            if est_g > 0:
                lines2.append(f"💎 <b>Lãi ước tính:</b> <b>+{est_g:,.1f} Vàng</b>")
            elif est_s > 0:
                lines2.append(f"💎 <b>Lãi ước tính:</b> <b>+{est_s:,.0f} Bạc</b>")

        reasons = [html.escape(str(r)) for r in grp.get("reasons", [])]
        if reasons:
            lines2.append(f"💡 <b>Đánh giá:</b> {', '.join(reasons)}")

        return title, line1, "\n".join(lines2)

    def notify_new_items(self, grouped: List[Dict[str, Any]], total_count: int = 0):
        """Gửi thông báo Telegram khi có Kèo Rẻ / Watchlist (hoặc thêm đồ mới nếu bật notify_mode='all')."""
        if not grouped:
            return

        market_cfg = self._load_market_config()
        if not market_cfg.get("item_notify", True):
            return

        notify_mode = str(market_cfg.get("notify_mode", "deals_only")).strip().lower()
        min_grade = int(market_cfg.get("item_min_grade", 0) or 0)
        tracked_cats = market_cfg.get("tracked_categories", ["Tất cả"])
        excluded_cats = {
            str(c).strip().lower()
            for c in (market_cfg.get("excluded_categories") or [])
            if str(c).strip()
        }
        excluded_cat_exceptions = [
            str(kw).strip().lower()
            for kw in (market_cfg.get("excluded_category_exceptions") or [])
            if str(kw).strip()
        ]
        raw_cat_min = market_cfg.get("category_min_grade") or {}
        cat_min_grade_map: Dict[str, int] = {}
        if isinstance(raw_cat_min, dict):
            for k, v in raw_cat_min.items():
                ks = str(k).strip().lower()
                if ks:
                    try:
                        cat_min_grade_map[ks] = int(v)
                    except (TypeError, ValueError):
                        pass
        silver_per_gold = float(market_cfg.get("silver_per_gold", 50000.0) or 50000.0)

        allow_all_cats = True
        if isinstance(tracked_cats, list) and tracked_cats and "Tất cả" not in tracked_cats:
            allow_all_cats = False

        priority_groups: List[Dict[str, Any]] = []
        normal_groups: List[Dict[str, Any]] = []

        for grp in grouped:
            it = grp["sample"]
            cat = it.get("category", get_sgd_broad_category(it.get("type", -1)))
            is_watchlist = bool(grp.get("is_watchlist", False))
            norm_name_lower = normalize_item_name(str(it.get("name", ""))).strip().lower()
            is_cat_exception = any(
                bool(re.search(rf'(?<!\w){re.escape(exc)}(?!\w)', norm_name_lower))
                for exc in excluded_cat_exceptions
            )

            if cat.strip().lower() in excluded_cats and not (is_cat_exception or is_watchlist):
                continue
            if not allow_all_cats and cat not in tracked_cats and not (is_cat_exception or is_watchlist):
                continue

            analysis = grp.get("analysis", {})
            is_bargain = bool(grp.get("is_bargain", False) or analysis.get("is_bargain", False))
            grade = int(it.get("grade", 0) or 0)
            effective_min_grade = max(min_grade, cat_min_grade_map.get(cat.strip().lower(), min_grade))

            grp["is_snipe"] = is_bargain
            grp["is_bargain"] = is_bargain
            grp["is_watchlist"] = is_watchlist
            grp["is_rare"] = False

            if is_bargain or is_watchlist:
                if is_watchlist or grade >= effective_min_grade:
                    priority_groups.append(grp)
            elif notify_mode == "all" and grade >= effective_min_grade:
                normal_groups.append(grp)

        # Ưu tiên gửi riêng từng Kèo Rẻ / Watchlist để không bị chìm thông tin định giá
        if priority_groups:
            priority_groups.sort(key=lambda g: (
                not g.get("is_watchlist", False),
                -float(g.get("estimated_profit_gold", 0.0) or 0.0),
                -float(g.get("discount_percent", 0.0) or 0.0),
                -int(g["sample"].get("grade", 0) or 0)
            ))

            if len(priority_groups) <= 5:
                for grp in priority_groups:
                    title, line1, line2 = self._format_single_deal_toast(grp, silver_per_gold)
                    WindowsNotifier.send_toast(title, line1, line2)
            else:
                summary_lines = []
                for grp in priority_groups:
                    it = grp["sample"]
                    analysis = grp.get("analysis", {})
                    cnt = int(grp.get("count", 1) or 1)
                    lot_str = f" (x{cnt} lô)" if cnt > 1 else ""
                    icon = get_grade_info(it["grade"]).get("icon", "⚪")
                    cat = it.get("category", get_sgd_broad_category(it.get("type", -1)))
                    cat_icon = get_category_icon(cat)
                    price_str = format_price_str(it["gold"], it["silver"])
                    disc = float(grp.get("discount_percent", 0.0) or 0.0)
                    badge = "🎯" if grp.get("is_watchlist") else ""
                    if grp.get("is_bargain"):
                        badge += f"🔥[-{disc:.0f}%] " if disc > 0 else "🔥[RẺ] "
                    else:
                        badge += " "

                    m_name = html.escape(normalize_item_name(str(it["name"])))
                    m_enh = html.escape(format_enhance_badge(it))
                    med_g = analysis.get("market_gold_median", 0.0)
                    est_g = float(grp.get("estimated_profit_gold", 0.0) or 0.0)
                    extra_parts = []
                    if med_g and med_g > 0:
                        extra_parts.append(f"TV: {med_g:,.1f}V")
                    if est_g > 0:
                        extra_parts.append(f"Lãi: +{est_g:,.1f}V")
                    extra_str = f" ({', '.join(extra_parts)})" if extra_parts else ""

                    summary_lines.append(
                        f"{badge}[{cat_icon}{html.escape(cat)}] {icon} <b>{m_name}</b>{m_enh} x{it['quantity']}{lot_str} — <b>{price_str}</b>{extra_str}"
                    )

                CHUNK_SIZE = 8
                chunks = [summary_lines[i:i + CHUNK_SIZE] for i in range(0, len(summary_lines), CHUNK_SIZE)]
                total_parts = len(chunks)
                deal_total = sum(int(g.get("count", 1) or 1) for g in priority_groups)
                for part_idx, chunk in enumerate(chunks, 1):
                    part_tag = f" ({part_idx}/{total_parts})" if total_parts > 1 else ""
                    title = f"🔥 PHÁT HIỆN {deal_total} KÈO RẺ / WATCHLIST!{part_tag}"
                    line1 = f"Danh sách cơ hội nổi bật (Phần {part_idx}/{total_parts}):" if total_parts > 1 else f"Có <b>{deal_total}</b> lô vật phẩm đạt tiêu chí săn:"
                    WindowsNotifier.send_toast(title, line1, "\n".join(chunk))

        # Chỉ gửi danh sách vật phẩm thường nếu cấu hình notify_mode = 'all'
        if notify_mode == "all" and normal_groups:
            normal_groups.sort(key=lambda g: -int(g["sample"].get("grade", 0) or 0))
            item_lines = []
            for grp in normal_groups:
                it = grp["sample"]
                cnt = int(grp.get("count", 1) or 1)
                lot_str = f" (x{cnt} lô)" if cnt > 1 else ""
                icon = get_grade_info(it["grade"]).get("icon", "⚪")
                cat = it.get("category", get_sgd_broad_category(it.get("type", -1)))
                cat_icon = get_category_icon(cat)
                price_str = format_price_str(it["gold"], it["silver"])
                unit_info = ""
                if it["quantity"] > 1:
                    if it["gold"] > 0:
                        unit_info = f" <i>({round(it['gold'] / it['quantity'], 1):,} V/món)</i>"
                    elif it["silver"] > 0:
                        unit_info = f" <i>({round(it['silver'] / it['quantity']):,} Bạc/món)</i>"
                m_itname = html.escape(normalize_item_name(str(it["name"])))
                m_enh = html.escape(format_enhance_badge(it))
                item_lines.append(
                    f"[{cat_icon}{html.escape(cat)}] {icon} <b>{m_itname}</b>{m_enh} x{it['quantity']}{lot_str} — <b>{price_str}</b>{unit_info}"
                )

            CHUNK_SIZE = 10
            chunks = [item_lines[i:i + CHUNK_SIZE] for i in range(0, len(item_lines), CHUNK_SIZE)]
            total_parts = len(chunks)
            normal_total = sum(int(g.get("count", 1) or 1) for g in normal_groups)
            for part_idx, chunk in enumerate(chunks, 1):
                if total_parts == 1:
                    title = f"SỞ GIAO DỊCH: +{normal_total} VẬT PHẨM MỚI"
                    line1 = f"Tổng cộng có <b>+{normal_total}</b> vật phẩm mới đăng sàn:"
                else:
                    title = f"SỞ GIAO DỊCH: +{normal_total} VẬT PHẨM MỚI ({part_idx}/{total_parts})"
                    line1 = f"Danh sách vật phẩm mới tiếp theo (Phần {part_idx}/{total_parts}):"
                WindowsNotifier.send_toast(title, line1, "\n".join(chunk))
    def print_idle_status(self):

        """In log trạng thái gộp tại chỗ (in-place) không nhảy dòng, chỉ cập nhật thời gian đếm ngược."""
        if not sys.stdout.isatty():
            return
        with self.print_lock:
            curr_time = time.strftime("%H:%M:%S")
            now = time.time()
            rem_char = max(0, int(self.next_char_scan_time - now))
            rem_item = max(0, int(self.next_item_scan_time - now))

            online_count = sum(1 for w in self.workers if w.is_connected)
            total_workers = len(self.workers)

            try:
                import shutil
                cols = shutil.get_terminal_size((80, 20)).columns
            except Exception:
                cols = 80

            # Hoàn toàn KHÔNG hiển thị Lì Xì tại đây (chỉ hiển thị khi đoạt được lì xì thành công)
            msg = (
                f"\r[{curr_time}] [THEO DÕI] "
                f"Nhân vật: \033[93m{len(self.known_characters)}\033[0m ({rem_char}s) | "
                f"Vật phẩm: \033[93m{len(self.known_items)}\033[0m ({rem_item}s) | "
                f"Online: \033[92m{online_count}/{total_workers}\033[0m"
            )

            import re
            plain = re.sub(r'\033\[[0-9;]*m', '', msg.lstrip('\r'))
            if len(plain) > cols - 1:
                short_text = f"\r[{curr_time}] NV:{len(self.known_characters)}({rem_char}s)|Đồ:{len(self.known_items)}({rem_item}s)|{online_count}/{total_workers}"
                short_plain = short_text.lstrip('\r')
                pad = max(0, cols - 1 - len(short_plain))
                sys.stdout.write(f"{short_text[:cols-1]}{' ' * pad}\r")
            else:
                pad = max(0, cols - 1 - len(plain))
                sys.stdout.write(f"{msg}{' ' * pad}\r")
            sys.stdout.flush()
            self.in_place_active = True

    def _handle_scan_error(self, worker: WatcherWorker, err: str, kind_label: str, alert_title: str, source: str):
        """Ghi nhận lỗi quét chợ, chỉ cảnh báo Telegram khi lỗi >= 3 lần liên tiếp và đóng socket nếu bị đứt."""
        worker.consecutive_scan_errors = getattr(worker, "consecutive_scan_errors", 0) + 1
        with self.print_lock:
            self.clear_in_place_line()
            print(f"\033[91m[ACC {worker.idx} - {kind_label} (Lần {worker.consecutive_scan_errors})]\033[0m {err}", flush=True)

        err_low = (err or "").lower()
        conn_dead = (
            not worker.conn
            or not worker.conn.is_connected
            or "mất kết nối" in err_low
            or "gửi gói tin" in err_low
        )
        if conn_dead:
            worker._close_conn()

        if worker.consecutive_scan_errors >= 3:
            try:
                from core.tele_notifier import TeleNotifier
                TeleNotifier.get_instance().send_error_alert(
                    f"{alert_title} (Acc {worker.idx})",
                    f"Tài khoản: {worker.user}\nLỗi liên tiếp: {worker.consecutive_scan_errors} lần\nLỗi: {err}",
                    source=source,
                    cooldown=120.0,
                )
            except Exception:
                pass

        if (not conn_dead) and worker.conn and worker.conn.is_connected and worker.consecutive_scan_errors >= 2:
            worker.ensure_npc_and_map()

    def do_character_scan_step(self, worker: WatcherWorker):
        """Tiến trình quét Sở Giao Dịch Nhân Vật (Acc 1)."""
        self.next_char_scan_time = time.time() + getattr(self, "scan_interval", 15.0)
        ok, current_chars, err = worker.fetch_market_characters()
        if not ok:
            self._handle_scan_error(worker, err, "LỖI QUÉT NHÂN VẬT", "Lỗi Quét Nhân Vật", "SGD Nhân Vật")
            return
        worker.consecutive_scan_errors = 0

        curr_time = time.strftime("%H:%M:%S")
        with self.char_lock:
            if not self.initialized_chars:
                if not self.has_saved_chars:
                    self.known_characters = current_chars
                    self.save_data(self.storage_char_file, self.known_characters)
                    with self.print_lock:
                        self.clear_in_place_line()
                        print(f"[{curr_time}] \033[92m[KHỞI TẠO NHÂN VẬT]\033[0m Đã nạp \033[93m{len(self.known_characters)}\033[0m nhân vật vào bộ nhớ.", flush=True)
                else:
                    curr_ids = set(current_chars.keys())
                    known_ids = set(self.known_characters.keys())
                    new_ids = curr_ids - known_ids
                    rem_ids = known_ids - curr_ids

                    with self.print_lock:
                        self.clear_in_place_line()
                        print(f"[{curr_time}] \033[96m[SO SÁNH NHÂN VẬT LẦN TRƯỚC]\033[0m Tổng sàn: \033[93m{len(current_chars)}\033[0m | Mới: \033[92m+{len(new_ids)}\033[0m | Đã gỡ: \033[91m-{len(rem_ids)}\033[0m", flush=True)
                        if new_ids:
                            for aid in new_ids:
                                c = current_chars[aid]
                                s_cname = html.escape(str(c['name']))
                                s_cjob = html.escape(str(c['job_name']))
                                WindowsNotifier.send_toast(
                                    f"SỞ GIAO DỊCH: NHÂN VẬT MỚI! - {c['price']:,} VÀNG",
                                    f"Tên: <b>{s_cname}</b> (Lv {c['level']} {s_cjob})",
                                    f"Giá bán: <b>{c['price']:,} Vàng</b>"
                                )
                                print(f"[{curr_time}] \033[92m[NHÂN VẬT MỚI (LẦN TRƯỚC)]\033[0m \033[93m{c['name']}\033[0m | Cấp: \033[96m{c['level']}\033[0m | Phái: \033[95m{c['job_name']}\033[0m | Giá: \033[93m{c['price']:,} Vàng\033[0m", flush=True)
                                self.known_characters[aid] = c
                        if rem_ids:
                            for aid in rem_ids:
                                self.known_characters.pop(aid, None)
                    self.save_data(self.storage_char_file, self.known_characters)
                self.initialized_chars = True
            else:
                curr_ids = set(current_chars.keys())
                known_ids = set(self.known_characters.keys())
                new_ids = curr_ids - known_ids
                rem_ids = known_ids - curr_ids

                if new_ids or rem_ids:
                    with self.print_lock:
                        self.clear_in_place_line()

                        if new_ids:
                            for aid in new_ids:
                                c = current_chars[aid]
                                s_cname = html.escape(str(c['name']))
                                s_cjob = html.escape(str(c['job_name']))
                                WindowsNotifier.send_toast(
                                    f"SỞ GIAO DỊCH: NHÂN VẬT MỚI! - {c['price']:,} VÀNG",
                                    f"Tên: <b>{s_cname}</b> (Lv {c['level']} {s_cjob})",
                                    f"Giá bán: <b>{c['price']:,} Vàng</b>"
                                )
                                print(f"[{curr_time}] \033[92m[NHÂN VẬT MỚI XUẤT HIỆN!]\033[0m \033[93m{c['name']}\033[0m | Cấp: \033[96m{c['level']}\033[0m | Phái: \033[95m{c['job_name']}\033[0m | Giá: \033[93m{c['price']:,} Vàng\033[0m", flush=True)
                                self.known_characters[aid] = c

                        if rem_ids:
                            for aid in rem_ids:
                                self.known_characters.pop(aid, None)

                        self.save_data(self.storage_char_file, self.known_characters)
                        print(f"[{curr_time}] \033[96m[CẬP NHẬT NHÂN VẬT]\033[0m Tổng: \033[93m{len(current_chars)}\033[0m | Mới: \033[92m+{len(new_ids)}\033[0m | Đã gỡ: \033[91m-{len(rem_ids)}\033[0m", flush=True)

    def _evaluate_new_item_groups(
        self,
        current_items: Dict[int, Dict[str, Any]],
        new_ids: Set[int],
        market_cfg: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Nhóm vật phẩm mới, định giá đối chiếu mặt bằng đang bán và lịch sử, rồi kiểm tra Watchlist."""
        grouped = group_identical_items(current_items, new_ids)
        if not grouped:
            return []

        live_index: Dict[Tuple[str, int, int], List[Tuple[int, float, float]]] = {}
        for gid, cur in current_items.items():
            qty = max(1, int(cur.get("quantity", 1) or 1))
            g = max(0, int(cur.get("gold", 0) or 0))
            s = max(0, int(cur.get("silver", 0) or 0))
            ug = round(g / qty, 2) if g > 0 else 0.0
            us = round(s / qty, 1) if (g == 0 and s > 0) else 0.0
            k = (normalize_item_name(cur.get("name", "")), int(cur.get("grade", 0) or 0), _get_equipment_enhance_tier(cur))
            live_index.setdefault(k, []).append((gid, ug, us))

        raw_wl = market_cfg.get("watchlist_keywords") or []
        watchlist_kws = [str(kw).strip().lower() for kw in raw_wl if str(kw).strip()]
        curr_time = time.strftime("%H:%M:%S")

        for grp in grouped:
            it = grp["sample"]
            cnt = grp["count"]
            gids = grp["gids"]
            grp_gid_set = set(gids)
            for gid in gids:
                self.known_items[gid] = current_items[gid]

            norm_name = normalize_item_name(it["name"])
            grade = int(it.get("grade", 0) or 0)
            tier = _get_equipment_enhance_tier(it)
            idx_key = (norm_name, grade, tier)

            other_gold: List[float] = []
            other_silver: List[float] = []
            for o_gid, o_ug, o_us in live_index.get(idx_key, []):
                if o_gid in grp_gid_set:
                    continue
                if o_ug > 0:
                    other_gold.append(o_ug)
                elif o_us > 0:
                    other_silver.append(o_us)

            analysis = self.price_tracker.process_item_listing(
                name=norm_name,
                grade=grade,
                quantity=it["quantity"],
                gold=it["gold"],
                silver=it["silver"],
                is_new_listing=True,
                good_id=gids[0],
                enhance_tier=tier,
                live_gold_prices=other_gold,
                live_silver_prices=other_silver,
                config=market_cfg,
                record_sample=True
            )

            # Giới hạn ghi tối đa thêm 2 mẫu giống hệt trong cùng đợt để tránh 1 người bán xả 20 lô làm méo median
            for extra_idx, extra_gid in enumerate(gids[1:]):
                ex_it = current_items[extra_gid]
                if extra_idx < 2:
                    self.price_tracker.process_item_listing(
                        name=norm_name,
                        grade=grade,
                        quantity=ex_it["quantity"],
                        gold=ex_it["gold"],
                        silver=ex_it["silver"],
                        is_new_listing=True,
                        good_id=extra_gid,
                        enhance_tier=tier,
                        config=market_cfg,
                        record_sample=True
                    )
                else:
                    self.price_tracker.mark_good_id_seen(extra_gid)

            name_lower = norm_name.lower()
            is_watchlist = bool(watchlist_kws and any(kw in name_lower for kw in watchlist_kws))

            reasons = list(analysis.get("reasons", []))
            if is_watchlist and "Khớp Watchlist săn đồ" not in reasons:
                reasons.append("Khớp Watchlist săn đồ")

            grp["analysis"] = analysis
            grp["is_bargain"] = bool(analysis.get("is_bargain", False))
            grp["is_watchlist"] = is_watchlist
            grp["is_rare"] = False
            grp["discount_percent"] = float(analysis.get("discount_percent", 0.0) or 0.0)
            grp["estimated_profit_gold"] = float(analysis.get("estimated_profit_gold", 0.0) or 0.0)
            grp["estimated_profit_silver"] = float(analysis.get("estimated_profit_silver", 0.0) or 0.0)
            grp["reasons"] = reasons

            cat = it.get("category", get_sgd_broad_category(it.get("type", -1)))
            cat_icon = get_category_icon(cat)
            cat_str = f"\033[94m[{cat_icon} {cat}]\033[0m "
            enh_badge = format_enhance_badge(it)
            colored_name = format_colored_item_name(f"{norm_name}{enh_badge}", grade)
            price_display = format_price_str(it["gold"], it["silver"])
            lot_str = f" \033[93m(x{cnt} lô)\033[0m" if cnt > 1 else ""
            tag = ""
            if grp["is_bargain"]:
                tag = f" \033[91m[KÈO RẺ -{grp['discount_percent']:.0f}%]\033[0m" if grp["discount_percent"] > 0 else " \033[91m[KÈO RẺ]\033[0m"
            elif is_watchlist:
                tag = " \033[96m[WATCHLIST]\033[0m"
            print(f"[{curr_time}] [VẬT PHẨM MỚI]{tag} {cat_str}{colored_name} x{it['quantity']}{lot_str} | Giá: {price_display}", flush=True)

        return grouped

    def do_item_scan_step(self, worker: WatcherWorker):
        """Tiến trình quét Sở Giao Dịch Vật Phẩm (Acc 2)."""
        market_cfg = self._load_market_config()
        self.scan_interval = float(market_cfg.get("scan_interval_sec", getattr(self, "scan_interval", 15.0)))
        self.next_item_scan_time = time.time() + self.scan_interval

        ok, current_items, err = worker.fetch_market_items()
        if not ok:
            self._handle_scan_error(worker, err, "LỖI QUÉT VẬT PHẨM", "Lỗi Quét Vật Phẩm", "SGD Vật Phẩm")
            return
        worker.consecutive_scan_errors = 0

        curr_time = time.strftime("%H:%M:%S")
        with self.item_lock:
            if not self.initialized_items:
                if not self.has_saved_items or (len(current_items) - len(self.known_items) > 100):
                    added = self.price_tracker.record_baseline_snapshot(current_items)
                    self.known_items = current_items
                    self.save_data(self.storage_item_file, self.known_items)
                    self.price_tracker.save_checkpoint()
                    with self.print_lock:
                        self.clear_in_place_line()
                        print(f"[{curr_time}] \033[92m[KHỞI TẠO VẬT PHẨM TOÀN SÀN]\033[0m Đã nạp \033[93m{len(self.known_items)}\033[0m vật phẩm (ghi mới \033[96m{added}\033[0m mẫu vào CSDL giá).", flush=True)
                else:
                    curr_ids = set(current_items.keys())
                    known_ids = set(self.known_items.keys())
                    new_ids = curr_ids - known_ids
                    rem_ids = known_ids - curr_ids

                    # Bổ sung baseline cho các món cũ trên sàn nếu chưa từng được ghi vào seen_good_ids
                    existing_items = {gid: it for gid, it in current_items.items() if gid not in new_ids}
                    self.price_tracker.record_baseline_snapshot(existing_items)

                    with self.print_lock:
                        self.clear_in_place_line()
                        print(f"[{curr_time}] \033[96m[SO SÁNH VẬT PHẨM LẦN TRƯỚC]\033[0m Tổng sàn: \033[93m{len(current_items)}\033[0m | Mới: \033[92m+{len(new_ids)}\033[0m | Đã gỡ: \033[91m-{len(rem_ids)}\033[0m", flush=True)
                        if new_ids:
                            grouped = self._evaluate_new_item_groups(current_items, new_ids, market_cfg)
                            self.notify_new_items(grouped, len(new_ids))

                        if rem_ids:
                            for gid in rem_ids:
                                self.known_items.pop(gid, None)
                    self.save_data(self.storage_item_file, self.known_items)
                    self.price_tracker.save_checkpoint()
                self.initialized_items = True
            else:
                curr_ids = set(current_items.keys())
                known_ids = set(self.known_items.keys())
                new_ids = curr_ids - known_ids
                rem_ids = known_ids - curr_ids

                if new_ids or rem_ids:
                    with self.print_lock:
                        self.clear_in_place_line()

                        if len(new_ids) > 100:
                            added = self.price_tracker.record_baseline_snapshot(current_items)
                            print(f"[{curr_time}] \033[93m[SGD - ĐỒNG BỘ LẠI]\033[0m Phát hiện số lượng thay đổi đột biến (+{len(new_ids)} món), cập nhật baseline (+{added} mẫu mới) để tránh spam.", flush=True)
                            self.known_items = current_items
                            self.save_data(self.storage_item_file, self.known_items)
                            self.price_tracker.save_checkpoint()
                            return

                        if new_ids:
                            grouped = self._evaluate_new_item_groups(current_items, new_ids, market_cfg)
                            self.notify_new_items(grouped, len(new_ids))

                        if rem_ids:
                            for gid in rem_ids:
                                self.known_items.pop(gid, None)

                        self.save_data(self.storage_item_file, self.known_items)
                        self.price_tracker.save_checkpoint()
                        print(f"[{curr_time}] \033[96m[CẬP NHẬT VẬT PHẨM]\033[0m Tổng: \033[93m{len(current_items)}\033[0m | Mới: \033[92m+{len(new_ids)}\033[0m | Đã gỡ: \033[91m-{len(rem_ids)}\033[0m", flush=True)

    def start(self):
        """Khởi động theo dõi (alias tương thích)."""
        self.run()

    def run(self):
        self.is_running = True

        print("\n" + "=" * 80)
        print("  GIÁM SÁT SỞ GIAO DỊCH & ĐOẠT LÌ XÌ ĐA TÀI KHOẢN (5 ACC)")
        print("  - Acc 1: Theo dõi Sở Giao Dịch Vật Phẩm (SGD - chu kỳ 30s) + Đoạt Lì Xì")
        print("  - Acc 2: Theo dõi Sở Giao Dịch Nhân Vật (chu kỳ 30s) + Đoạt Lì Xì")
        print("  - Acc 3, 4, 5: Treo trực tuyến giữ kết nối thế giới + Đoạt Lì Xì")
        print("  - 🧧 TẤT CẢ ACC ĐỒNG LOẠT ĐOẠT LÌ XÌ THẾ GIỚI KHI CÓ NGƯỜI PHÁT")
        print("  - Gửi thông báo ngay về Telegram khi đoạt được Lì Xì hoặc phát hiện đồ rẻ / NV mới")
        print("=" * 80 + "\n")

        accounts = self.load_accounts()
        if not accounts:
            return

        saved_chars = self.load_saved_data(self.storage_char_file)
        self.has_saved_chars = len(saved_chars) > 0
        if self.has_saved_chars:
            self.known_characters = saved_chars
            print(f"\033[92m[LƯU TRỮ NHÂN VẬT]\033[0m Đã nạp \033[93m{len(saved_chars)}\033[0m nhân vật từ file ({self.storage_char_file}).")

        saved_items = self.load_saved_data(self.storage_item_file)
        self.has_saved_items = len(saved_items) > 0
        if self.has_saved_items:
            self.known_items = saved_items
            print(f"\033[92m[LƯU TRỮ VẬT PHẨM]\033[0m Đã nạp \033[93m{len(saved_items)}\033[0m vật phẩm từ file ({self.storage_item_file}).")

        total_accs = len(accounts)
        print(f"\n\033[93m[KHỞI TẠO ĐA TÀI KHOẢN]\033[0m Đã nạp \033[92m{total_accs}\033[0m tài khoản:")

        for i, (user, pwd, proxy) in enumerate(accounts):
            idx = i + 1
            u_clean = user.strip().lower()
            if "thinht7113" in u_clean:
                role = "item"
            elif "toilabaon" in u_clean:
                role = "char"
            elif "thinht7114" in u_clean:
                role = "idle"
            elif total_accs == 1:
                role = "both"
            elif i == 0:
                role = "char"
            elif i == 1:
                role = "item"
            else:
                role = "idle"

            worker = WatcherWorker(idx, user, pwd, proxy, role, self)
            self.workers.append(worker)

            prx_str = f" | Proxy: \033[95m{proxy}\033[0m" if proxy else " | \033[92mIP Chính (Trực tiếp)\033[0m"
            print(f"  - [ACC {idx}: \033[92m{user}\033[0m] Vai trò: \033[96m{worker.get_role_display()}\033[0m{prx_str}")

        print("\n\033[93m[KẾT NỐI]\033[0m Đang tiến hành đăng nhập các tài khoản vào game...")

        for worker in self.workers:
            worker.is_running = True
            ok = worker.login_and_enter()
            if not ok:
                print(f"\033[93m[CẢNH BÁO]\033[0m Acc {worker.idx} ({worker.user}) đăng nhập thất bại, sẽ tự thử lại ngầm.")
            time.sleep(1.0)

        for worker in self.workers:
            t = threading.Thread(target=worker.run_worker_loop, daemon=True)
            worker.thread = t
            t.start()

        print("\n\033[92m[THEO DÕI HOẠT ĐỘNG]\033[0m Tất cả tài khoản đã sẵn sàng! Đang quét chợ & trực chiến Lì Xì...\n")

        try:
            while self.is_running:
                self.print_idle_status()
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def trigger_in_session_lottery(self):
        """Kích hoạt Rút Thưởng trên tất cả các Worker đang trực chiến."""
        threads = []
        for w in self.workers:
            if w.is_connected and w.conn:
                t = threading.Thread(target=w.execute_in_session_lottery, daemon=True)
                threads.append(t)
                t.start()
        return threads

    def trigger_in_session_quiz(self):
        """Kích hoạt Vấn Đáp trên tất cả các Worker đang trực chiến."""
        threads = []
        for w in self.workers:
            if w.is_connected and w.conn:
                t = threading.Thread(target=w.execute_in_session_quiz, daemon=True)
                threads.append(t)
                t.start()
        return threads

    def stop(self):
        self.is_running = False
        self.clear_in_place_line()

        for w in self.workers:
            w.is_running = False
            if w.conn:
                try:
                    w.conn.close()
                except Exception:
                    pass

        print("\n\033[93m[ĐÃ DỪNG]\033[0m Đã dừng tất cả các luồng theo dõi.")


def run_watcher(accounts_file: str = "accountsNV.txt"):
    watcher = MultiAccountMarketWatcher(accounts_file=accounts_file)
    watcher.run()


if __name__ == "__main__":
    run_watcher()
