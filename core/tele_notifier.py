"""
Telegram Error & Exception Central Notification Hub for PVTK Automation.
Provides instant, non-blocking, thread-safe notification of any system error,
runtime exception, runner failure, network drop, or warning to Telegram.
Includes:
- Background asynchronous queue (never blocks game / worker threads).
- Smart anti-spam throttling & cooldown per error signature.
- Root logging handler integration (captures all ERROR & CRITICAL logs).
- Global sys.excepthook & threading.excepthook integration (uncaught crashes).
- BaseAccountRunner log watcher (automatically detects runner errors).
"""

import os
import sys
import time
import json
import re
import html
import queue
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

VN_TZ = timezone(timedelta(hours=7))

# Regex to strip ANSI color codes (e.g. \033[91m ... \033[0m)
ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

# Keywords/patterns indicating errors
ERROR_KEYWORDS = [
    "[LỖI", "[ERROR]", "[CRITICAL]", "[FAIL]", "[THẤT BẠI]",
    "LỖI ĐĂNG NHẬP", "LỖI KẾT NỐI", "LỖI NGOẠI LỆ", "LỖI ĐỔI PROXY",
    "LỖI SERVER", "LỖI VẤN ĐÁP", "LỖI RÚT THƯỞNG", "LỖI MẠNG",
    "LỖI ĐỔI NV", "LỖI XỬ LÝ QUÀ", "LỖI THÔNG TIN", "LỖI QUÉT",
    "LỖI VÀO GAME", "VÀO GAME THẤT BẠI", "LỖI DỊCH CHUYỂN", "LỖI NPC",
    "Lỗi không xác định", "Ngoại lệ nghiêm trọng", "Uncaught Exception",
    "Traceback (most recent call last)", "Server từ chối vào game", "❌"
]


def is_allowed_telegram_folder(file_path: Optional[str] = None) -> bool:
    """
    Cho phép chạy trên VPS Linux hoặc trong thư mục Tool hợp lệ.
    Chỉ chặn khi đang chạy ở thư mục cha d:/PVTK/tool1/Tool trên Windows.
    """
    if sys.platform != "win32":
        return True
    target = file_path or os.path.abspath(__file__)
    norm = target.replace("\\", "/").lower()
    if "d:/pvtk/tool1/tool" in norm and not ("/tool/tool" in norm or norm.endswith("/tool/tool")):
        return False
    return True


class TeleNotifier:
    """Singleton dispatcher for Telegram error notifications."""
    _instance: Optional['TeleNotifier'] = None
    _lock = threading.Lock()

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.bot_instance = None
        self.token = ""
        self.chat_id = ""
        self.enabled = is_allowed_telegram_folder(self.base_dir)

        # Anti-spam state: error_key -> last_sent_timestamp
        self._last_sent: Dict[str, float] = {}
        self._suppressed_counts: Dict[str, int] = {}
        self._anti_spam_lock = threading.Lock()

        # Background worker queue for zero-lag dispatching
        self._alert_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._worker_thread: Optional[threading.Thread] = None
        self._is_running = True

        self._load_config()
        self._start_worker()

    @classmethod
    def get_instance(cls, base_dir: Optional[str] = None) -> 'TeleNotifier':
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(base_dir=base_dir)
            return cls._instance

    def _load_config(self):
        """Auto-discover Telegram credentials from known config paths."""
        if not is_allowed_telegram_folder(self.base_dir):
            self.enabled = False
            self.token = ""
            self.chat_id = ""
            return

        config_candidates = [
            os.path.join(self.base_dir, "config_247.json"),
            os.path.join(self.base_dir, "storage", "telegram_notify.json"),
            os.path.join(self.base_dir, "telegram_notify.json"),
            "config_247.json",
            "storage/telegram_notify.json"
        ]

        for cp in config_candidates:
            if os.path.exists(cp):
                try:
                    with open(cp, "r", encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)
                    tg = data.get("telegram", data)
                    token = str(tg.get("bot_token", "")).strip()
                    chat_id = str(tg.get("chat_id", "")).strip()
                    enabled = tg.get("enabled", True)

                    if token and chat_id:
                        self.token = token
                        self.chat_id = chat_id
                        self.enabled = enabled
                        return
                except Exception:
                    pass

    def set_bot(self, bot_instance):
        """Bind an active TelegramBot instance if available."""
        self.bot_instance = bot_instance
        if bot_instance:
            if hasattr(bot_instance, "bot_token") and bot_instance.bot_token:
                self.token = bot_instance.bot_token
            if hasattr(bot_instance, "chat_id") and bot_instance.chat_id:
                self.chat_id = str(bot_instance.chat_id)

    def _start_worker(self):
        """Starts the background worker thread that drains the alert queue."""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(
                target=self._process_queue_loop,
                daemon=True,
                name="TeleNotifierWorker"
            )
            self._worker_thread.start()

    def _process_queue_loop(self):
        """Processes alerts from queue with gentle spacing to avoid Telegram 429."""
        while self._is_running:
            try:
                item = self._alert_queue.get(timeout=1.0)
                if item is None:
                    break
                text, target_chat = item
                self._send_raw_http(text, target_chat=target_chat)
                self._alert_queue.task_done()
                time.sleep(0.3)  # Gentle spacing
            except queue.Empty:
                continue
            except Exception:
                time.sleep(1.0)

    def _send_raw_http(self, text: str, target_chat: Optional[str] = None) -> bool:
        """Sends an HTML formatted message to Telegram via urllib."""
        if not self.enabled or not is_allowed_telegram_folder(self.base_dir):
            return False

        # 1. If active TelegramBot instance is present, try it first
        if self.bot_instance and hasattr(self.bot_instance, "send_message"):
            try:
                ok = self.bot_instance.send_message(text, chat_id=target_chat)
                if ok:
                    return True
            except Exception:
                pass

        # 2. Direct HTTP call fallback
        if not self.token:
            self._load_config()
        if not self.token:
            return False

        chat_id = target_chat or self.chat_id
        if not chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({
            "chat_id": str(chat_id).strip(),
            "text": text,
            "parse_mode": "HTML"
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "PVTK-Notifier/1.0"}
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return bool(data.get("ok", False))
        except Exception:
            return False

    @staticmethod
    def clean_text(text: str) -> str:
        """Removes ANSI terminal color escape codes."""
        if not text:
            return ""
        return ANSI_ESCAPE_RE.sub('', str(text)).strip()

    @staticmethod
    def is_error_text(text: str) -> Tuple[bool, str]:
        """
        Determines whether a message represents an error.
        Returns: (is_error: bool, category_tag: str)
        """
        if not text:
            return False, ""
        clean = TeleNotifier.clean_text(text)
        upper = clean.upper()

        # Ignore common non-error informational messages
        clean_lower = clean.lower()
        if "[đã hủy]" in clean_lower or "bỏ qua" in clean_lower:
            return False, ""

        # Bỏ qua tuyệt đối các thông báo đang thử lại, chạy lại, đổi proxy, kết nối lại...
        # Chỉ thông báo khi tài khoản thực sự không thể vào được nữa (hết lượt thử hoặc sai pass/khóa)
        if any(ign in clean_lower for ign in [
            "thử lại", "chạy lại", "đổi proxy", "chưa vào được",
            "chưa xong", "kết nối lại", "đang kết nối", "hàng đợi chạy vét",
            "thử lại sau", "lượt chạy vét"
        ]):
            return False, ""

        for kw in ERROR_KEYWORDS:
            if kw.upper() in upper:
                # Find the matched tag
                tag_match = re.search(r'\[(.*?)\]', clean)
                category = tag_match.group(0) if tag_match else kw
                return True, category

        return False, ""

    def send_error_alert(
        self,
        title: str,
        detail: str,
        level: str = "ERROR",
        source: str = "",
        cooldown: float = 45.0,
        chat_id: Optional[str] = None
    ) -> bool:
        """
        Submits an error alert for Telegram delivery with anti-spam throttling.
        """
        if not self.enabled or not is_allowed_telegram_folder(self.base_dir):
            return False

        clean_title = self.clean_text(title)
        clean_detail = self.clean_text(detail)
        clean_source = self.clean_text(source) or "Hệ thống PVTK"

        if not clean_detail and not clean_title:
            return False

        # Generate deduplication key (first 120 chars)
        dedup_key = f"{clean_title}:{clean_detail[:120]}"

        now = time.time()
        with self._anti_spam_lock:
            last_time = self._last_sent.get(dedup_key, 0.0)
            if now - last_time < cooldown:
                self._suppressed_counts[dedup_key] = self._suppressed_counts.get(dedup_key, 0) + 1
                return False

            suppressed = self._suppressed_counts.pop(dedup_key, 0)
            self._last_sent[dedup_key] = now

        curr_time = datetime.now(VN_TZ).strftime("%H:%M:%S %d/%m/%Y")
        level_icon = "🚨" if level.upper() in ("CRITICAL", "FATAL") else "⚠️"

        # Truncate detail if too long to fit Telegram limit (4096)
        safe_detail = html.escape(clean_detail[:2500])
        safe_title = html.escape(clean_title)
        safe_source = html.escape(clean_source)

        suppressed_note = f"\n<i>(Lỗi này đã lặp lại {suppressed} lần trong khoảng thời gian vừa qua)</i>" if suppressed > 0 else ""

        message = (
            f"{level_icon} <b>[CẢNH BÁO LỖI HỆ THỐNG]</b>\n"
            f"• <b>Phân hệ / Nguồn:</b> <code>{safe_source}</code>\n"
            f"• <b>Loại lỗi:</b> <b>{safe_title}</b>\n"
            f"• <b>Mức độ:</b> {level.upper()}\n"
            f"• <b>Thời gian:</b> {curr_time}{suppressed_note}\n"
            f"• <b>Chi tiết:</b>\n"
            f"<pre>{safe_detail}</pre>"
        )

        try:
            self._alert_queue.put_nowait((message, chat_id))
            return True
        except queue.Full:
            return False

    def notify_log_error(self, log_message: str, source: str = "", cooldown: float = 45.0) -> bool:
        """
        Parses a raw log line, checks if it is an error, and dispatches to Telegram.
        """
        is_err, category = self.is_error_text(log_message)
        if not is_err:
            return False

        clean_msg = self.clean_text(log_message)

        # Extract source tag if present in log (e.g. [Tài khoản 1/5 | ...])
        tag_match = re.search(r'\[(.*?)\]', clean_msg)
        auto_source = tag_match.group(0) if tag_match else (source or "Runner")

        title = category or "Lỗi Thực Thi"
        return self.send_error_alert(
            title=title,
            detail=clean_msg,
            level="ERROR",
            source=auto_source,
            cooldown=cooldown
        )


class UniversalTelegramLogHandler(logging.Handler):
    """
    Standard logging Handler that pipes any logging.ERROR or logging.CRITICAL
    directly into TeleNotifier.
    """
    def __init__(self, notifier: Optional[TeleNotifier] = None):
        super().__init__(level=logging.ERROR)
        self.notifier = notifier or TeleNotifier.get_instance()

    def emit(self, record):
        try:
            raw_msg = record.getMessage()
            # Ignore expected shutdown noise
            if "interpreter shutdown" in raw_msg.lower() or "cannot schedule new futures" in raw_msg.lower():
                return
            formatted = self.format(record)
            source = f"{record.name} (L{record.lineno})"
            self.notifier.send_error_alert(
                title=f"Log {record.levelname}",
                detail=formatted,
                level=record.levelname,
                source=source,
                cooldown=45.0
            )
        except Exception:
            pass


def install_global_error_handlers(bot_instance=None, target_logger=None):
    """
    Installs:
    1. Telegram logging handler on root logger and target logger.
    2. Global sys.excepthook (main thread unhandled crashes).
    3. Global threading.excepthook (worker threads unhandled crashes).
    """
    notifier = TeleNotifier.get_instance()
    if not is_allowed_telegram_folder(notifier.base_dir):
        return notifier

    if bot_instance:
        notifier.set_bot(bot_instance)

    # 1. Attach to logging
    handler = UniversalTelegramLogHandler(notifier=notifier)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    # Add to root logger
    root_logger = logging.getLogger()
    if not any(isinstance(h, UniversalTelegramLogHandler) for h in root_logger.handlers):
        root_logger.addHandler(handler)

    # Add to target logger if provided
    if target_logger and not any(isinstance(h, UniversalTelegramLogHandler) for h in target_logger.handlers):
        target_logger.addHandler(handler)

    # 2. Main thread unhandled exceptions
    prev_sys_excepthook = sys.excepthook

    def _global_sys_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            if prev_sys_excepthook:
                prev_sys_excepthook(exc_type, exc_value, exc_traceback)
            return
        import traceback
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        notifier.send_error_alert(
            title=f"Ngoại Lệ Chưa Bắt ({exc_type.__name__})",
            detail=f"{exc_value}\n\nTraceback:\n{tb_str}",
            level="CRITICAL",
            source="Main Thread",
            cooldown=10.0
        )
        if prev_sys_excepthook:
            prev_sys_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _global_sys_excepthook

    # 3. Threading unhandled exceptions
    if hasattr(threading, "excepthook"):
        def _global_threading_excepthook(args):
            if issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
                return
            import traceback
            tb_str = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            thread_name = getattr(args.thread, "name", "UnknownThread")
            notifier.send_error_alert(
                title=f"Crash Luồng Con ({args.exc_type.__name__})",
                detail=f"{args.exc_value}\n\nTraceback:\n{tb_str}",
                level="CRITICAL",
                source=f"Thread: {thread_name}",
                cooldown=10.0
            )

        threading.excepthook = _global_threading_excepthook

    return notifier
