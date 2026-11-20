"""
Centralized Telegram Error & System Notification Dispatcher.
Designed for 24/7 headless operation with zero-lag async queue and anti-spam throttling.
Features:
- Singleton instance for cross-module usage.
- Background worker thread with queue to prevent blocking main execution threads.
- Anti-spam deduplication and rate-limiting (cooldown per error signature).
- Automatic plain-text fallback on Telegram 400 Bad Request entity parse errors.
- Automatic backoff and retry on Telegram 429 Too Many Requests (using retry_after).
- Intelligent message splitting for Telegram 4096 character limit.
- Global unhandled exception hook (sys.excepthook & threading.excepthook).
- Logging handler integration (UniversalTelegramLogHandler).
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
import socket
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple, List

VN_TZ = timezone(timedelta(hours=7))

# Regex to strip ANSI color codes (e.g. \033[91m ... \033[0m)
ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

# Regex to strip HTML tags for plain-text fallback
HTML_TAG_RE = re.compile(r'<[^<]+?>')

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


def strip_html_tags(text: str) -> str:
    """Strips HTML tags and unescapes entities for plain-text fallback."""
    if not text:
        return ""
    clean = HTML_TAG_RE.sub('', text)
    return html.unescape(clean).strip()


def split_telegram_message(text: str, max_len: int = 4000) -> List[str]:
    """Splits a long message into chunks <= max_len without breaking lines where possible."""
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    lines = text.split("\n")
    chunks = []
    current_chunk: List[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if line_len > max_len:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            for i in range(0, len(line), max_len):
                chunks.append(line[i:i + max_len])
            continue

        if current_len + line_len > max_len:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = line_len
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def telegram_api_post(
    token: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 15,
    max_retries: int = 2
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Calls Telegram Bot API via HTTPS POST with automatic 429 rate-limit backoff and error logging.
    Returns: (success: bool, response_json: Optional[dict], error_description: str)
    """
    if not token:
        return False, None, "Missing bot_token"

    url = f"https://api.telegram.org/bot{token}/{method}"
    payload = json.dumps(params or {}).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "PVTK-Bot/1.0"}

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                if data.get("ok"):
                    return True, data, ""
                desc = data.get("description", "Unknown error")
                return False, data, desc
        except urllib.error.HTTPError as e:
            err_desc = ""
            data = None
            try:
                err_raw = e.read().decode("utf-8")
                data = json.loads(err_raw)
                err_desc = data.get("description", "")
            except Exception:
                err_desc = str(e)

            if e.code == 429:
                retry_after = 2.0
                if isinstance(data, dict):
                    retry_after = float(data.get("parameters", {}).get("retry_after", 3.0))
                retry_after = max(1.0, min(retry_after, 30.0))
                print(f"[TG API] HTTP 429 Rate limited ({method}). Retrying after {retry_after}s (attempt {attempt + 1}/{max_retries + 1})...")
                if attempt < max_retries:
                    time.sleep(retry_after)
                    continue
                return False, data, f"429 Too Many Requests (retry_after={retry_after}s)"

            print(f"[TG API] Lỗi HTTP {e.code} ({method}): {err_desc}")
            return False, data, err_desc
        except (socket.timeout, urllib.error.URLError) as e:
            print(f"[TG API] Lỗi kết nối mạng ({method}): {e}")
            if attempt < max_retries:
                time.sleep(1.5)
                continue
            return False, None, str(e)
        except Exception as e:
            print(f"[TG API] Lỗi ngoại lệ ({method}): {e}")
            return False, None, str(e)

    return False, None, "Max retries exceeded"


def send_telegram_html_safe(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    timeout: int = 15
) -> bool:
    """
    Sends a message to Telegram with:
    1. Message length splitting (<= 4000 chars per message).
    2. HTML parse_mode with automatic plain-text fallback on 400 Bad Request entity errors.
    3. Rate limit retry handling.
    """
    if not token or not chat_id or not text:
        return False

    chunks = split_telegram_message(text, max_len=4000)
    all_ok = True

    for idx, chunk in enumerate(chunks):
        params: Dict[str, Any] = {
            "chat_id": str(chat_id).strip(),
            "text": chunk,
            "parse_mode": "HTML"
        }
        if reply_markup and idx == len(chunks) - 1:
            params["reply_markup"] = reply_markup

        ok, data, err_desc = telegram_api_post(token, "sendMessage", params, timeout=timeout)
        if not ok:
            err_lower = err_desc.lower()
            if "can't parse entities" in err_lower or "entity" in err_lower or "bad request" in err_lower:
                print(f"[TG API] HTML entity parse error. Thử lại dạng plain-text cho Chat {chat_id}...")
                params.pop("parse_mode", None)
                params["text"] = strip_html_tags(chunk)
                ok_fb, _, _ = telegram_api_post(token, "sendMessage", params, timeout=timeout)
                if not ok_fb:
                    all_ok = False
            else:
                all_ok = False

        if len(chunks) > 1 and idx < len(chunks) - 1:
            time.sleep(0.4)

    return all_ok


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
                try:
                    self._send_raw_http(text, target_chat=target_chat)
                finally:
                    self._alert_queue.task_done()
                time.sleep(0.5)  # Gentle spacing to stay safely below rate limits
            except queue.Empty:
                continue
            except Exception:
                time.sleep(1.0)

    def _send_raw_http(self, text: str, target_chat: Optional[str] = None) -> bool:
        """Sends an HTML formatted message to Telegram."""
        if not self.enabled or not is_allowed_telegram_folder(self.base_dir):
            return False

        # 1. If active TelegramBot instance is present, try it first
        if self.bot_instance and hasattr(self.bot_instance, "send_message"):
            try:
                ok = self.bot_instance.send_message(text, chat_id=target_chat)
                # If bot_instance handled the call, do not make duplicate raw HTTP call
                return bool(ok)
            except Exception as e:
                print(f"[TELE_NOTIFIER] Lỗi qua bot_instance: {e}, fallback sang HTTP trực tiếp...")

        # 2. Direct HTTP call fallback using safe sender
        if not self.token:
            self._load_config()
        if not self.token:
            return False

        chat_id = target_chat or self.chat_id
        if not chat_id:
            return False

        return send_telegram_html_safe(self.token, str(chat_id).strip(), text, timeout=10)

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
        chat_id: Optional[str] = None,
        dedup_key: Optional[str] = None
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

        # Generate / normalize deduplication key
        if not dedup_key:
            norm_title = re.sub(r"\(?Lần \d+\)?", "Lần X", clean_title, flags=re.IGNORECASE)
            norm_title = re.sub(r"\d+\s+lần", "X lần", norm_title, flags=re.IGNORECASE)
            norm_detail = re.sub(r"\(?Lần \d+\)?", "Lần X", clean_detail[:150], flags=re.IGNORECASE)
            norm_detail = re.sub(r"\d+\s+lần", "X lần", norm_detail, flags=re.IGNORECASE)
            norm_detail = re.sub(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+", "HOST:PORT", norm_detail)
            norm_detail = re.sub(r"\d{2}:\d{2}:\d{2}", "TIME", norm_detail)
            dedup_key = f"{norm_title}:{norm_detail}"
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

        # Truncate detail if too long to fit Telegram limit
        safe_detail = html.escape(clean_detail[:2200])
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
            if getattr(record, "_tele_notified", False):
                return
            record._tele_notified = True
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
