"""
PVTK 24/7 Bot Automation System - Main Entry Point.
Fully automated headless operation designed for 24/7 VPS deployment.
Integrated with Telegram Bot 2-way remote control.
"""

import sys
import os
import time
import json
import signal
import socket
import html

# Force IPv4 preference for getaddrinfo to bypass broken IPv6 routes to Telegram and external services
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_preferred_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == 0 or family == socket.AF_UNSPEC:
        try:
            res = _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
            if res:
                return res
        except Exception:
            pass
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _ipv4_preferred_getaddrinfo

import logging
import threading

if sys.platform.startswith("linux"):
    try:
        threading.stack_size(512 * 1024)
    except Exception:
        pass
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Dict, Any, Optional, Callable

VN_TZ = timezone(timedelta(hours=7))

# Force UTF-8 encoding across Windows & Linux
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        # Prevent Windows Power Throttling / sleep
        try:
            kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass
    except Exception:
        pass

# Force Vietnam timezone (Asia/Ho_Chi_Minh - GMT+7) across Linux / VPS
if hasattr(time, "tzset"):
    try:
        os.environ["TZ"] = "Asia/Ho_Chi_Minh"
        time.tzset()
    except Exception:
        pass

# Ensure base directory is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from modules.free_proxy import FreeProxyManager
from modules.online_reward_runner import OnlineRewardRunner
from modules.runner import run_all_accounts
from modules.lottery_runner import run_all_lottery_accounts
from telegram_bot import TelegramBot
from special_watcher_manager import SpecialWatcherManager
from scheduler import PVTKScheduler
from core.tele_notifier import TeleNotifier, install_global_error_handlers

# Setup rotating file logger (max 10MB per file, keep 3 backups = max 40MB total)
import re
import gc
from logging.handlers import RotatingFileHandler

LOG_FILE = os.path.join(BASE_DIR, "bot_247.log")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[mK]")
_LIBC = None
_LIBC_LOAD_FAILED = False


def _trim_process_memory():
    """Collect Python cyclic garbage and ask glibc to release freed arenas back to OS on Linux."""
    global _LIBC, _LIBC_LOAD_FAILED
    try:
        gc.collect()
    except Exception:
        pass
    if not sys.platform.startswith("linux") or _LIBC_LOAD_FAILED:
        return
    try:
        if _LIBC is None:
            import ctypes
            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim.argtypes = [ctypes.c_size_t]
            libc.malloc_trim.restype = ctypes.c_int
            _LIBC = libc
        _LIBC.malloc_trim(0)
    except Exception:
        _LIBC_LOAD_FAILED = True


def _get_process_memory_stats() -> Optional[Dict[str, Any]]:
    """Read current RSS, Swap, Thread count, and FD count from /proc/self on Linux."""
    if not sys.platform.startswith("linux"):
        return None
    stats: Dict[str, Any] = {"rss_mb": 0.0, "swap_mb": 0.0, "threads": 0, "fds": 0}
    try:
        with open("/proc/self/status", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    stats["rss_mb"] = int(line.split()[1]) / 1024.0
                elif line.startswith("VmSwap:"):
                    stats["swap_mb"] = int(line.split()[1]) / 1024.0
                elif line.startswith("Threads:"):
                    stats["threads"] = int(line.split()[1])
        stats["fds"] = len(os.listdir("/proc/self/fd"))
        return stats
    except Exception:
        return None


class _RotatingFileFormatter(logging.Formatter):
    def format(self, record):
        if getattr(record, "_raw", False):
            return _ANSI_ESCAPE_RE.sub("", record.getMessage())
        return _ANSI_ESCAPE_RE.sub("", super().format(record))


class _TeeStream:
    """Forwards stdout/stderr to original stream and writes complete lines to RotatingFileHandler."""

    def __init__(self, orig_stream, file_handler):
        self._orig = orig_stream
        self._handler = file_handler
        self._buf_lock = threading.Lock()
        self._local = threading.local()
        self._buf = ""

    @property
    def encoding(self):
        return getattr(self._orig, "encoding", "utf-8")

    def isatty(self):
        try:
            return self._orig.isatty()
        except Exception:
            return False

    def fileno(self):
        return self._orig.fileno()

    def reconfigure(self, *args, **kwargs):
        if hasattr(self._orig, "reconfigure"):
            return self._orig.reconfigure(*args, **kwargs)

    def _emit_line(self, line: str):
        if not line or "[THEO DÕI]" in line:
            return
        if getattr(self._local, "writing", False):
            return
        stream = getattr(self._handler, "stream", None)
        if stream is None or getattr(stream, "closed", False):
            return
        self._local.writing = True
        try:
            rec = logging.LogRecord(
                name="stdout", level=logging.INFO, pathname="", lineno=0,
                msg=line, args=(), exc_info=None
            )
            rec._raw = True
            self._handler.handle(rec)
        except Exception:
            pass
        finally:
            self._local.writing = False

    def write(self, data):
        if not data:
            return 0
        try:
            res = self._orig.write(data)
        except Exception:
            res = len(data)
        norm = data.replace("\r\n", "\n")
        with self._buf_lock:
            self._buf += norm
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if "\r" in line:
                    line = line.rsplit("\r", 1)[-1]
                self._emit_line(line)
            if "\r" in self._buf:
                self._buf = self._buf.rsplit("\r", 1)[-1]
            elif len(self._buf) > 16384:
                self._emit_line(self._buf)
                self._buf = ""
        return res

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass
        try:
            stream = getattr(self._handler, "stream", None)
            if stream is not None and not getattr(stream, "closed", False):
                self._handler.flush()
        except Exception:
            pass


_orig_stdout = sys.stdout
_orig_stderr = sys.stderr

_file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_RotatingFileFormatter("%(asctime)s [%(levelname)s] %(message)s"))

_stream_handler = logging.StreamHandler(_orig_stdout)
_stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

sys.stdout = _TeeStream(_orig_stdout, _file_handler)
sys.stderr = _TeeStream(_orig_stderr, _file_handler)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_file_handler, _stream_handler]
)
logger = logging.getLogger("PVTK_247")

# Khởi tạo bộ bắt lỗi toàn cục sớm ngay khi load file
install_global_error_handlers(target_logger=logger)


class TelegramLogHandler(logging.Handler):
    """Tự động chuyển tiếp toàn bộ lỗi ERROR và CRITICAL về Telegram Bot qua TeleNotifier."""
    def __init__(self, master=None):
        super().__init__(level=logging.ERROR)
        self.master = master

    def emit(self, record):
        try:
            if getattr(record, "_tele_notified", False):
                return
            record._tele_notified = True
            if not getattr(self.master, "is_running", True):
                return
            raw_msg = record.getMessage()
            if "interpreter shutdown" in raw_msg.lower() or "cannot schedule new futures" in raw_msg.lower():
                return
            msg = self.format(record)
            source = f"{record.name} (L{record.lineno})"
            TeleNotifier.get_instance().send_error_alert(
                title=f"Log {record.levelname}",
                detail=msg,
                level=record.levelname,
                source=source,
                cooldown=45.0
            )
        except Exception:
            pass



def load_accounts_file(filepath: str) -> List[Tuple]:
    accounts = []
    if not os.path.isabs(filepath):
        filepath = os.path.join(BASE_DIR, filepath)
    if not os.path.exists(filepath):
        return accounts

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                accounts.append((parts[0], parts[1], parts[2] if parts[2] else None))
            elif len(parts) >= 2:
                accounts.append((parts[0], parts[1], None))
    return accounts


class PVTKMaster247:
    def __init__(self, config_path: str = "config_247.json"):
        self.config_path = os.path.join(BASE_DIR, config_path)
        self.start_time = time.time()
        self.is_running = False
        self.is_paused = False
        self.is_restarting = False

        # Configuration
        self.config: Dict[str, Any] = self._load_config()
        self._sync_telegram_storage()

        # Accounts
        self.accounts_file = self.config.get("accounts_file", "accounts_tuyet_vo_than.txt")
        self.raw_accounts: List[Tuple] = load_accounts_file(self.accounts_file)
        self.accounts: List[Tuple] = []

        # Roles
        self.special_accounts_file = self.config.get("special_accounts_file", "accountsNV.txt")
        self.special_accounts_count = self.config.get("special_accounts_count", 3)
        self.special_accounts: List[Tuple] = []
        self.online_reward_accounts: List[Tuple] = []

        # Components
        self.telegram_bot: Optional[TelegramBot] = None
        self.special_watcher: Optional[SpecialWatcherManager] = None
        self.scheduler: Optional[PVTKScheduler] = None
        
        # Online Reward Runner state
        self.online_reward_runner: Optional[OnlineRewardRunner] = None
        self.online_reward_thread: Optional[threading.Thread] = None
        self.online_reward_stop_event = threading.Event()
        self.is_event_running = False
        self.event_lock = threading.Lock()
        self._server_unreachable = False
        self._stop_lock = threading.Lock()
        self._stopped = False

    def _load_config(self) -> Dict[str, Any]:
        default_cfg = {
            "telegram": {"bot_token": "", "chat_id": "", "enabled": True, "admin_ids": []},
            "server": {
                "name": "Tuyệt Vô Thần (Cụm 3)",
                "game_host": "123.30.142.210",
                "game_port": 10000,
                "master_host": "123.30.142.219",
                "master_port": 30000
            },
            "accounts_file": "accounts_tuyet_vo_than.txt",
            "special_accounts_file": "accountsNV.txt",
            "use_proxy": True,
            "proxy_file": "proxies_good.txt",
            "special_accounts_count": 3,
            "schedule": {
                "daily_relogin": "00:01",
                "quiz_start": "22:01",
                "quiz_end": "22:35",
                "lottery_start": "08:01",
                "quiz_min_level": 0,
                "lottery_min_level": 30,
                "login_lottery_time": "07:01",
                "login_lottery_min_level": 35,
                "status_report_interval_minutes": 180
            },
            "threading": {"max_concurrent": 0}
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    default_cfg.update(data)
            except Exception as e:
                logger.error(f"Lỗi đọc config_247.json: {e}")
        return default_cfg

    def _sync_telegram_storage(self):
        """Keep storage/telegram_notify.json in sync with config_247.json."""
        tg_storage_path = os.path.join(BASE_DIR, "storage", "telegram_notify.json")
        tg_cfg = self.config.get("telegram", {})
        token = tg_cfg.get("bot_token", "").strip()
        chat_id = str(tg_cfg.get("chat_id", "")).strip()

        # If empty in config_247, check if storage has it
        if (not token or not chat_id) and os.path.exists(tg_storage_path):
            try:
                with open(tg_storage_path, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                if s_data.get("bot_token") and not token:
                    self.config["telegram"]["bot_token"] = s_data["bot_token"]
                    token = s_data["bot_token"]
                if s_data.get("chat_id") and not chat_id:
                    self.config["telegram"]["chat_id"] = str(s_data["chat_id"])
                    chat_id = str(s_data["chat_id"])
            except Exception:
                pass

        # Write back to storage
        if token and chat_id:
            try:
                from core.storage_utils import atomic_write_json
                atomic_write_json(tg_storage_path, {"enabled": True, "bot_token": token, "chat_id": chat_id}, indent=2)
            except Exception:
                pass

    def check_first_run_credentials(self):
        """If Telegram token is missing, allow user to input it once from console."""
        tg = self.config.get("telegram", {})
        token = tg.get("bot_token", "").strip()
        chat_id = str(tg.get("chat_id", "")).strip()

        if not token:
            print("\n" + "=" * 75)
            print("  ⚙️  THIẾT LẬP TELEGRAM BOT LẦN ĐẦU (CHO VPS VÀ REMOTE CONTROL)")
            print("=" * 75)
            print("Để nhận thông báo và điều khiển qua Telegram:")
            print("1. Tạo bot qua @BotFather trên Telegram để lấy Bot Token.")
            print("2. Lấy Chat ID của bạn qua bot @userinfobot trên Telegram.")
            print("-" * 75)
            try:
                inp_token = input("-> Nhập Telegram Bot Token (hoặc nhấn Enter để bỏ qua): ").strip()
                if inp_token:
                    self.config["telegram"]["bot_token"] = inp_token
                    inp_chat = input("-> Nhập Telegram Chat ID của bạn: ").strip()
                    if inp_chat:
                        self.config["telegram"]["chat_id"] = inp_chat
                    self._save_config()
                    self._sync_telegram_storage()
            except (KeyboardInterrupt, EOFError):
                pass
            print("=" * 75 + "\n")

    def _save_config(self):
        try:
            from core.storage_utils import atomic_write_json
            atomic_write_json(self.config_path, self.config, indent=2)
        except Exception as e:
            logger.error(f"Lỗi lưu config: {e}")

    def prepare_accounts_and_proxies(self):
        special_file = self.config.get("special_accounts_file", "accountsNV.txt")
        raw_special = []
        if os.path.exists(special_file):
            raw_special = load_accounts_file(special_file)
            logger.info(f"Đã nạp {len(raw_special)} tài khoản theo dõi đặc biệt từ '{special_file}'.")

        self.raw_accounts = load_accounts_file(self.accounts_file)
        logger.info(f"Đã nạp {len(self.raw_accounts)} tài khoản từ '{self.accounts_file}'.")

        # Nếu không có file special hoặc file rỗng, fallback lấy n acc đầu từ accounts_file
        if not raw_special:
            raw_special = self.raw_accounts[:self.special_accounts_count]
            raw_online = self.raw_accounts[self.special_accounts_count:]
        else:
            # Loại bỏ các acc đặc biệt khỏi raw_online nếu vô tình còn sót trong accounts_file
            special_user_set = {acc[0].strip().lower() for acc in raw_special}
            raw_online = [acc for acc in self.raw_accounts if acc[0].strip().lower() not in special_user_set]

        # Acc Đặc Biệt: Luôn luôn kết nối trực tiếp IP Chính (proxy = None) để đoạt Lì Xì siêu tốc
        self.special_accounts = [(acc[0], acc[1], None) for acc in raw_special]

        # Các acc còn lại (Treo online nhận quà): Gán Proxy nếu use_proxy=True
        if self.config.get("use_proxy", True) and raw_online:
            logger.info("Đang cấu hình Proxy cho các tài khoản treo Online (dùng file proxies_good.txt)...")
            FreeProxyManager.set_accounts_file(self.accounts_file)
            self.online_reward_accounts = FreeProxyManager.assign_proxies_to_accounts(raw_online)
        else:
            self.online_reward_accounts = raw_online

        self.accounts = self.special_accounts + self.online_reward_accounts
        logger.info(f"⚡ [IP CHÍNH] {len(self.special_accounts)} Acc Đặc Biệt ({special_file}): Theo dõi SGD + Sàn NV + Đoạt Lì Xì (Cố định Tân Nguyệt Trấn, không dịch chuyển).")
        logger.info(f"🌐 [PROXY] {len(self.online_reward_accounts)} Acc Treo Online Nhận Quà: Vận hành qua Proxy.")

    def start(self):
        self._stopped = False
        self.is_running = True
        logger.info("=" * 80)
        logger.info("       HỆ THỐNG PVTK 24/7 BOT AUTOMATION CHÍNH THỨC KHỞI ĐỘNG")
        logger.info("=" * 80)

        self.check_first_run_credentials()

        # 1. Khởi động Telegram Bot ngay lập tức để nhận lệnh điều khiển không bị trễ
        self.telegram_bot = TelegramBot(config_path=self.config_path, context=self)
        self.telegram_bot.start()

        # Đăng ký Telegram Bot với TeleNotifier và cài đặt handler toàn cục
        TeleNotifier.get_instance().set_bot(self.telegram_bot)
        install_global_error_handlers(bot_instance=self.telegram_bot, target_logger=logger)

        # Tự động gửi mọi lỗi ERROR/CRITICAL về Telegram Bot
        tg_log_handler = TelegramLogHandler(master=self)
        tg_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(tg_log_handler)

        # 2. Nạp tài khoản và chuẩn bị Proxy
        self.prepare_accounts_and_proxies()

        # 2. Khởi động 5 Acc Đặc Biệt
        self.special_watcher = SpecialWatcherManager(self.special_accounts, telegram_bot=self.telegram_bot)
        self.special_watcher.start()

        # 3. Khởi động Luồng Treo Nhận Quà Online cho các acc còn lại
        self.start_online_rewards()

        # 4. Khởi động Scheduler (00:01 Relogin, 22:01 Vấn Đáp, 08:00 Rút Thưởng)
        self.scheduler = PVTKScheduler(context=self, telegram_bot=self.telegram_bot)
        self.scheduler.start()

        logger.info("Toàn bộ 4 phân hệ chính đã khởi động thành công và đang hoạt động 24/7.")

    def start_online_rewards(self):
        if not self.online_reward_accounts:
            return

        if self.online_reward_thread and self.online_reward_thread.is_alive():
            logger.info("Dừng luồng treo online cũ trước khi khởi động mới...")
            self.stop_online_rewards_temporarily(timeout=5.0)

        self.online_reward_stop_event.clear()
        total_online_accs = len(self.online_reward_accounts)

        # Sử dụng tối đa số luồng = số tài khoản theo yêu cầu
        max_workers = total_online_accs

        def _run_online_rewards_loop():
            from modules.daily_history import DailyHistoryManager
            while not self.online_reward_stop_event.is_set() and self.is_running:
                # 1. Lọc trước các tài khoản chưa hoàn thành đủ quà online của cả 3 NV hôm nay
                pending_accounts = [
                    acc for acc in self.online_reward_accounts
                    if not DailyHistoryManager.is_account_fully_done_today(acc[0])
                ]
                summary = DailyHistoryManager.get_today_online_reward_summary()
                fully_done = summary.get("total_accs_fully_done", summary.get("total_accs_done", 0))
                if not pending_accounts or (fully_done >= total_online_accs and total_online_accs > 0):
                    today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
                    logger.info(f"🏆 [TẤT CẢ TÀI KHOẢN ĐÃ XONG QUÀ HÔM NAY {today_str}] {fully_done}/{total_online_accs} tài khoản ({summary.get('total_chars_done')} NV) đã nhận đủ quà! Toàn bộ tài khoản giữ trạng thái OFFLINE chờ sự kiện / sang ngày mới...")
                    while not self.online_reward_stop_event.is_set() and self.is_running:
                        current_date = datetime.now(VN_TZ).strftime("%Y-%m-%d")
                        if current_date != today_str:
                            logger.info(f"🌅 [SANG NGÀY MỚI {current_date}] Kích hoạt lại luồng treo online nhận quà ngày mới!")
                            break
                        if self.online_reward_stop_event.wait(timeout=10.0):
                            break
                    if self.online_reward_stop_event.is_set() or not self.is_running:
                        break
                    continue

                logger.info(
                    f"Khởi động tiến trình Treo Online Nhận Quà ({len(pending_accounts)}/{total_online_accs} acc chưa xong hôm nay, {len(pending_accounts)} luồng)..."
                )
                try:
                    runner = OnlineRewardRunner(
                        accounts=pending_accounts,
                        auto_cycle_characters=True,
                        slot_choice=1,
                        stop_event=self.online_reward_stop_event,
                        max_workers=len(pending_accounts),
                        proxy_rotation=bool(self.config.get("use_proxy", True))
                    )
                    self.online_reward_runner = runner
                    runner.run_all()
                except Exception as e:
                    logger.error(f"[ONLINE REWARD ERR] {e}")

                # 2. Sau khi chạy xong 1 vòng: kiểm tra xem toàn bộ tài khoản đã hoàn tất chưa
                summary_after = DailyHistoryManager.get_today_online_reward_summary()
                fully_done_after = summary_after.get("total_accs_fully_done", summary_after.get("total_accs_done", 0))
                if fully_done_after >= total_online_accs and total_online_accs > 0:
                    today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
                    logger.info(f"🎉 [HOÀN TẤT TOÀN BỘ QUÀ ONLINE {today_str}] Đã hoàn thành {fully_done_after}/{total_online_accs} tài khoản. Toàn bộ tài khoản đã ngắt kết nối (OFFLINE). Chờ sự kiện hoặc 00:01 ngày mới.")
                    while not self.online_reward_stop_event.is_set() and self.is_running:
                        current_date = datetime.now(VN_TZ).strftime("%Y-%m-%d")
                        if current_date != today_str:
                            break
                        if self.online_reward_stop_event.wait(timeout=10.0):
                            break
                    if self.online_reward_stop_event.is_set() or not self.is_running:
                        break

                if self.online_reward_stop_event.wait(timeout=5.0):
                    break

        self.online_reward_thread = threading.Thread(target=_run_online_rewards_loop, daemon=True, name="OnlineRewardMainThread")
        self.online_reward_thread.start()

    def stop_online_rewards_temporarily(self, timeout: float = 4.0):
        """Tạm dừng luồng treo online và đóng toàn bộ socket để nhường kết nối hoặc relogin."""
        self.online_reward_stop_event.set()
        if self.online_reward_runner:
            self.online_reward_runner.stop_event.set()
            if hasattr(self.online_reward_runner, "close_all_connections"):
                self.online_reward_runner.close_all_connections()
        if self.online_reward_thread and self.online_reward_thread.is_alive():
            self.online_reward_thread.join(timeout=timeout)
        self.online_reward_thread = None
        self.online_reward_runner = None

    def restart_online_rewards(self):
        """Khởi động lại luồng treo online sạch sẽ cho toàn bộ tài khoản."""
        self.stop_online_rewards_temporarily(timeout=8.0)
        self.prepare_accounts_and_proxies()
        self.start_online_rewards()

    # ================= EVENT TRIGGERS =================

    def trigger_quiz_event(self, callback: Optional[Callable[[str], None]] = None):
        """Sự kiện Vấn Đáp (Tại chỗ - Mọi cấp độ). Dùng core.event_runner.run_event()."""
        from core.event_runner import run_event

        def batch_fn(accounts, max_w):
            run_all_accounts(accounts, max_workers=max_w)
            return None

        def report_fn(elapsed, total_accounts, _summary):
            from modules.daily_history import DailyHistoryManager
            quiz_sum = DailyHistoryManager.get_today_quiz_summary()
            return (
                "🎉 <b>BÁO CÁO KẾT QUẢ VẤN ĐÁP TOÀN HỆ THỐNG</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👥 <b>Tổng tài khoản:</b> {total_accounts} acc\n"
                f"🧙‍♂️ <b>Nhân vật hoàn thành:</b> {quiz_sum.get('total_done', 0)}/{quiz_sum.get('total_chars', 0)} NV\n"
                f"🎯 <b>Tổng câu trả lời đúng:</b> {quiz_sum.get('total_correct', 0):,} câu\n"
                f"⏱ <b>Thời gian thực thi:</b> {elapsed} giây\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🟢 <i>Đang khôi phục lại trạng thái treo Online nhận quà 24/7...</i>"
            )

        run_event(
            self, "SỰ KIỆN VẤN ĐÁP TOÀN BỘ TÀI KHOẢN", "🎓",
            "Tạm dừng treo quà → Chạy Vấn Đáp đa luồng → Khôi phục treo quà sau khi hoàn thành.",
            batch_fn, report_fn, callback
        )

    def trigger_lottery_event(self, callback: Optional[Callable[[str], None]] = None):
        """Sự kiện Rút Thưởng May Mắn (Lv30+). Dùng core.event_runner.run_event()."""
        from core.event_runner import run_event

        def batch_fn(accounts, max_w):
            run_all_lottery_accounts(accounts, max_workers=max_w)
            return None

        def report_fn(elapsed, _total, _summary):
            from modules.daily_history import DailyHistoryManager
            return DailyHistoryManager.format_lottery_summary_message(
                title="🎉 <b>BÁO CÁO KẾT QUẢ RÚT THƯỞNG MAY MẮN TOÀN HỆ THỐNG</b>",
                elapsed=elapsed
            )

        run_event(
            self, "SỰ KIỆN RÚT THƯỞNG MAY MẮN (LV30+)", "🎲",
            "Tạm dừng treo quà → Chạy Rút Thưởng đa luồng → Khôi phục treo quà sau khi hoàn thành.",
            batch_fn, report_fn, callback
        )

    def trigger_login_lottery_event(self, callback: Optional[Callable[[str], None]] = None):
        """Sự kiện Rút Thưởng Login (Lv >= 35). Dùng core.event_runner.run_event()."""
        from core.event_runner import run_event

        min_lv = self.config.get("schedule", {}).get("login_lottery_min_level", 35)

        def batch_fn(accounts, max_w):
            from modules.login_lottery_runner import run_all_login_lottery_accounts
            run_all_login_lottery_accounts(accounts, min_level=min_lv, max_workers=max_w)
            return None

        def report_fn(elapsed, _total, _summary):
            from modules.daily_history import DailyHistoryManager
            return DailyHistoryManager.format_login_lottery_summary_message(
                title="🎁 <b>BÁO CÁO KẾT QUẢ RÚT THƯỞNG LOGIN TOÀN HỆ THỐNG</b>",
                elapsed=elapsed
            )

        run_event(
            self, f"SỰ KIỆN RÚT THƯỞNG LOGIN (LV >= {min_lv})", "🎁",
            "Tạm dừng treo quà → Rút thưởng Login vòng xoay đa luồng → Khôi phục treo quà sau khi hoàn thành.",
            batch_fn, report_fn, callback
        )

    def trigger_exp_nv_event(self, callback: Optional[Callable[[str], None]] = None):
        """Sự kiện Sử Dụng Vé EXP Nhân Vật (Khóa). Dùng core.event_runner.run_event()."""
        from core.event_runner import run_event

        telegram_bot = self.telegram_bot  # capture reference cho closure

        def batch_fn(accounts, max_w):
            from modules.bag_cleaner import run_all_exp_nv
            from modules.free_proxy import FreeProxyManager
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
            return run_all_exp_nv(proxy_accounts, max_workers=max_w)

        def report_fn(elapsed, _total, summary):
            if telegram_bot and hasattr(telegram_bot, "_format_exp_nv_report"):
                return telegram_bot._format_exp_nv_report(summary or {})
            return f"✅ <b>Hoàn tất dùng vé EXP Nhân Vật (Khóa)!</b> Đã dùng: {(summary or {}).get('total_exp_used', 0)} vé."

        run_event(
            self, "SỬ DỤNG VÉ EXP NHÂN VẬT (KHÓA)", "⚡",
            "Tạm dừng treo quà → Quét & dùng vé EXP khóa đa luồng → Khôi phục treo quà sau khi xong.",
            batch_fn, report_fn, callback
        )

    def daily_relogin(self):
        """00:01: Reset and re-login all accounts for fresh daily online rewards."""
        logger.info("⏰ [00:01] Thực hiện Re-login toàn bộ tài khoản ngày mới...")
        try:
            OnlineRewardRunner._session_cache.clear()
            with open(os.path.join(BASE_DIR, "sessionIDs.txt"), "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass
        self.stop_online_rewards_temporarily(timeout=8.0)
        if self.special_watcher:
            self.special_watcher.stop()

        time.sleep(2.0)
        self.prepare_accounts_and_proxies()

        # Restart Watchers
        if self.special_watcher:
            self.special_watcher.accounts = self.special_accounts
            self.special_watcher.start()

        # Restart Online Rewards
        self.start_online_rewards()

        msg = f"🌅 <b>[00:01] ĐÃ RE-LOGIN THÀNH CÔNG TOÀN BỘ {len(self.accounts)} TÀI KHOẢN NGÀY MỚI!</b>\nBắt đầu chu trình nhận quà Online ngày mới."
        logger.info(msg)
        if self.telegram_bot:
            self.telegram_bot.send_message(msg)

    def health_check(self):
        """Periodic deep health check every 5 minutes - auto-recover all subsystems."""
        if not self.is_running:
            return

        # === 1. Kiểm tra kết nối tới Máy chủ Game ===
        srv_cfg = self.config.get("server", {})
        g_host = srv_cfg.get("game_host", "123.30.142.210")
        g_port = srv_cfg.get("game_port", 10000)
        hc_sock = None
        try:
            hc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            hc_sock.settimeout(5.0)
            hc_sock.connect((g_host, g_port))
            if self._server_unreachable:
                self._server_unreachable = False
                logger.info(f"Đã khôi phục kết nối thành công đến Game Server ({g_host}:{g_port}).")
                if self.telegram_bot:
                    self.telegram_bot.send_message(
                        f"✅ <b>[ĐÃ KHÔI PHỤC KẾT NỐI]</b>\n"
                        f"Máy chủ Game (<code>{g_host}:{g_port}</code>) đã mở lại bình thường.\n"
                        f"Bot đang tiếp tục chu trình tự động hóa 24/7."
                    )
        except Exception as e:
            if not self._server_unreachable:
                self._server_unreachable = True
                logger.error(f"Không thể kết nối đến Game Server ({g_host}:{g_port}): {e}")
                if self.telegram_bot:
                    self.telegram_bot.send_message(
                        f"⚠️ <b>[CẢNH BÁO MẤT KẾT NỐI GAME SERVER]</b>\n"
                        f"Không thể kết nối tới Server Game (<code>{g_host}:{g_port}</code>).\n"
                        f"👉 <b>Nguyên nhân:</b> Game đang BẢO TRÌ hoặc mạng VPS bị gián đoạn.\n"
                        f"Bot sẽ tự động giữ phiên và tự kết nối lại ngay khi server mở lại!"
                    )
        finally:
            if hc_sock is not None:
                try:
                    hc_sock.close()
                except Exception:
                    pass

        # === 2. Kiểm tra SpecialWatcher (SGD/Nhân Vật/Lì Xì) ===
        if self.special_watcher and not self.special_watcher.is_running:
            logger.warning("[HEALTH CHECK] SpecialWatcher dừng, đang khởi động lại...")
            try:
                self.special_watcher.start()
            except Exception as e:
                logger.error(f"[HEALTH CHECK] Lỗi khởi động lại SpecialWatcher: {e}")

        # === 3. Kiểm tra OnlineRewardThread ===
        if not self.is_event_running and self.online_reward_thread and not self.online_reward_thread.is_alive():
            logger.warning("[HEALTH CHECK] OnlineRewardThread dừng, đang khởi động lại...")
            try:
                self.start_online_rewards()
            except Exception as e:
                logger.error(f"[HEALTH CHECK] Lỗi khởi động lại OnlineRewardThread: {e}")

        # === 4. Kiểm tra Telegram Polling Thread - CỰC KỲ QUAN TRỌNG ===
        if self.telegram_bot and self.telegram_bot.running:
            if not self.telegram_bot.poll_thread or not self.telegram_bot.poll_thread.is_alive():
                logger.warning("[HEALTH CHECK] ⚠️ Telegram Poll Thread đã chết! Đang khởi động lại...")
                try:
                    self.telegram_bot.poll_thread = threading.Thread(
                        target=self.telegram_bot._poll_loop, daemon=True, name="TelegramPollThread"
                    )
                    self.telegram_bot.poll_thread.start()
                    logger.info("[HEALTH CHECK] ✅ Đã khởi động lại Telegram Poll Thread thành công.")
                except Exception as e:
                    logger.error(f"[HEALTH CHECK] Lỗi khởi động lại Telegram: {e}")

        # === 5. Kiểm tra Scheduler Thread ===
        if self.scheduler and self.scheduler.is_running:
            if not self.scheduler.thread or not self.scheduler.thread.is_alive():
                logger.warning("[HEALTH CHECK] ⚠️ Scheduler Thread đã chết! Đang khởi động lại...")
                try:
                    self.scheduler.is_running = False
                    self.scheduler.start()
                    logger.info("[HEALTH CHECK] ✅ Đã khởi động lại Scheduler Thread thành công.")
                except Exception as e:
                    logger.error(f"[HEALTH CHECK] Lỗi khởi động lại Scheduler: {e}")

        # === 6. Refresh proxy pool nếu quá ít ===
        try:
            FreeProxyManager.periodic_refresh()
        except Exception:
            pass

        # === 7. Thu gom bộ nhớ định kỳ & ghi nhận telemetry ===
        _trim_process_memory()
        mem = _get_process_memory_stats()
        if mem is not None:
            with FreeProxyManager._lock:
                proxy_cnt = len(FreeProxyManager._proxies)
            logger.info(
                f"[HEALTH CHECK] RAM: RSS={mem['rss_mb']:.1f}MB | Swap={mem['swap_mb']:.1f}MB | "
                f"Threads={mem['threads']} | FDs={mem['fds']} | ProxyPool={proxy_cnt}"
            )


    def teleport_all(self, map_option: str, callback: Optional[Callable[[str], None]] = None):
        """
        Dịch chuyển hoặc thoát lag toàn bộ tài khoản (Option 3):
        - NẾU ĐANG TREO ONLINE: Dịch chuyển TRỰC TIẾP trên phiên online đang mở (KHÔNG login lại, KHÔNG đứt kết nối).
        - NẾU CHƯA TREO ONLINE: Chạy batch login từng tài khoản như trước.
        - 'thanh_vuc' / '1' / 'Thánh Vực': Dịch chuyển sang Thánh Vực (Map ID: 11, Yêu cầu Lv >= 35)
        - 'tan_nguyet' / '2' / 'Tân Nguyệt Trấn': Dịch chuyển sang Tân Nguyệt Trấn (Map ID: 37, Yêu cầu Lv > 15)
        - 'thoat_lag' / '3' / 'Thoát lag': Cài đặt / H.Thống -> Thoát lag (Không yêu cầu level)
        """
        def _worker():
            opt = (map_option or "").strip().lower()

            if any(k in opt for k in ["thoát lag", "thoat_lag", "thoat lag", "escape", "3"]):
                target = "thoat_lag"
                map_name = "Thoát Lag (Cài đặt / H.Thống)"
                map_id = 0
                min_level = 0
            elif any(k in opt for k in ["tân nguyệt", "tan nguyet", "tan_nguyet", "2"]):
                target = "tan_nguyet"
                map_name = "Tân Nguyệt Trấn"
                map_id = 37
                min_level = 16  # Yêu cầu Lv > 15
            else:
                target = "thanh_vuc"
                map_name = "Thánh Vực"
                map_id = 11
                min_level = 35  # Yêu cầu Lv >= 35

            # 1. ƯU TIÊN: Đang treo online -> Dịch chuyển trực tiếp ngay trong game, KHÔNG CẦN LOGIN LẠI!
            if self.online_reward_runner and self.online_reward_thread and self.online_reward_thread.is_alive():
                logger.info(f"🚀 [DỊCH CHUYỂN TRỰC TIẾP] Đang gửi lệnh '{map_name}' tới toàn bộ acc đang treo online...")
                self.online_reward_runner.trigger_in_session_teleport(target, map_name, map_id, min_level)

                # Chờ động tối đa 8.0s để toàn bộ nhân vật online gửi xong gói tin
                online_target = self.online_reward_runner.get_realtime_online_count()
                wait_t = time.time()
                while time.time() - wait_t < 8.0:
                    with self.online_reward_runner.action_lock:
                        if len(self.online_reward_runner.teleport_results_summary) >= online_target:
                            break
                    time.sleep(0.5)

                res = self.online_reward_runner.get_teleport_summary()
                already_line = f"📍 <b>Đã ở sẵn từ trước:</b> {res.get('already', 0)}\n" if res.get("already", 0) > 0 else ""
                msg = (
                    f"🚀 <b>BÁO CÁO DỊCH CHUYỂN TRỰC TIẾP</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <b>Mục tiêu:</b> {map_name}\n"
                    f"👥 <b>Nhân vật đang treo:</b> {res.get('total', 0)}\n"
                    f"✅ <b>Thành công:</b> {res.get('ok', 0)}\n"
                    f"{already_line}"
                    f"⚠️ <b>Bỏ qua do chưa đủ cấp:</b> {res.get('skipped', 0)}\n"
                    f"❌ <b>Thất bại thực sự:</b> {res.get('failed', 0)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🟢 <i>Tất cả nhân vật đang tiếp tục đứng tại map và treo nhận quà bình thường!</i>"
                )
                logger.info(msg)
                if callback:
                    callback(msg)
                elif self.telegram_bot:
                    self.telegram_bot.send_message(msg)
                return

            # 2. FALLBACK: Nếu chưa bật luồng treo online, chạy batch login như trước
            try:
                self.stop_online_rewards_temporarily()
                from modules.teleport_all import run_all_teleport, run_mass_escape_lag
                special_users = {a[0].strip().lower() for a in self.special_accounts}
                teleport_accs = [acc for acc in self.online_reward_accounts if acc[0].strip().lower() not in special_users]
                max_w = len(teleport_accs) if teleport_accs else 10

                if target == "thoat_lag":
                    res = run_mass_escape_lag(teleport_accs, max_workers=max_w)
                    msg = (
                        "🔄 <b>BÁO CÁO THOÁT LAG TOÀN BỘ TÀI KHOẢN (CÀI ĐẶT / H.THỐNG)</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👥 <b>Tổng tài khoản:</b> {res.get('total_accounts', 0)} acc\n"
                        f"✅ <b>Thoát lag thành công:</b> {res.get('total_escaped', 0)} nhân vật\n"
                        f"📍 <b>Đã ở sẵn map hồi sinh:</b> {res.get('total_already', 0)} nhân vật\n"
                        f"⚠️ <b>Tài khoản lỗi:</b> {res.get('failed_accounts', 0)}\n"
                        f"⏱ <b>Thời gian thực hiện:</b> {res.get('elapsed', 0)} giây\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        "🔒 <i>Các Acc theo dõi đặc biệt (accountsNV.txt) được bảo vệ cố định tại Tân Nguyệt Trấn, không bị dịch chuyển!</i>\n"
                        "🟢 <i>Đã tiếp tục tiến trình treo online nhận quà!</i>"
                    )
                elif target == "tan_nguyet":
                    res = run_all_teleport(teleport_accs, target_map_name="Tân Nguyệt Trấn", target_map_id=37, max_workers=max_w)
                    msg = (
                        "🌙 <b>BÁO CÁO DỊCH CHUYỂN TÂN NGUYỆT TRẤN (YÊU CẦU LV > 15)</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👥 <b>Tổng tài khoản:</b> {res.get('total_accounts', 0)} acc\n"
                        f"✅ <b>Dịch chuyển thành công:</b> {res.get('total_teleported', 0)} nhân vật\n"
                        f"📍 <b>Đã ở sẵn từ trước:</b> {res.get('total_already', 0)} nhân vật\n"
                        f"⚠️ <b>Bỏ qua do chưa đủ cấp (Lv ≤ 15):</b> {res.get('total_skipped', 0)} nhân vật\n"
                        f"❌ <b>Tài khoản lỗi:</b> {res.get('failed_accounts', 0)}\n"
                        f"⏱ <b>Thời gian thực hiện:</b> {res.get('elapsed', 0)} giây\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        "🔒 <i>Các Acc theo dõi đặc biệt (accountsNV.txt) được bảo vệ cố định tại Tân Nguyệt Trấn, không bị dịch chuyển!</i>\n"
                        "🟢 <i>Đã tiếp tục tiến trình treo online nhận quà!</i>"
                    )
                else:
                    res = run_all_teleport(teleport_accs, target_map_name="Thánh Vực", target_map_id=11, max_workers=max_w)
                    msg = (
                        "🏛 <b>BÁO CÁO DỊCH CHUYỂN THÁNH VỰC (YÊU CẦU LV ≥ 35)</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👥 <b>Tổng tài khoản:</b> {res.get('total_accounts', 0)} acc\n"
                        f"✅ <b>Dịch chuyển thành công:</b> {res.get('total_teleported', 0)} nhân vật\n"
                        f"📍 <b>Đã ở sẵn từ trước:</b> {res.get('total_already', 0)} nhân vật\n"
                        f"⚠️ <b>Bỏ qua do chưa đủ cấp (Lv < 35):</b> {res.get('total_skipped', 0)} nhân vật\n"
                        f"❌ <b>Tài khoản lỗi:</b> {res.get('failed_accounts', 0)}\n"
                        f"⏱ <b>Thời gian thực hiện:</b> {res.get('elapsed', 0)} giây\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        "🔒 <i>Các Acc theo dõi đặc biệt (accountsNV.txt) được bảo vệ cố định tại Tân Nguyệt Trấn, không bị dịch chuyển!</i>\n"
                        "🟢 <i>Đã tiếp tục tiến trình treo online nhận quà!</i>"
                    )

                logger.info(msg)
                if callback:
                    callback(msg)
                elif self.telegram_bot:
                    self.telegram_bot.send_message(msg)
            except Exception as e:
                err_msg = f"❌ <b>Lỗi dịch chuyển:</b> {html.escape(str(e))}"
                logger.error(err_msg)
                if callback:
                    callback(err_msg)
                elif self.telegram_bot:
                    self.telegram_bot.send_message(err_msg)
            finally:
                self.start_online_rewards()

        threading.Thread(target=_worker, daemon=True).start()

    def enter_city_all(self, target_str: str, callback: Optional[Callable[[str], None]] = None):
        """
        Dịch chuyển toàn bộ tài khoản vào Thành của ID chỉ định:
        - NẾU ĐANG TREO ONLINE: Dịch chuyển TRỰC TIẾP trên phiên online đang mở (KHÔNG login lại, KHÔNG đứt kết nối).
        - NẾU CHƯA TREO ONLINE: Chạy login vào thành 1 acc test.
        """
        def _worker():
            from modules.city_manager import CityManager
            target_id = CityManager.parse_target_id(target_str)
            if not target_id:
                err = f"❌ ID mục tiêu '{target_str}' không hợp lệ (hỗ trợ dạng số '1906278888' hoặc Area ID 'am6278888')."
                if callback:
                    callback(err)
                elif self.telegram_bot:
                    self.telegram_bot.send_message(err)
                return

            # 1. ƯU TIÊN: Đang treo online -> Dịch chuyển trực tiếp ngay trong game
            if self.online_reward_runner and self.online_reward_thread and self.online_reward_thread.is_alive():
                logger.info(f"🏰 [VÀO THÀNH TRỰC TIẾP] Đang gửi lệnh vào Thành ID {target_id} tới toàn bộ acc đang treo online...")
                self.online_reward_runner.trigger_in_session_teleport("city", f"Thành ID {target_id}", target_id, min_level=0, target_id=target_id)

                # Chờ động tối đa 8.0s để toàn bộ nhân vật online gửi xong gói tin
                online_target = self.online_reward_runner.get_realtime_online_count()
                wait_t = time.time()
                while time.time() - wait_t < 8.0:
                    with self.online_reward_runner.action_lock:
                        if len(self.online_reward_runner.teleport_results_summary) >= online_target:
                            break
                    time.sleep(0.5)

                res = self.online_reward_runner.get_teleport_summary()
                msg = (
                    f"🏰 <b>BÁO CÁO VÀO THÀNH TRỰC TIẾP</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <b>Mục tiêu:</b> Thành của ID <code>{target_str}</code> ({target_id})\n"
                    f"👥 <b>Nhân vật đang treo:</b> {res.get('total', 0)}\n"
                    f"✅ <b>Thành công:</b> {res.get('ok', 0)}\n"
                    f"❌ <b>Thất bại:</b> {res.get('failed', 0)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🟢 <i>Tất cả nhân vật đang tiếp tục đứng tại Thành và treo nhận quà bình thường!</i>"
                )
                logger.info(msg)
                if callback:
                    callback(msg)
                elif self.telegram_bot:
                    self.telegram_bot.send_message(msg)
                return

            # 2. FALLBACK: Chưa treo online -> Chạy vào thành 1 acc test
            accounts = self.online_reward_accounts or self.accounts
            ok, enter_res, city_info = CityManager.run_enter_city_single(target_str, accounts)
            if ok:
                s_target_str = html.escape(str(target_str))
                s_enter_res = html.escape(str(enter_res))
                msg = (
                    f"🏰 <b>VÀO THÀNH THÀNH CÔNG</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <b>Mục tiêu:</b> ID <code>{s_target_str}</code> ({target_id})\n"
                    f"✅ {s_enter_res}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                )
            else:
                s_enter_res = html.escape(str(enter_res))
                msg = f"❌ <b>[VÀO THÀNH THẤT BẠI]</b>\n{s_enter_res}"
            if callback:
                callback(msg)
            elif self.telegram_bot:
                self.telegram_bot.send_message(msg)

        threading.Thread(target=_worker, daemon=True, name="Thread-EnterCityAll").start()

    def delete_items(self, keywords: List[str], callback: Optional[Callable[[str], None]] = None):
        with self.event_lock:
            if self.is_event_running:
                msg = "⚠️ Hiện đang có một sự kiện/tiến trình khác đang chạy. Vui lòng đợi hoàn tất."
                if callback: callback(msg)
                return
            self.is_event_running = True

        def _worker():
            try:
                self.stop_online_rewards_temporarily()
                from modules.item_deleter import run_all_item_deleter
                all_accs = self.get_active_accounts()
                res = run_all_item_deleter(all_accs, keywords=keywords, max_workers=len(all_accs))
                total_slots = res.get("total_slots", 0) if isinstance(res, dict) else 0
                total_qty = res.get("total_qty", 0) if isinstance(res, dict) else 0
                total_silver = res.get("total_silver", 0) if isinstance(res, dict) else 0
                items_details = res.get("sold_items_details", {})
                items_summary = res.get("sold_items_summary", res.get("deleted_items_summary", {})) if isinstance(res, dict) else {}
                details_per_acc = res.get("details_per_acc", []) if isinstance(res, dict) else []
                kw_str = ", ".join(keywords)

                lines = [
                    "💰 <b>BÁO CÁO CHI TIẾT BÁN VẬT PHẨM VÀO SHOP</b>",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    f"🔍 <b>Từ khóa đã quét:</b> <code>{kw_str}</code>",
                    f"📦 <b>Tổng số ô đã bán:</b> <b>{total_slots:,}</b> ô",
                    f"🔢 <b>Tổng số lượng món đã bán:</b> <b>{total_qty:,}</b> món",
                ]
                if total_silver > 0:
                    lines.append(f"💵 <b>Tổng Bạc thu về:</b> <b>+{total_silver:,} Bạc</b>")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━")

                if items_details or items_summary:
                    lines.append("📋 <b>DANH SÁCH CHI TIẾT VẬT PHẨM ĐÃ BÁN:</b>")
                    if items_details:
                        sorted_items = sorted(items_details.items(), key=lambda x: x[1].get("qty", 0), reverse=True)
                        for item_name, d_info in sorted_items:
                            iqty = d_info.get("qty", 0)
                            islots = d_info.get("slots", 0)
                            isilver = d_info.get("silver", 0)
                            extra_parts = []
                            if islots > 0:
                                extra_parts.append(f"{islots:,} ô")
                            if isilver > 0:
                                extra_parts.append(f"+{isilver:,} Bạc")
                            extra_str = f" <i>({', '.join(extra_parts)})</i>" if extra_parts else ""
                            lines.append(f"  • <b>{item_name}</b>: <code>{iqty:,}</code> món{extra_str}")
                    else:
                        sorted_items = sorted(items_summary.items(), key=lambda x: x[1], reverse=True)
                        for item_name, count in sorted_items:
                            lines.append(f"  • <b>{html.escape(str(item_name))}</b>: <code>{count:,}</code> món")
                    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

                if details_per_acc:
                    unique_accs = len(set(d.get("username", "") for d in details_per_acc if d.get("username")))
                    lines.append(f"👥 <b>TỔNG KẾT TÀI KHOẢN & NHÂN VẬT THỰC HIỆN:</b>")
                    lines.append(f"  • Đã bán thành công trên: <b>{len(details_per_acc):,}</b> nhân vật (thuộc <b>{unique_accs:,}</b> tài khoản)")
                    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                else:
                    lines.append("ℹ️ <i>Không có tài khoản nào có vật phẩm khớp từ khóa trên.</i>")
                    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

                lines.append("🟢 <i>Đã tự động tiếp tục treo game online nhận quà!</i>")

                full_text = "\n".join(lines)
                if len(full_text) <= 4000:
                    if callback: callback(full_text)
                else:
                    chunks = []
                    current_chunk = []
                    current_len = 0
                    for line in lines:
                        if current_len + len(line) + 1 > 3800:
                            chunks.append("\n".join(current_chunk))
                            current_chunk = [line]
                            current_len = len(line)
                        else:
                            current_chunk.append(line)
                            current_len += len(line) + 1
                    if current_chunk:
                        chunks.append("\n".join(current_chunk))
                    for part in chunks:
                        if callback: callback(part)
            except Exception as e:
                err_msg = f"❌ <b>Lỗi khi bán vật phẩm:</b> {html.escape(str(e))}"
                logger.error(err_msg)
                if callback: callback(err_msg)
            finally:
                try:
                    self.start_online_rewards()
                except Exception:
                    pass
                with self.event_lock:
                    self.is_event_running = False

        threading.Thread(target=_worker, daemon=True, name="Thread-DeleteItems").start()

    sell_items = delete_items

    def get_special_watchers_gold(self) -> Dict[str, int]:
        """Lấy số dư KNB hiện tại của các tài khoản đoạt lì xì."""
        if self.special_watcher:
            return self.special_watcher.get_gold_balances()
        return {}

    def send_knb_from_special_watchers(self, target_id: str, amount_per_account: Optional[int] = None, callback: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None):
        """Chuyển KNB hiện có của các tài khoản đoạt lì xì sang target_id qua thư in-game."""
        def _worker():
            try:
                if not self.special_watcher:
                    msg = "⚠️ Luồng tài khoản đoạt lì xì chưa được khởi động."
                    if callback: callback(msg, None)
                    return

                res = self.special_watcher.send_all_knb(target_id, amount_per_account=amount_per_account)
                total = res.get("total_sent", 0)
                details = res.get("details", {})
                is_test = (amount_per_account is not None and amount_per_account > 0)

                header = "🧪 <b>BÁO CÁO GỬI TEST (1 KNB / MỖI ACC)</b>" if is_test else "💰 <b>BÁO CÁO CHUYỂN KNB TỪ CÁC ACC ĐOẠT LÌ XÌ</b>"
                lines = [
                    header,
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    f"🎯 <b>Người nhận:</b> <code>{target_id}</code>"
                ]
                for user, d in details.items():
                    st_icon = "✅" if d.get("success") else "❌"
                    amt = d.get("amount", 0)
                    detail_msg = d.get("message", "")
                    lines.append(f"{st_icon} <b>{html.escape(str(user))}:</b> {amt:,} KNB ({html.escape(str(detail_msg))})")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                lines.append(f"🏆 <b>Tổng KNB đã chuyển:</b> {total:,} KNB")

                report = "\n".join(lines)
                logger.info(report)
                if callback:
                    callback(report, res)
                elif self.telegram_bot:
                    self.telegram_bot.send_message(report)
            except Exception as e:
                err_msg = f"❌ <b>Lỗi chuyển KNB:</b> {html.escape(str(e))}"
                logger.error(err_msg)
                if callback:
                    callback(err_msg, None)
                elif self.telegram_bot:
                    self.telegram_bot.send_message(err_msg)

        threading.Thread(target=_worker, daemon=True, name="SendKnbThread").start()

    def query_item_price(self, keyword: str) -> List[Dict[str, Any]]:
        """Tra cứu thống kê giá lịch sử vật phẩm qua special_watcher."""
        if self.special_watcher and hasattr(self.special_watcher, "query_item_price"):
            return self.special_watcher.query_item_price(keyword)
        from modules.item_price_tracker import ItemPriceTracker
        tracker = ItemPriceTracker(db_path="storage/item_price_history.json")
        return tracker.query_item_price(keyword)

    def get_active_accounts(self) -> List[Tuple]:
        return self.accounts if self.accounts else self.raw_accounts

    def get_system_status(self) -> Dict[str, Any]:
        uptime_sec = int(time.time() - self.start_time)
        hours = uptime_sec // 3600
        mins = (uptime_sec % 3600) // 60
        uptime_str = f"{hours} giờ {mins} phút" if hours > 0 else f"{mins} phút"

        watcher_status = self.special_watcher.get_status() if self.special_watcher else {}
        total_won = watcher_status.get("total_won_gold", 0)
        watcher_online = watcher_status.get("online_count", 0)
        watcher_total = watcher_status.get("total_workers", len(self.special_accounts))
        watcher_active = watcher_status.get("active_threads", (watcher_total if (self.special_watcher and self.special_watcher.is_running) else 0))

        online_target = len(self.online_reward_accounts)
        online_realtime = 0
        online_active_workers = 0
        if self.online_reward_runner and self.online_reward_thread and self.online_reward_thread.is_alive():
            if hasattr(self.online_reward_runner, "get_realtime_online_count"):
                online_realtime = self.online_reward_runner.get_realtime_online_count()
            if hasattr(self.online_reward_runner, "get_active_workers_count"):
                online_active_workers = self.online_reward_runner.get_active_workers_count()

        total_accs = len(self.accounts)
        active_threads = online_active_workers + watcher_active
        total_real_online = online_realtime + watcher_online

        srv_cfg = self.config.get("server", {})
        srv_name = srv_cfg.get("name", "Tuyệt Vô Thần (Cụm 3)")

        return {
            "server": srv_name,
            "uptime_str": uptime_str,
            "total_accounts": total_accs,
            "active_threads": active_threads,
            "total_real_online": total_real_online,
            "special_watchers_count": watcher_online,
            "special_watchers_total": watcher_total,
            "online_reward_realtime": online_realtime,
            "online_reward_target": online_target,
            "luck_money_won_gold": total_won,
            "use_proxy": self.config.get("use_proxy", True),
            "proxy_count": len(FreeProxyManager._proxies)
        }

    def get_formatted_status_message(self, title: str = "BÁO CÁO TRẠNG THÁI HỆ THỐNG 24/7") -> str:
        status_data = self.get_system_status()
        srv = status_data.get("server", "N/A")
        total_accs = status_data.get("total_accounts", 0)
        active_threads = status_data.get("active_threads", 0)
        total_real_online = status_data.get("total_real_online", 0)

        special_online = status_data.get("special_watchers_count", 0)
        special_total = status_data.get("special_watchers_total", len(self.special_accounts))

        online_realtime = status_data.get("online_reward_realtime", 0)
        online_target = status_data.get("online_reward_target", 0)

        won_gold = status_data.get("luck_money_won_gold", 0)
        uptime = status_data.get("uptime_str", "0 phút")
        use_proxy = status_data.get("use_proxy", True)
        proxy_count = status_data.get("proxy_count", 0)
        curr_time = datetime.now(VN_TZ).strftime("%H:%M:%S %d/%m/%Y")

        claimed_rewards_info = ""
        if self.online_reward_runner and hasattr(self.online_reward_runner, "char_rewards_claimed_global"):
            total_mocs = sum(sum(m.values()) for m in self.online_reward_runner.char_rewards_claimed_global.values())
            claimed_rewards_info = f"\n🎁 <b>Tổng mốc quà online nhận được:</b> <code>{total_mocs}</code> mốc"

        # Special accounts description dynamically adapted for 3 accounts (or special_total accounts)
        if special_total == 3:
            special_desc = (
                f"👀 <b>3 Acc Đặc Biệt (IP Chính):</b> <b>{special_online}/3</b> acc đang trực chiến\n"
                f"   • Acc 1 (thinht7113): Quét Sở Giao Dịch Vật Phẩm (SGD)\n"
                f"   • Acc 2 (toilabaon): Quét Sở Nhân Vật\n"
                f"   • Acc 3 (thinht7114): Chuyên Đoạt Lì Xì Kênh Thế Giới\n"
                f"   • Cả 3 Acc: Đoạt Lì Xì (IP Chính cực nhanh) + Nhận Quà Online\n"
                f"   🧧 <b>Tổng Vàng đã đoạt Lì Xì:</b> <b>+{won_gold:,} Vàng</b>"
            )
        else:
            special_desc = (
                f"👀 <b>{special_total} Acc Đặc Biệt (IP Chính):</b> <b>{special_online}/{special_total}</b> acc đang trực chiến\n"
                f"   • Acc 1: Quét Sở Giao Dịch Vật Phẩm (SGD)\n"
                f"   • Acc 2: Quét Sở Nhân Vật\n"
                f"   • Cả {special_total} Acc: Đoạt Lì Xì (IP Chính cực nhanh) + Nhận Quà Online\n"
                f"   🧧 <b>Tổng Vàng đã đoạt Lì Xì:</b> <b>+{won_gold:,} Vàng</b>"
            )

        from modules.daily_history import DailyHistoryManager
        reward_sum = DailyHistoryManager.get_today_online_reward_summary()
        done_accs = reward_sum.get("total_accs_fully_done", reward_sum.get("total_accs_done", 0))
        done_chars = reward_sum.get("total_chars_done", 0)
        partial_accs = reward_sum.get("total_accs_partial", 0)

        if done_accs >= online_target and online_target > 0:
            online_reward_desc = (
                f"<b>{online_realtime}/{online_target}</b> acc online\n"
                f"   • ✅ <b>Đã xong 100% (OFFLINE nghỉ):</b> <b>{done_accs}/{online_target}</b> acc\n"
                f"   • 🎁 <b>Tổng nhân vật hoàn thành:</b> <code>{done_chars}</code> NV\n"
                f"   • 💤 Toàn bộ acc đang ngắt kết nối chờ sự kiện hoặc 00:01 ngày mới"
            )
        elif done_accs > 0 or partial_accs > 0 or online_realtime > 0:
            online_reward_desc = (
                f"<b>{online_realtime}/{online_target}</b> acc đang online\n"
                f"   • 💤 <b>Đã xong 100% (OFFLINE):</b> <b>{done_accs}/{online_target}</b> acc\n"
                f"   • 🔄 <b>Đang treo xoay tua NV 2-3:</b> <b>{online_realtime}/{online_target}</b> acc\n"
                f"   • 🎁 <b>Tổng nhân vật đã nhận quà:</b> <code>{done_chars}</code> NV"
            )
        else:
            online_reward_desc = f"<b>{online_realtime}/{online_target}</b> acc đang giữ kết nối"

        msg = (
            f"📊 <b>{title}</b>\n"
            f"⏱ <i>Cập nhật: {curr_time}</i>\n"
            + ("⏸ <b>CHẾ ĐỘ CHỜ:</b> Đang tạm dừng luồng game (gõ /khoi_dong hoặc /restart để chạy lại)\n" if self.is_paused else "")
            + "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 <b>Máy chủ:</b> {srv}\n"
            f"⌛ <b>Thời gian bot chạy:</b> {uptime}\n"
            f"👥 <b>Tổng tài khoản:</b> <code>{total_accs}</code> acc (Đang online thật sự: <b>{total_real_online}/{total_accs}</b>)\n"
            f"⚡ <b>Số luồng hoạt động:</b> <b>{active_threads}/{total_accs}</b> luồng (Tối đa = số acc)\n"
            f"🛡 <b>Proxy:</b> {'Đang bật (' + str(proxy_count) + ' proxy hoạt động)' if use_proxy else 'Tắt'}\n\n"
            f"{special_desc}\n\n"
            f"🎁 <b>Treo Game Online & Nhận Quà:</b> {online_reward_desc}{claimed_rewards_info}\n\n"
            "⏰ <b>Lịch trình tự động:</b>\n"
            "   • <b>00:01:</b> Re-login toàn bộ Acc & Nhận quà ngày mới\n"
            "   • <b>07:01:</b> Rút Thưởng Login (Nhân vật Lv >= 35)\n"
            "   • <b>08:01:</b> Rút Thưởng May Mắn (Nhân vật Lv30+)\n"
            "   • <b>22:01:</b> Vấn Đáp (Tại chỗ - Mọi cấp độ)\n"
            "   • <b>23:01:</b> Bán vật phẩm vào Shop (Dược, Thuốc, Ngày, Loa)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <i>Gõ /menu để điều khiển nhanh hoặc /help để xem trợ giúp.</i>"
        )
        return msg

    def pause_automation(self):
        """Tạm dừng toàn bộ phân hệ game (online, watcher, scheduler) nhưng giữ nguyên Telegram Bot hoạt động để nhận lệnh khởi động lại."""
        logger.info("⏸ Đang tạm dừng toàn bộ phân hệ game (giữ Telegram Bot ở chế độ chờ)...")
        self.is_paused = True
        self.stop_online_rewards_temporarily()
        if self.special_watcher:
            self.special_watcher.stop()
        if self.scheduler:
            self.scheduler.stop()
        try:
            FreeProxyManager.stop_background_worker()
        except Exception:
            pass
        _trim_process_memory()
        logger.info("Đã chuyển sang chế độ chờ thành công.")

    def stop(self):
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        logger.info("🛑 Đang đóng toàn bộ hệ thống PVTK 24/7...")
        self.is_running = False
        try:
            FreeProxyManager.stop_background_worker()
        except Exception:
            pass
        if self.scheduler:
            self.scheduler.stop()
        if self.telegram_bot:
            self.telegram_bot.stop()
        if self.special_watcher:
            self.special_watcher.stop(timeout=2.0)
        self.stop_online_rewards_temporarily(timeout=3.0)
        try:
            from core.network import graceful_shutdown_all
            graceful_shutdown_all()
        except Exception:
            pass
        logger.info("Đã dừng thành công.")

    def restart_system(self):
        """Dừng sạch sẽ các kết nối và khởi động lại toàn bộ tiến trình/service."""
        self.is_restarting = True
        logger.info("🔄 Đang khởi động lại toàn bộ hệ thống PVTK 24/7 theo lệnh Telegram...")
        try:
            self.stop()
            try:
                sys.stdout.flush()
                sys.stderr.flush()
                logging.shutdown()
            except Exception:
                pass
            time.sleep(0.3)
        finally:
            if os.environ.get("INVOCATION_ID"):
                # Chạy dưới systemd (Restart=on-failure): thoát mã lỗi 1 để systemd tự động khởi động lại sạch sẽ
                os._exit(1)
            else:
                # Chạy trực tiếp trên Windows/Terminal: thay thế tiến trình hiện tại
                os.execv(sys.executable, [sys.executable] + sys.argv)


def main():
    master = PVTKMaster247()
    shutdown_flag = [False]

    def handle_sig(sig, frame):
        shutdown_flag[0] = True
        master.is_running = False

    signal.signal(signal.SIGINT, handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sig)

    master.start()

    # Main thread keep-alive loop
    try:
        while (master.is_running or master.is_restarting) and not shutdown_flag[0]:
            time.sleep(0.2)
    except KeyboardInterrupt:
        shutdown_flag[0] = True
    finally:
        if not master.is_restarting:
            logger.info("[SHUTDOWN] Nhận tín hiệu dừng (SIGINT/SIGTERM), đang đóng kết nối an toàn...")
            master.stop()
            try:
                sys.stdout.flush()
                sys.stderr.flush()
                logging.shutdown()
            except Exception:
                pass
            # Bypass concurrent.futures.thread._python_exit which can hang 90s waiting on dead proxy connect sockets
            os._exit(0)


if __name__ == "__main__":
    main()
