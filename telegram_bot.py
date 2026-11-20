"""
Telegram Bot 2-way Controller for PVTK 24/7 Automation.
Uses standard library urllib for zero external dependencies.
Handles commands, callbacks, test sends, confirmations, and alerts.
"""

import os
import sys
import time
import json
import socket

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

import urllib.request
import urllib.parse
import urllib.error
import threading
import html
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Callable, Tuple
from core.tele_notifier import telegram_api_post, send_telegram_html_safe, strip_html_tags

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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


class TelegramBot:
    def __init__(self, config_path: str = "config_247.json", context=None):
        self.config_path = config_path
        self.context = context # Reference to Master247 / runner state
        self.base_dir = os.path.dirname(os.path.abspath(config_path))
        self.bot_token = ""
        self.chat_id = ""
        self.enabled = is_allowed_telegram_folder(self.base_dir)
        self.admin_ids = []
        
        self.running = False
        self.poll_thread: Optional[threading.Thread] = None
        self.last_update_id = 0
        self.bot_start_time = time.time()
        
        # State tracking for pending confirmations
        # key: chat_id -> dict(action, target, extra, timestamp)
        self.pending_actions: Dict[str, Dict[str, Any]] = {}
        self.action_lock = threading.Lock()

        # Running tasks tracking (để có thể hủy các tiến trình gửi ngầm)
        # key: task_id -> dict(id, type, name, stop_event, start_time, details)
        self.running_tasks: Dict[str, Dict[str, Any]] = {}
        self.running_tasks_lock = threading.Lock()
        self._restarting = False
        
        self.load_config()

    def load_config(self):
        if not is_allowed_telegram_folder(self.base_dir):
            self.enabled = False
            self.bot_token = ""
            self.chat_id = ""
            return
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                tg = cfg.get("telegram", {})
                self.bot_token = tg.get("bot_token", "").strip()
                self.chat_id = str(tg.get("chat_id", "")).strip()
                self.enabled = tg.get("enabled", True)
                self.admin_ids = [str(x) for x in tg.get("admin_ids", [])]
            except Exception as e:
                print(f"[TG BOT] Lỗi đọc config: {e}")

    def save_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg["telegram"]["bot_token"] = self.bot_token
                cfg["telegram"]["chat_id"] = self.chat_id
                cfg["telegram"]["enabled"] = self.enabled
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def _api_call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Optional[Dict[str, Any]]:
        if not self.enabled or not is_allowed_telegram_folder(self.base_dir):
            return None
        if not self.bot_token:
            return None
        ok, res, err_desc = telegram_api_post(self.bot_token, method, params, timeout=timeout)
        if method == "getUpdates" and not ok and "timed out" in err_desc.lower():
            return {"ok": True, "result": []}
        return res

    def send_message(self, text: str, chat_id: Optional[str] = None, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
        if not self.enabled or not is_allowed_telegram_folder(self.base_dir):
            return False
        target_chat = str(chat_id or self.chat_id).strip()
        if not target_chat or not self.bot_token:
            return False
        return send_telegram_html_safe(self.bot_token, target_chat, text, reply_markup=reply_markup, timeout=12)

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None):
        params = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        self._api_call("answerCallbackQuery", params, timeout=5)

    def is_authorized(self, sender_id: str) -> bool:
        sender_str = str(sender_id)
        if not self.chat_id:
            # Auto-bind first user as primary chat_id
            self.chat_id = sender_str
            self.save_config()
            return True
        if sender_str == self.chat_id:
            return True
        if sender_str in self.admin_ids:
            return True
        return False

    def register_bot_commands(self) -> bool:
        """Đăng ký danh sách lệnh với Telegram API để khi gõ / sẽ hiện menu gợi ý."""
        commands = [
            {"command": "menu", "description": "Mở bảng điều khiển tương tác (nút bấm)"},
            {"command": "status", "description": "Xem báo cáo trạng thái hệ thống 24/7"},
            {"command": "dich_chuyen", "description": "Dịch chuyển (1: Thánh Vực, 2: Tân Nguyệt, 3: Thoát lag)"},
            {"command": "van_dap", "description": "Kích hoạt chạy Vấn Đáp (Mọi cấp độ) ngay lập tức"},
            {"command": "rut_login", "description": "Kích hoạt Rút Thưởng Login (Lv35+) ngay lập tức"},
            {"command": "rut_thuong", "description": "Kích hoạt Rút Thưởng May Mắn (Lv30+) ngay"},
            {"command": "lich_su", "description": "Xem lịch sử hoàn thành hôm nay"},
            {"command": "ls_mayman", "description": "Xem lịch sử rút số may mắn các ngày trước đó"},
            {"command": "gui_bac", "description": "Gửi Bạc đến ID nhân vật (Test 1 lần + Xác nhận)"},
            {"command": "gui_exp", "description": "Gửi vé EXP đến ID nhân vật (Test + Xác nhận)"},
            {"command": "gui_vp", "description": "Gửi vật phẩm đến ID nhân vật (Test + Xác nhận)"},
            {"command": "gui_knb", "description": "Gửi toàn bộ KNB từ acc đoạt Lì Xì đến ID người nhận"},
            {"command": "xac_nhan", "description": "Xác nhận thực hiện gửi hàng loạt"},
            {"command": "huy", "description": "Hủy tiến trình gửi đang chờ"},
            {"command": "nhan_thu", "description": "Tự động nhận tất cả hòm thư"},
            {"command": "exp_nv", "description": "Dùng Vé EXP Nhân Vật (Khóa)"},
            {"command": "tao_nv", "description": "Tự động tạo đủ 3 nhân vật cho toàn bộ acc"},
            {"command": "treo_online", "description": "Bật / khôi phục luồng treo nhận quà"},
            {"command": "ban_vp", "description": "Bán vật phẩm chỉ định trong túi đồ vào Shop lấy Bạc"},
            {"command": "vao_thanh", "description": "Xem thông tin & vào Thành của ID chỉ định"},
            {"command": "logs", "description": "Xem 25 dòng nhật ký hoạt động gần nhất"},
            {"command": "khoi_dong", "description": "Khởi động / khởi động lại toàn bộ hệ thống tool"},
            {"command": "restart", "description": "Khởi động lại toàn bộ hệ thống tool (nhanh)"},
            {"command": "help", "description": "Xem hướng dẫn chi tiết các lệnh"},
            {"command": "stop", "description": "Tạm dừng toàn bộ luồng game (giữ bot chờ lệnh)"}
        ]
        res1 = self._api_call("setMyCommands", {"commands": commands})
        self._api_call("setMyCommands", {"commands": commands, "scope": {"type": "all_private_chats"}})
        if self.chat_id:
            self._api_call("setMyCommands", {"commands": commands, "scope": {"type": "chat", "chat_id": self.chat_id}})
        res2 = self._api_call("setChatMenuButton", {"menu_button": {"type": "commands"}})
        ok1 = res1.get("ok", False) if res1 else False
        ok2 = res2.get("ok", False) if res2 else False
        if ok1:
            print("[TG BOT] Đã đăng ký gợi ý lệnh (setMyCommands) thành công cho mọi Scope!")
        return ok1 and ok2

    def flush_pending_updates(self):
        """
        Quản lý tin nhắn tồn đọng khi bot khởi động:
        - Giữ lại các lệnh hợp lệ được gửi gần đây (trong vòng 10 phút).
        - Chỉ bỏ qua các tin nhắn quá cũ (> 10 phút) để tránh nghẽn.
        """
        if not self.bot_token:
            return
        try:
            res = self._api_call("getUpdates", {"limit": 100, "timeout": 0}, timeout=6)
            if res and res.get("ok"):
                results = res.get("result", [])
                if not results:
                    return
                now_epoch = time.time()
                old_ids = []
                fresh_count = 0
                for upd in results:
                    upd_id = upd.get("update_id", 0)
                    msg = upd.get("message") or upd.get("callback_query", {}).get("message", {})
                    msg_date = msg.get("date", 0)
                    if msg_date and (now_epoch - msg_date) > 600:
                        old_ids.append(upd_id)
                    else:
                        fresh_count += 1

                if old_ids and fresh_count == 0:
                    latest_old = max(old_ids)
                    self.last_update_id = latest_old
                    self._api_call("getUpdates", {"offset": latest_old + 1, "timeout": 0}, timeout=6)
                    print(f"[TG BOT] Đã xóa {len(old_ids)} tin nhắn/lệnh quá cũ (>10 phút) trên máy chủ Telegram.")
                elif old_ids and fresh_count > 0:
                    fresh_updates = [u for u in results if (now_epoch - (u.get("message") or u.get("callback_query", {}).get("message", {})).get("date", 0)) <= 600]
                    first_fresh_id = min(u.get("update_id", 0) for u in fresh_updates)
                    self.last_update_id = first_fresh_id - 1
                    print(f"[TG BOT] Giữ lại {len(fresh_updates)} lệnh mới để xử lý ngay, bỏ qua {len(old_ids)} tin cũ.")
        except Exception as e:
            print(f"[TG BOT] Lỗi kiểm tra pending updates: {e}")

    def start(self):
        if self.running:
            return
        if not self.enabled or not is_allowed_telegram_folder(self.base_dir):
            return
        if not self.bot_token:
            print("[TG BOT] Chưa có Telegram bot_token! Bot không khởi động.")
            return
        
        self.bot_start_time = time.time()
        self.flush_pending_updates()
        self.running = True
        self.register_bot_commands()
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="TelegramPollThread")
        self.poll_thread.start()
        print(f"[TG BOT] Đã khởi động Telegram Bot polling (Chat ID: {self.chat_id or 'Chưa gán'}).")
        if self.chat_id:
            ok_start = self.send_message("🤖 <b>PVTK 24/7 Bot đã khởi động thành công!</b>\nGõ /help để xem danh sách lệnh hoặc /menu để mở bảng điều khiển.")
            status_str = "THÀNH CÔNG" if ok_start else "THẤT BẠI (Vui lòng kiểm tra lại Bot Token / Chat ID / Quyền chat)"
            print(f"[TG BOT] Thông báo khởi động tới Chat ID {self.chat_id}: {status_str}.")

    def stop(self):
        self.running = False

    def _poll_loop(self):
        consecutive_errors = 0
        max_consecutive_errors = 50  # Tăng giới hạn lỗi liên tiếp trước khi sleep dài
        while self.running:
            try:
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 20,
                    "allowed_updates": ["message", "callback_query"]
                }
                res = self._api_call("getUpdates", params, timeout=30)
                if not res or not res.get("ok"):
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        # Reset connection state and try from scratch
                        print(f"[TG BOT] ⚠️ Đã gặp {consecutive_errors} lỗi liên tiếp, reset và thử lại sau 30s...")
                        time.sleep(30)
                        consecutive_errors = 0
                    else:
                        time.sleep(min(15, 2 ** min(consecutive_errors, 4)))
                    continue
                
                consecutive_errors = 0
                updates = res.get("result", [])
                for upd in updates:
                    self.last_update_id = max(self.last_update_id, upd.get("update_id", 0))
                    try:
                        self._process_update(upd)
                    except Exception as e:
                        print(f"[TG BOT] Lỗi xử lý update: {e}")

            except Exception as e:
                consecutive_errors += 1
                print(f"[TG BOT] Lỗi poll loop: {e}")
                time.sleep(min(10, 2 ** min(consecutive_errors, 3)))

    def _process_update(self, upd: Dict[str, Any]):
        # Handle Callback Queries (Button clicks)
        if "callback_query" in upd:
            cb = upd["callback_query"]
            cb_id = cb["id"]
            data = cb.get("data", "")
            user_id = str(cb["from"]["id"])
            chat_id = str(cb["message"]["chat"]["id"])
            
            if not self.is_authorized(user_id):
                self.answer_callback_query(cb_id, "Bạn không có quyền sử dụng bot này!")
                return
            
            self._handle_callback(cb_id, chat_id, data)
            return

        # Handle Normal Messages
        if "message" in upd:
            msg = upd["message"]
            msg_date = msg.get("date", 0)
            now_epoch = time.time()
            # Bỏ qua các lệnh quá cũ (> 10 phút)
            if msg_date and (now_epoch - msg_date) > 600:
                return

            user_id = str(msg.get("from", {}).get("id", ""))
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()
            
            if not text:
                return

            if not self.is_authorized(user_id):
                self.send_message("⛔ <b>Bạn không có quyền điều khiển Bot này!</b>", chat_id)
                return

            if self.chat_id and str(chat_id).strip() != str(self.chat_id).strip():
                print(f"[TG BOT] Nhận lệnh '{text[:30]}' từ Chat ID nhóm/kênh ({chat_id}) khác cấu hình mặc định ({self.chat_id}). Phản hồi trực tiếp về Chat {chat_id}.")
            self._handle_command(chat_id, text)

    def register_running_task(self, task_type: str, name: str, stop_event: threading.Event, details: Optional[Dict[str, Any]] = None) -> str:
        task_id = f"{task_type}_{int(time.time() * 1000)}"
        with self.running_tasks_lock:
            self.running_tasks[task_id] = {
                "id": task_id,
                "type": task_type,
                "name": name,
                "stop_event": stop_event,
                "start_time": time.time(),
                "details": details or {}
            }
        return task_id

    def unregister_running_task(self, task_id: str):
        with self.running_tasks_lock:
            self.running_tasks.pop(task_id, None)

    def cancel_all_running_tasks(self) -> List[Dict[str, Any]]:
        stopped = []
        with self.running_tasks_lock:
            for tid, tinfo in list(self.running_tasks.items()):
                stop_ev = tinfo.get("stop_event")
                if stop_ev and not stop_ev.is_set():
                    stop_ev.set()
                    stopped.append(tinfo)
        return stopped

    def _process_cancel(self, chat_id: str, cb_id: Optional[str] = None):
        """
        Xử lý hủy thao tác thông minh & chính xác:
        1. Hủy các lệnh đang chờ xác nhận / nhập ID (Pending Action) kèm thông tin chi tiết.
        2. Dừng khẩn cấp các tiến trình gửi hàng loạt đang chạy ngầm (Active Running Tasks) qua cờ stop_event.
        3. Dừng sự kiện hệ thống (Automation Event Runner) nếu đang chạy.
        4. Nếu không có thao tác nào: Thông báo trung thực và rõ ràng (không báo hủy giả mạo).
        """
        if cb_id:
            self.answer_callback_query(cb_id, "Đang xử lý lệnh dừng...")

        cancelled_items = []

        # 1. Kiểm tra Pending Action
        with self.action_lock:
            pending = self.pending_actions.pop(chat_id, None)

        if pending:
            p_stop_ev = pending.get("stop_event")
            if p_stop_ev and not p_stop_ev.is_set():
                p_stop_ev.set()

            action = pending.get("action", "")
            state = pending.get("state", "")
            target = pending.get("target", "")
            max_mails = pending.get("max_mails")
            keyword = pending.get("keyword", "")

            limit_str = f"{max_mails} thư" if max_mails else "Toàn bộ"

            if action == "gui_bac":
                if state == "waiting_target":
                    desc = "Lệnh <b>Gửi Bạc (COD)</b> — <i>Đang ở bước chờ nhập ID người nhận</i>"
                elif state == "testing":
                    desc = f"Lệnh <b>Gửi Bạc (COD)</b> — <i>Đang quét mẫu thử Bạc đến <code>{target}</code> -> Đã dừng quét thành công!</i>"
                else:
                    desc = f"Lệnh <b>Gửi Bạc Hàng Loạt (COD)</b>\n  • Người nhận: <code>{target}</code> | Số lượng: <b>{limit_str}</b>\n  • Trạng thái: <i>Đã hủy bỏ hoàn toàn, không gửi tiếp thư nào!</i>"
            elif action == "gui_exp":
                if state == "waiting_target":
                    desc = "Lệnh <b>Gửi Vé EXP</b> — <i>Đang ở bước chờ nhập ID người nhận</i>"
                elif state == "testing":
                    desc = f"Lệnh <b>Gửi Vé EXP</b> — <i>Đang quét tìm Vé EXP test đến <code>{target}</code> -> Đã dừng quét thành công!</i>"
                else:
                    desc = f"Lệnh <b>Gửi Vé EXP Hàng Loạt</b>\n  • Người nhận: <code>{target}</code> | Số lượng: <b>{limit_str}</b>\n  • Trạng thái: <i>Đã hủy bỏ hoàn toàn, không gửi tiếp thư nào!</i>"
            elif action == "gui_vp":
                if state == "waiting_target":
                    desc = f"Lệnh <b>Gửi Vật Phẩm ({keyword})</b> — <i>Đang ở bước chờ nhập ID người nhận</i>"
                elif state == "testing":
                    desc = f"Lệnh <b>Gửi Vật Phẩm ({keyword})</b> — <i>Đang quét tìm vật phẩm test đến <code>{target}</code> -> Đã dừng quét thành công!</i>"
                else:
                    desc = f"Lệnh <b>Gửi Vật Phẩm Hàng Loạt</b>\n  • Vật phẩm: <code>{keyword}</code>\n  • Người nhận: <code>{target}</code> | Số lượng: <b>{limit_str}</b>\n  • Trạng thái: <i>Đã hủy bỏ hoàn toàn, không gửi tiếp thư nào!</i>"
            elif action in ("gui_knb", "gui_knb_test", "gui_knb_all"):
                desc = f"Lệnh <b>Chuyển KNB</b> (Người nhận: <code>{target or 'Chưa nhập'}</code>)\n  • Trạng thái: <i>Đã hủy bỏ, số dư KNB được giữ nguyên 100%!</i>"
            elif action == "vao_thanh":
                desc = f"Lệnh <b>Vào Thành</b> (ID Thành: <code>{target or 'Chưa nhập'}</code>)\n  • Trạng thái: <i>Đã hủy yêu cầu vào thành.</i>"
            else:
                desc = f"Thao tác đang chờ: <b>{action}</b> (Trạng thái: {state})"

            cancelled_items.append(("pending", desc))

        # 2. Kiểm tra Running Tasks (Tiến trình ngầm đang chạy)
        stopped_tasks = self.cancel_all_running_tasks()
        for t in stopped_tasks:
            tid = t.get("id", "")
            if pending and pending.get("state") == "testing" and tid.startswith("test_"):
                continue
            tname = t.get("name", "Tiến trình gửi thư")
            tdetails = t.get("details", {})
            ttarget = tdetails.get("target", "")
            elapsed = int(time.time() - t.get("start_time", time.time()))
            target_info = f"\n  • Người nhận: <code>{ttarget}</code>" if ttarget else ""
            desc = (
                f"Tiến trình đang chạy: <b>{tname}</b>{target_info}\n"
                f"  • Đã chạy: <b>{elapsed}s</b> -> 🛑 <i>Đã phát cờ STOP tới tất cả các luồng worker để dừng khẩn cấp!</i>"
            )
            cancelled_items.append(("running", desc))

        # 3. Kiểm tra Sự kiện hệ thống (Automation Event)
        if self.context and hasattr(self.context, "is_event_running") and self.context.is_event_running:
            ev_name = getattr(self.context, "current_event_name", "Sự kiện hệ thống")
            ev_stop = getattr(self.context, "current_event_stop_event", None)
            if ev_stop and not ev_stop.is_set():
                ev_stop.set()
                cancelled_items.append(("event", f"Sự kiện: <b>{ev_name}</b> -> 🛑 <i>Đã phát tín hiệu dừng sự kiện!</i>"))

        # 4. Trả lời người dùng
        if cancelled_items:
            lines = [
                "🛑 <b>XÁC NHẬN HỦY THAO TÁC / DỪNG TIẾN TRÌNH:</b>",
                "━━━━━━━━━━━━━━━━━━━━━━"
            ]
            for it_type, it_desc in cancelled_items:
                if it_type == "pending":
                    lines.append(f"❌ <b>ĐÃ HỦY LỆNH CHỜ:</b>\n{it_desc}")
                elif it_type == "running":
                    lines.append(f"⏹ <b>ĐÃ GỬI LỆNH DỪNG TIẾN TRÌNH NGẦM:</b>\n{it_desc}")
                elif it_type == "event":
                    lines.append(f"⏹ <b>ĐÃ GỬI LỆNH DỪNG SỰ KIỆN:</b>\n{it_desc}")

            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("✅ <b>HỦY THÀNH CÔNG:</b> Các thao tác trên đã được hủy bỏ và các luồng ngầm đang dừng lại an toàn!")
            self.send_message("\n".join(lines), chat_id)
        else:
            self.send_message(
                "ℹ️ <b>HIỆN TẠI KHÔNG CÓ THAO TÁC NÀO ĐỂ HỦY!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Hệ thống không ghi nhận bất kỳ lệnh nào đang chờ xác nhận hay tiến trình gửi thư nào đang chạy.\n\n"
                "💡 <i>Lệnh <code>/huy</code> có tác dụng khi:</i>\n"
                "• Bạn vừa gõ lệnh gửi Bạc/EXP/KNB/VP và đang ở bước chờ xác nhận gửi.\n"
                "• Đang có tiến trình gửi thư hàng loạt chạy ngầm cần dừng khẩn cấp.",
                chat_id
            )

    def _handle_callback(self, cb_id: str, chat_id: str, data: str):
        if data == "cancel":
            self._process_cancel(chat_id, cb_id=cb_id)
            return

        if data.startswith("menu:"):
            sub = data.split(":", 1)[1]
            self.answer_callback_query(cb_id)
            if sub == "status":
                self._cmd_status(chat_id)
            elif sub == "logs":
                self._cmd_logs(chat_id)
            elif sub == "gui_bac":
                self._cmd_gui_bac(chat_id, [])
            elif sub == "gui_exp":
                self._cmd_gui_exp(chat_id, [])
            elif sub == "gui_vp":
                self._cmd_gui_vp(chat_id, [])
            elif sub == "gui_knb":
                self._cmd_gui_knb(chat_id, [])
            elif sub == "nhan_thu":
                self._cmd_nhan_thu(chat_id)
            elif sub in ("exp_nv", "don_dep"):
                self._cmd_exp_nv(chat_id)
            elif sub == "dich_chuyen":
                self._cmd_dich_chuyen(chat_id, [])
            elif sub == "van_dap":
                self._cmd_van_dap(chat_id)
            elif sub == "rut_login":
                self._cmd_rut_login(chat_id)
            elif sub == "rut_thuong":
                self._cmd_rut_thuong(chat_id)
            elif sub == "lich_su":
                self._cmd_lich_su(chat_id)
            elif sub == "ls_mayman":
                self._cmd_ls_mayman(chat_id, [])
            elif sub == "main":
                self._cmd_menu(chat_id)
            elif sub == "treo_online":
                self._cmd_treo_online(chat_id)
            elif sub == "vao_thanh":
                self._cmd_vao_thanh(chat_id, [])
            elif sub == "restart":
                self._cmd_restart(chat_id)
            return

        if data.startswith("ls_mayman_date:"):
            target_date = data.split(":", 1)[1]
            self.answer_callback_query(cb_id)
            self._show_lottery_history_for_date(chat_id, target_date)
            return

        if data.startswith("city_enter:"):
            parts = data.split(":")
            mode = parts[1] if len(parts) > 1 else "single"
            target_str = parts[2] if len(parts) > 2 else ""
            self.answer_callback_query(cb_id)
            if mode == "all":
                self.send_message(f"🚀 <b>Đang gửi lệnh vào Thành của ID <code>{target_str}</code> tới toàn bộ Bot...</b>", chat_id)
                if self.context and hasattr(self.context, "enter_city_all"):
                    self.context.enter_city_all(target_str, callback=lambda m: self.send_message(m, chat_id))
                else:
                    self.send_message("❌ Hệ thống chưa sẵn sàng.", chat_id)
            else:
                self.send_message(f"🏰 <b>Đang thực hiện vào Thành ID <code>{target_str}</code> cho 1 tài khoản test...</b>", chat_id)
                def _worker_single():
                    from modules.city_manager import CityManager
                    accounts = self._get_accounts_safe()
                    ok, msg_res, _ = CityManager.run_enter_city_single(target_str, accounts)
                    if ok:
                        self.send_message(f"✅ <b>[VÀO THÀNH THÀNH CÔNG]</b>\n{msg_res}", chat_id)
                    else:
                        self.send_message(f"❌ <b>[VÀO THÀNH THẤT BẠI]</b>\n{msg_res}", chat_id)
                threading.Thread(target=_worker_single, daemon=True, name="Thread-CitySingle").start()
            return

        if data.startswith("teleport:"):
            sub = data.split(":", 1)[1]
            self.answer_callback_query(cb_id)
            if sub == "thanh_vuc":
                self.send_message("🏛 <b>Đang thực hiện dịch chuyển toàn bộ tài khoản đến Thánh Vực (Yêu cầu Lv ≥ 35)...</b>", chat_id)
                if self.context:
                    self.context.teleport_all("thanh_vuc", callback=lambda m: self.send_message(m, chat_id))
            elif sub == "tan_nguyet":
                self.send_message("🌙 <b>Đang thực hiện dịch chuyển toàn bộ tài khoản đến Tân Nguyệt Trấn (Yêu cầu Lv > 15)...</b>", chat_id)
                if self.context:
                    self.context.teleport_all("tan_nguyet", callback=lambda m: self.send_message(m, chat_id))
            elif sub == "thoat_lag":
                self.send_message("🔄 <b>Đang thực hiện Cài Đặt / H.Thống -> Thoát Lag cho toàn bộ tài khoản (Mọi cấp độ)...</b>", chat_id)
                if self.context:
                    self.context.teleport_all("thoat_lag", callback=lambda m: self.send_message(m, chat_id))
            return

        if data.startswith("confirm:"):
            parts = data.split(":")
            action = parts[1] if len(parts) > 1 else ""
            target = parts[2] if len(parts) > 2 else ""
            extra = ":".join(parts[3:]) if len(parts) > 3 else ""

            self.answer_callback_query(cb_id, "Đã nhận lệnh xác nhận! Bắt đầu thực thi...")
            self._execute_confirmed_action(chat_id, action, target, extra)
            return

        self.answer_callback_query(cb_id)

    def _handle_command(self, chat_id: str, text: str):
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        if cmd in ["/start", "/help"]:
            self._cmd_help(chat_id)
        elif cmd in ["/menu", "/chuc_nang"]:
            self._cmd_menu(chat_id)
        elif cmd == "/status":
            self._cmd_status(chat_id)
        elif cmd == "/gui_bac":
            self._cmd_gui_bac(chat_id, args)
        elif cmd == "/gui_exp":
            self._cmd_gui_exp(chat_id, args)
        elif cmd == "/gui_vp":
            self._cmd_gui_vp(chat_id, args)
        elif cmd == "/gui_knb":
            self._cmd_gui_knb(chat_id, args)
        elif cmd in ["/xac_nhan", "/ok", "y", "yes"]:
            self._cmd_confirm(chat_id)
        elif cmd in ["/huy", "/cancel", "n", "no"]:
            self._process_cancel(chat_id)
        elif cmd == "/nhan_thu":
            self._cmd_nhan_thu(chat_id)
        elif cmd in ["/exp_nv", "/exp", "/don_dep"]:
            self._cmd_exp_nv(chat_id)
        elif cmd == "/tao_nv":
            self._cmd_tao_nv(chat_id)
        elif cmd == "/dich_chuyen":
            self._cmd_dich_chuyen(chat_id, args)
        elif cmd == "/van_dap":
            self._cmd_van_dap(chat_id)
        elif cmd in ("/rut_login", "/login_lottery"):
            self._cmd_rut_login(chat_id)
        elif cmd == "/rut_thuong":
            self._cmd_rut_thuong(chat_id)
        elif cmd in ("/lich_su", "/history"):
            self._cmd_lich_su(chat_id)
        elif cmd in ("/ls_mayman", "/ls_rut_thuong", "/lich_su_may_man", "/lich_su_rut_thuong"):
            self._cmd_ls_mayman(chat_id, args)
        elif cmd == "/treo_online":
            self._cmd_treo_online(chat_id)
        elif cmd in ["/ban_vp", "/xoa_vp", "/ban", "/sell"]:
            self._cmd_ban_vp(chat_id, args)
        elif cmd in ["/vao_thanh", "/thanh", "/city"]:
            self._cmd_vao_thanh(chat_id, args)
        elif cmd in ["/gia", "/price", "/tra_gia"]:
            self._cmd_gia(chat_id, args)
        elif cmd == "/logs":
            self._cmd_logs(chat_id)
        elif cmd in ["/restart", "/khoi_dong", "/khoi_dong_lai", "/bat_tool"]:
            self._cmd_restart(chat_id)
        elif cmd == "/stop":
            self._cmd_stop(chat_id)
        else:
            # Check if user just typed an ID while a prompt is pending
            with self.action_lock:
                pending = self.pending_actions.get(chat_id)
            if pending and pending.get("state") == "waiting_target":
                self._resume_with_target(chat_id, text)
            elif pending and pending.get("state") == "waiting_city_target":
                with self.action_lock:
                    self.pending_actions.pop(chat_id, None)
                self._cmd_vao_thanh(chat_id, [text.strip()])
            else:
                self.send_message(f"❓ Lệnh không hợp lệ: <code>{text}</code>. Gõ /help để xem danh sách lệnh.", chat_id)

    # ================= COMMAND IMPLEMENTATIONS =================

    def _cmd_help(self, chat_id: str):
        msg = (
            "🤖 <b>HỆ THỐNG PVTK 24/7 - DANH SÁCH LỆNH ĐIỀU KHIỂN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎛 <b>Bảng điều khiển nhanh:</b>\n"
            "• <code>/menu</code> : Mở giao diện nút bấm tiện lợi\n\n"
            "📊 <b>Thông tin & Định giá thị trường:</b>\n"
            "• <code>/status</code> : Xem trạng thái toàn hệ thống, 5 acc đặc biệt, RAM, luồng\n"
            "• <code>/gia &lt;tên vật phẩm&gt;</code> : Tra cứu đơn giá trung bình & lịch sử giá Sở Giao Dịch\n"
            "• <code>/logs</code> : Xem các dòng log gần nhất\n\n"
            "💸 <b>Gửi Bạc, KNB & Vật Phẩm (Có xác nhận an toàn):</b>\n"
            "• <code>/gui_knb &lt;ID&gt;</code> : Gửi toàn bộ KNB từ các acc đoạt Lì Xì cho 1 ID người nhận\n"
            "• <code>/gui_bac &lt;ID&gt; [số thư]</code> : Gửi test 1 Bạc → Xác nhận → Gửi Bạc (hỗ trợ giới hạn số thư)\n"
            "• <code>/gui_exp &lt;ID&gt; [số thư]</code> : Gửi test 1 vé EXP → Xác nhận → Gửi vé EXP (hỗ trợ giới hạn số thư)\n"
            "• <code>/gui_vp &lt;ID&gt; &lt;từ khóa&gt; [số thư]</code> : Gửi test → Xác nhận → Gửi vật phẩm (hỗ trợ giới hạn số thư)\n"
            "• <code>/xac_nhan</code> : Xác nhận gửi hàng loạt sau khi nhận được thư test\n"
            "• <code>/huy</code> : Hủy tiến trình gửi\n\n"
            "📦 <b>Tính Năng Tự Động & Hỗ TrỢ:</b>\n"
            "• <code>/nhan_thu</code> : Tự động nhận toàn bộ thư (trừ thư có phí)\n"
            "• <code>/EXP_NV</code> : Dùng toàn bộ Vé EXP Nhân Vật (trạng thái Khóa)\n"
            "• <code>/tao_nv</code> : Tự động tạo đủ 3 nhân vật cho toàn bộ acc\n"
            "• <code>/dich_chuyen [1|2|3]</code> : Dịch chuyển (1: Thánh Vực Lv≥35, 2: Tân Nguyệt Lv>15, 3: Thoát lag)\n"
            "• <code>/van_dap</code> : Kích hoạt chạy Vấn Đáp (Mọi cấp độ) ngay lập tức\n"
            "• <code>/rut_login</code> : Kích hoạt Rút Thưởng Login (Lv35+) ngay lập tức\n"
            "• <code>/rut_thuong</code> : Kích hoạt Rút Thưởng May Mắn (Lv30+) ngay\n"
            "• <code>/lich_su</code> : Xem lịch sử hoàn thành hôm nay\n"
            "• <code>/ls_mayman [ngày]</code> : Xem lịch sử rút số may mắn các ngày trước đó\n"
            "• <code>/treo_online</code> : Kiểm tra & Khởi động lại luồng treo nhận quà\n"
            "• <code>/ban_vp &lt;từ khóa&gt;</code> : Bán vật phẩm chỉ định trong túi đồ vào Shop lấy Bạc (ví dụ: /ban_vp kim cương, thuốc)\n"
            "• <code>/vao_thanh &lt;ID&gt;</code> : Xem thông tin & vào Thành của ID chỉ định (ví dụ: /vao_thanh am6278888)\n"
            "• <code>/khoi_dong</code> hoặc <code>/restart</code> : Khởi động lại toàn bộ tool ngay trên Telegram\n"
            "• <code>/stop</code> : Tạm dừng toàn bộ luồng game (giữ bot Telegram ở chế độ chờ)\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg, chat_id)

    def _cmd_menu(self, chat_id: str):
        msg = (
            "🎛 <b>BẢNG ĐIỀU KHIỂN PVTK 24/7</b>\n"
            "Chọn chức năng bên dưới để điều khiển nhanh:"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "📊 Trạng Thái", "callback_data": "menu:status"},
                    {"text": "📑 Xem Log", "callback_data": "menu:logs"}
                ],
                [
                    {"text": "💰 Gửi KNB (Lì Xì)", "callback_data": "menu:gui_knb"},
                    {"text": "💸 Gửi Bạc", "callback_data": "menu:gui_bac"}
                ],
                [
                    {"text": "🎫 Gửi EXP", "callback_data": "menu:gui_exp"},
                    {"text": "📦 Gửi Vật Phẩm", "callback_data": "menu:gui_vp"}
                ],
                [
                    {"text": "📬 Nhận Thư", "callback_data": "menu:nhan_thu"},
                    {"text": "⚡ Dùng Vé EXP (Khóa)", "callback_data": "menu:exp_nv"}
                ],
                [
                    {"text": "🎁 Rút Login (Lv35+)", "callback_data": "menu:rut_login"},
                    {"text": "🎲 Rút May Mắn", "callback_data": "menu:rut_thuong"}
                ],
                [
                    {"text": "📜 Lịch Sử Hôm Nay", "callback_data": "menu:lich_su"},
                    {"text": "📅 Lịch Sử Rút May Mắn", "callback_data": "menu:ls_mayman"}
                ],
                [
                    {"text": "🎓 Vấn Đáp", "callback_data": "menu:van_dap"},
                    {"text": "🚀 Dịch Chuyển / Thoát Lag", "callback_data": "menu:dich_chuyen"}
                ],
                [
                    {"text": "🏰 Vào Thành", "callback_data": "menu:vao_thanh"},
                    {"text": "🌐 Treo Online", "callback_data": "menu:treo_online"}
                ],
                [
                    {"text": "🔄 Khởi Động Lại Tool", "callback_data": "menu:restart"}
                ],
                [
                    {"text": "❌ Đóng / Hủy", "callback_data": "cancel"}
                ]
            ]
        }
        self.send_message(msg, chat_id, reply_markup=keyboard)

    def _cmd_status(self, chat_id: str):
        if not self.context:
            self.send_message("⚠️ Hệ thống chưa nạp ngữ cảnh hoạt động.", chat_id)
            return

        if hasattr(self.context, "get_formatted_status_message"):
            msg = self.context.get_formatted_status_message("BÁO CÁO TRẠNG THÁI HỆ THỐNG 24/7")
            self.send_message(msg, chat_id)
            return

        status_data = self.context.get_system_status()
        srv = status_data.get("server", "N/A")
        total_accs = status_data.get("total_accounts", 0)
        active_threads = status_data.get("active_threads", 0)
        total_real_online = status_data.get("total_real_online", 0)
        online_realtime = status_data.get("online_reward_realtime", 0)
        online_target = status_data.get("online_reward_target", 0)
        special_accs = status_data.get("special_watchers_count", 0)
        special_total = status_data.get("special_watchers_total", 3)
        won_gold = status_data.get("luck_money_won_gold", 0)
        uptime = status_data.get("uptime_str", "0 phút")
        use_proxy = status_data.get("use_proxy", True)
        proxy_count = status_data.get("proxy_count", 0)

        msg = (
            "📊 <b>BÁO CÁO TRẠNG THÁI HỆ THỐNG 24/7</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 <b>Máy chủ:</b> {srv}\n"
            f"⏱ <b>Thời gian chạy:</b> {uptime}\n"
            f"👥 <b>Tổng tài khoản:</b> <code>{total_accs}</code> acc (Đang online thật sự: <b>{total_real_online}/{total_accs}</b>)\n"
            f"⚡ <b>Số luồng hoạt động:</b> <b>{active_threads}/{total_accs}</b> luồng (Tối đa = số acc)\n"
            f"🛡 <b>Proxy:</b> {'Đang bật (' + str(proxy_count) + ' proxy)' if use_proxy else 'Tắt'}\n\n"
            f"👀 <b>{special_total} Acc Đặc Biệt:</b> {special_accs}/{special_total} đang chạy\n"
            f"   • Acc 1 (thinht7113): Quét Sở Giao Dịch Vật Phẩm (SGD)\n"
            f"   • Acc 2 (toilabaon): Quét Sở Nhân Vật\n"
            f"   • Acc 3 (thinht7114): Chuyên Đoạt Lì Xì Kênh Thế Giới\n"
            f"   • Cả {special_total} Acc: Đoạt Lì Xì Thế Giới (Tổng đoạt: <b>{won_gold:,} Vàng</b>)\n\n"
            f"🎁 <b>Treo Game Online & Nhận Quà:</b> {online_realtime}/{online_target} acc đang giữ kết nối\n\n"
            "⏰ <b>Lịch trình tự động:</b>\n"
            "   • <b>00:01:</b> Re-login toàn bộ Acc & Treo online ngày mới\n"
            "   • <b>07:01:</b> Rút Thưởng Login (NV Lv > 40)\n"
            "   • <b>08:01:</b> Rút Thưởng May Mắn (NV Lv30+)\n"
            "   • <b>22:01:</b> Vấn Đáp (Tại chỗ - Mọi cấp độ)\n"
            "   • <b>23:01:</b> Bán vật phẩm vào Shop (Dược, Thuốc, Ngày, Loa)\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg, chat_id)

    def _cmd_gia(self, chat_id: str, args: List[str]):
        if not args:
            self.send_message(
                "📊 <b>TRA CỨU GIÁ THỊ TRƯỜNG SỞ GIAO DỊCH</b>\n"
                "👉 Cú pháp: <code>/gia &lt;Tên vật phẩm&gt;</code>\n\n"
                "<i>Ví dụ:</i>\n"
                "• <code>/gia cẩm thạch</code>\n"
                "• <code>/gia rương 50</code>\n"
                "• <code>/gia ngọc bích</code>\n"
                "• <code>/gia vé exp</code>",
                chat_id
            )
            return

        keyword = " ".join(args).strip()
        results = []
        if self.context and hasattr(self.context, "query_item_price"):
            results = self.context.query_item_price(keyword)
        else:
            from modules.item_price_tracker import ItemPriceTracker
            tracker = ItemPriceTracker(db_path="storage/item_price_history.json")
            results = tracker.query_item_price(keyword)

        if not results:
            self.send_message(
                f"❌ <b>Không tìm thấy dữ liệu giá cho từ khóa '<code>{keyword}</code>'.</b>\n"
                f"<i>Vật phẩm có thể chưa từng được bày bán trên Sở Giao Dịch kể từ khi bot chạy.</i>",
                chat_id
            )
            return

        lines = [
            f"📊 <b>THỐNG KÊ GIÁ THỊ TRƯỜNG: '{keyword.upper()}'</b>",
            f"<i>(Tìm thấy {len(results)} loại vật phẩm phù hợp trong lịch sử sàn)</i>",
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]

        from modules.character_market_watcher import get_grade_info
        for r in results:
            name = r.get("name", "")
            grade = r.get("grade", 0)
            g_info = get_grade_info(grade)
            icon = g_info.get("icon", "⚪")
            grade_name = g_info.get("name", "")

            g_stats = r.get("gold_stats", {})
            s_stats = r.get("silver_stats", {})
            total_seen = r.get("total_listings_seen", 0)
            last_seen = r.get("last_seen", "N/A")

            lines.append(f"{icon} <b>{name}</b> <i>({grade_name})</i>")

            # Thống kê Vàng
            g_samples = g_stats.get("sample_count", 0)
            if g_samples > 0:
                g_avg = g_stats.get("avg_unit_price", 0)
                g_min = g_stats.get("min_unit_price", 0)
                g_max = g_stats.get("max_unit_price", 0)
                g_med = g_stats.get("median_unit_price", 0)
                lines.append(f"   💰 <b>Vàng:</b> Đơn giá TB: <b>{g_avg:,.1f} V/món</b> <i>(Trung vị: {g_med:,.1f} V)</i>")
                lines.append(f"      • Rẻ nhất: <b>{g_min:,.1f} V</b> | Đắt nhất: <b>{g_max:,.1f} V</b> <i>({g_samples} lượt bán)</i>")

            # Thống kê Bạc
            s_samples = s_stats.get("sample_count", 0)
            if s_samples > 0:
                s_avg = s_stats.get("avg_unit_price", 0)
                s_min = s_stats.get("min_unit_price", 0)
                s_max = s_stats.get("max_unit_price", 0)
                lines.append(f"   🪙 <b>Bạc:</b> Đơn giá TB: <b>{s_avg:,.0f} Bạc/món</b> <i>(Đáy: {s_min:,.0f} | Đỉnh: {s_max:,.0f})</i>")

            lines.append(f"   ⏱ <i>Đã thấy {total_seen} lần trên sàn | Gần nhất: {last_seen}</i>\n")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        self.send_message("\n".join(lines), chat_id)

    # ---- FLOW GỬI BẠC (TEST 1 LẦN -> XÁC NHẬN -> HÀNG LOẠT) ----
    def _cmd_gui_bac(self, chat_id: str, args: List[str]):
        if not args:
            with self.action_lock:
                self.pending_actions[chat_id] = {
                    "action": "gui_bac",
                    "state": "waiting_target",
                    "timestamp": time.time()
                }
            self.send_message(
                "💸 <b>GỬI BẠC CHO NHÂN VẬT CHỈ ĐỊNH</b>\n"
                "👉 Vui lòng gửi ID hoặc Tên nhân vật nhận Bạc vào chat:\n"
                "<i>(Ví dụ: <code>108226</code> để gửi tất cả, hoặc <code>108226 20</code> để chỉ gửi tối đa 20 thư)</i>",
                chat_id
            )
            return

        target_str = args[0].strip()
        max_mails = None
        if len(args) > 1 and args[1].isdigit():
            max_mails = int(args[1])
            if max_mails <= 0:
                max_mails = None

        self._start_silver_flow(chat_id, target_str, max_mails=max_mails)

    def _start_silver_flow(self, chat_id: str, target_str: str, max_mails: Optional[int] = None):
        if not self.context:
            self.send_message("⚠️ Ngữ cảnh hệ thống chưa sẵn sàng.", chat_id)
            return

        accounts = self.context.get_active_accounts()
        if not accounts:
            self.send_message("❌ Không tìm thấy tài khoản nào trong file!", chat_id)
            return

        limit_desc = f"tối đa <b>{max_mails} thư</b>" if max_mails else f"toàn bộ ({len(accounts)} tài khoản)"

        stop_ev = threading.Event()
        with self.action_lock:
            self.pending_actions[chat_id] = {
                "action": "gui_bac",
                "state": "testing",
                "target": target_str,
                "max_mails": max_mails,
                "stop_event": stop_ev,
                "timestamp": time.time()
            }
        task_id = self.register_running_task("test_gui_bac", "Kiểm tra mẫu Bạc test", stop_ev, {"target": target_str})

        self.send_message(
            f"🔍 <b>[BƯỚC KIỂM TRA MẪU]</b> Đang quét nhanh nhân vật có Bạc để gửi thử <b>1 Bạc test</b> đến <code>{target_str}</code>...\n"
            f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng!</i>",
            chat_id
        )

        def _do_test():
            try:
                from modules.silver_sender import run_test_silver_mail
                ok = run_test_silver_mail(accounts, target_str, stop_event=stop_ev)
                self.unregister_running_task(task_id)

                if stop_ev.is_set():
                    return

                if not ok:
                    self.send_message(
                        f"❌ <b>[GỬI TEST THẤT BẠI]</b>\n"
                        f"Không thể gửi 1 Bạc test đến <code>{target_str}</code>.\n"
                        f"Nguyên nhân: Không có nhân vật nào đủ Bạc hoặc sai tên/ID người nhận.\n"
                        f"👉 Đã hủy tiến trình.",
                        chat_id
                    )
                    with self.action_lock:
                        self.pending_actions.pop(chat_id, None)
                    return

                with self.action_lock:
                    self.pending_actions[chat_id] = {
                        "action": "gui_bac",
                        "state": "waiting_confirmation",
                        "target": target_str,
                        "max_mails": max_mails,
                        "timestamp": time.time()
                    }

                cb_extra = str(max_mails) if max_mails else "0"
                btn_text = f"✅ Xác nhận gửi ({max_mails} thư)" if max_mails else "✅ Xác nhận gửi tất cả"
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": btn_text, "callback_data": f"confirm:gui_bac:{target_str}:{cb_extra}"},
                            {"text": "❌ Hủy bỏ", "callback_data": "cancel"}
                        ]
                    ]
                }
                self.send_message(
                    f"✅ <b>[GỬI TEST THÀNH CÔNG]</b>\n"
                    f"Đã gửi <b>1 Bạc</b> test đến người nhận: <code>{target_str}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Người nhận:</b> <code>{target_str}</code>\n"
                    f"📬 <b>Số lượng thư:</b> <b>{limit_desc}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👉 <b>Bạn đã nhận được thư trong game chưa?</b>\n"
                    f"Nhấn nút bên dưới (hoặc gõ <code>/xac_nhan</code>) để bắt đầu gửi Bạc!",
                    chat_id,
                    reply_markup=keyboard
                )
            except Exception as e:
                self.unregister_running_task(task_id)
                print(f"[TG BOT] Lỗi gửi Bạc test: {e}")
                self.send_message(
                    f"❌ <b>[LỖI GỬI TEST]</b>\n"
                    f"Đã xảy ra sự cố trong quá trình quét/gửi Bạc test: <code>{html.escape(str(e))}</code>\n"
                    f"👉 Vui lòng thử lại sau.",
                    chat_id
                )
                with self.action_lock:
                    self.pending_actions.pop(chat_id, None)

        threading.Thread(target=_do_test, daemon=True).start()

    # ---- FLOW GỬI VÉ EXP (TEST 1 LẦN -> XÁC NHẬN -> HÀNG LOẠT) ----
    def _cmd_gui_exp(self, chat_id: str, args: List[str]):
        if not args:
            with self.action_lock:
                self.pending_actions[chat_id] = {
                    "action": "gui_exp",
                    "state": "waiting_target",
                    "timestamp": time.time()
                }
            self.send_message(
                "📜 <b>GỬI VÉ EXP CHO NHÂN VẬT (KHÔNG KHÓA)</b>\n"
                "👉 Vui lòng gửi ID hoặc Tên nhân vật nhận vé EXP vào chat:\n"
                "<i>(Ví dụ: <code>108226</code> để gửi tất cả, hoặc <code>108226 20</code> để chỉ gửi tối đa 20 thư)</i>",
                chat_id
            )
            return

        target_str = args[0].strip()
        max_mails = None
        if len(args) > 1 and args[1].isdigit():
            max_mails = int(args[1])
            if max_mails <= 0:
                max_mails = None

        self._start_exp_flow(chat_id, target_str, max_mails=max_mails)

    def _start_exp_flow(self, chat_id: str, target_str: str, max_mails: Optional[int] = None):
        if not self.context:
            self.send_message("⚠️ Ngữ cảnh hệ thống chưa sẵn sàng.", chat_id)
            return

        accounts = self.context.get_active_accounts()
        if not accounts:
            self.send_message("❌ Không tìm thấy tài khoản nào!", chat_id)
            return

        limit_desc = f"tối đa <b>{max_mails} thư</b>" if max_mails else f"toàn bộ ({len(accounts)} tài khoản)"

        stop_ev = threading.Event()
        with self.action_lock:
            self.pending_actions[chat_id] = {
                "action": "gui_exp",
                "state": "testing",
                "target": target_str,
                "max_mails": max_mails,
                "stop_event": stop_ev,
                "timestamp": time.time()
            }
        task_id = self.register_running_task("test_gui_exp", "Kiểm tra mẫu Vé EXP test", stop_ev, {"target": target_str})

        self.send_message(
            f"🔍 <b>[BƯỚC KIỂM TRA MẪU]</b> Đang quét nhanh nhân vật có Vé EXP (không khóa) để gửi thử test đến <code>{target_str}</code>...\n"
            f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng!</i>",
            chat_id
        )

        def _do_test():
            try:
                from modules.exp_ticket_sender import run_test_item_mail
                ok = run_test_item_mail(accounts, target_str, keywords=["vé exp nhân vật", "ve exp nhan vat"], item_name_display="Vé EXP", stop_event=stop_ev)
                self.unregister_running_task(task_id)

                if stop_ev.is_set():
                    return

                if not ok:
                    self.send_message(
                        f"❌ <b>[GỬI TEST THẤT BẠI]</b>\n"
                        f"Không tìm thấy Vé EXP không khóa nào hoặc gửi test thất bại đến <code>{target_str}</code>.\n"
                        f"👉 Đã hủy tiến trình.",
                        chat_id
                    )
                    with self.action_lock:
                        self.pending_actions.pop(chat_id, None)
                    return

                with self.action_lock:
                    self.pending_actions[chat_id] = {
                        "action": "gui_exp",
                        "state": "waiting_confirmation",
                        "target": target_str,
                        "max_mails": max_mails,
                        "timestamp": time.time()
                    }

                cb_extra = str(max_mails) if max_mails else "0"
                btn_text = f"✅ Xác nhận gửi ({max_mails} thư)" if max_mails else "✅ Xác nhận gửi tất cả"
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": btn_text, "callback_data": f"confirm:gui_exp:{target_str}:{cb_extra}"},
                            {"text": "❌ Hủy bỏ", "callback_data": "cancel"}
                        ]
                    ]
                }
                self.send_message(
                    f"✅ <b>[GỬI TEST THÀNH CÔNG]</b>\n"
                    f"Đã gửi <b>1 Vé EXP</b> test đến người nhận: <code>{target_str}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Người nhận:</b> <code>{target_str}</code>\n"
                    f"📬 <b>Số lượng thư:</b> <b>{limit_desc}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👉 <b>Bạn đã nhận được thư trong game chưa?</b>\n"
                    f"Nhấn nút bên dưới (hoặc gõ <code>/xac_nhan</code>) để bắt đầu gửi vé EXP!",
                    chat_id,
                    reply_markup=keyboard
                )
            except Exception as e:
                self.unregister_running_task(task_id)
                print(f"[TG BOT] Lỗi gửi Vé EXP test: {e}")
                self.send_message(
                    f"❌ <b>[LỖI GỬI TEST]</b>\n"
                    f"Đã xảy ra sự cố trong quá trình quét/gửi Vé EXP test: <code>{html.escape(str(e))}</code>\n"
                    f"👉 Vui lòng thử lại sau.",
                    chat_id
                )
                with self.action_lock:
                    self.pending_actions.pop(chat_id, None)

        threading.Thread(target=_do_test, daemon=True).start()

    # ---- FLOW GỬI VẬT PHẨM (TEST 1 LẦN -> XÁC NHẬN -> HÀNG LOẠT) ----
    def _cmd_gui_vp(self, chat_id: str, args: List[str]):
        if len(args) < 2:
            self.send_message(
                "📦 <b>HƯỚNG DẪN GỬI VẬT PHẨM BẤT KỲ</b>\n"
                "Cú pháp: <code>/gui_vp &lt;ID/Tên người nhận&gt; &lt;Từ khóa vật phẩm&gt; [Số lượng thư]</code>\n\n"
                "<i>Ví dụ:</i>\n"
                "• <code>/gui_vp 108226 rương cấp 50</code> <i>(Gửi toàn bộ)</i>\n"
                "• <code>/gui_vp 108226 rương cấp 50 15</code> <i>(Chỉ gửi tối đa 15 thư)</i>\n"
                "• <code>/gui_vp 108226 rương vũ khí 10</code>\n"
                "• <code>/gui_vp LongKiem đan 20</code>",
                chat_id
            )
            return

        target_str = args[0].strip()
        max_mails = None
        if len(args) >= 3 and args[-1].isdigit():
            max_mails = int(args[-1])
            if max_mails <= 0:
                max_mails = None
            keyword = " ".join(args[1:-1]).strip()
        else:
            keyword = " ".join(args[1:]).strip()

        self._start_vp_flow(chat_id, target_str, keyword, max_mails=max_mails)

    def _start_vp_flow(self, chat_id: str, target_str: str, keyword: str, max_mails: Optional[int] = None):
        if not self.context:
            self.send_message("⚠️ Ngữ cảnh hệ thống chưa sẵn sàng.", chat_id)
            return

        accounts = self.context.get_active_accounts()
        if not accounts:
            self.send_message("❌ Không có tài khoản nào!", chat_id)
            return

        limit_desc = f"tối đa <b>{max_mails} thư</b>" if max_mails else f"toàn bộ ({len(accounts)} tài khoản)"
        keywords = [k.strip().lower() for k in keyword.split(",") if k.strip()]

        stop_ev = threading.Event()
        with self.action_lock:
            self.pending_actions[chat_id] = {
                "action": "gui_vp",
                "state": "testing",
                "target": target_str,
                "keyword": keyword,
                "max_mails": max_mails,
                "stop_event": stop_ev,
                "timestamp": time.time()
            }
        task_id = self.register_running_task("test_gui_vp", f"Kiểm tra mẫu '{keyword}' test", stop_ev, {"target": target_str, "keyword": keyword})

        self.send_message(
            f"🔍 <b>[BƯỚC KIỂM TRA MẪU]</b> Đang quét nhanh nhân vật có vật phẩm '<code>{keyword}</code>' (không khóa) để gửi thư test đến <code>{target_str}</code>...\n"
            f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng!</i>",
            chat_id
        )

        def _do_test():
            try:
                from modules.exp_ticket_sender import run_test_item_mail
                ok = run_test_item_mail(accounts, target_str, keywords=keywords, item_name_display=keyword.upper(), stop_event=stop_ev)
                self.unregister_running_task(task_id)

                if stop_ev.is_set():
                    return

                if not ok:
                    self.send_message(
                        f"❌ <b>[GỬI TEST THẤT BẠI]</b>\n"
                        f"Không tìm thấy vật phẩm nào khớp '<code>{keyword}</code>' (không khóa) hoặc gửi thư thất bại.\n"
                        f"👉 Đã hủy tiến trình.",
                        chat_id
                    )
                    with self.action_lock:
                        self.pending_actions.pop(chat_id, None)
                    return

                with self.action_lock:
                    self.pending_actions[chat_id] = {
                        "action": "gui_vp",
                        "state": "waiting_confirmation",
                        "target": target_str,
                        "keyword": keyword,
                        "max_mails": max_mails,
                        "timestamp": time.time()
                    }

                cb_extra = f"{keyword}|{max_mails if max_mails else 0}"
                btn_text = f"✅ Xác nhận gửi ({max_mails} thư)" if max_mails else "✅ Xác nhận gửi tất cả"
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": btn_text, "callback_data": f"confirm:gui_vp:{target_str}:{cb_extra}"},
                            {"text": "❌ Hủy bỏ", "callback_data": "cancel"}
                        ]
                    ]
                }
                self.send_message(
                    f"✅ <b>[GỬI TEST THÀNH CÔNG]</b>\n"
                    f"Đã gửi 1 '<code>{keyword}</code>' test đến <code>{target_str}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Người nhận:</b> <code>{target_str}</code>\n"
                    f"📦 <b>Vật phẩm:</b> <code>{keyword}</code>\n"
                    f"📬 <b>Số lượng thư:</b> <b>{limit_desc}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👉 <b>Bạn đã nhận được thư chưa?</b>\n"
                    f"Nhấn nút bên dưới để bắt đầu gửi vật phẩm '<code>{keyword}</code>'!",
                    chat_id,
                    reply_markup=keyboard
                )
            except Exception as e:
                self.unregister_running_task(task_id)
                print(f"[TG BOT] Lỗi gửi vật phẩm test: {e}")
                self.send_message(
                    f"❌ <b>[LỖI GỬI TEST]</b>\n"
                    f"Đã xảy ra sự cố trong quá trình quét/gửi vật phẩm test: <code>{html.escape(str(e))}</code>\n"
                    f"👉 Vui lòng thử lại sau.",
                    chat_id
                )
                with self.action_lock:
                    self.pending_actions.pop(chat_id, None)

        threading.Thread(target=_do_test, daemon=True).start()

    # ---- FLOW GỬI KNB TỪ ACC ĐOẠT LÌ XÌ (XÁC NHẬN -> CHUYỂN HẾT) ----
    def _cmd_gui_knb(self, chat_id: str, args: List[str]):
        if not args:
            with self.action_lock:
                self.pending_actions[chat_id] = {
                    "action": "gui_knb",
                    "state": "waiting_target",
                    "timestamp": time.time()
                }
            self.send_message(
                "💰 <b>GỬI TOÀN BỘ KNB TỪ CÁC ACC ĐOẠT LÌ XÌ</b>\n"
                "👉 Vui lòng nhập ID nhân vật nhận KNB vào chat:\n"
                "<i>(Ví dụ: 1033027042 hoặc u33027042 - Đã tắt gửi theo tên, chỉ chấp nhận ID)</i>",
                chat_id
            )
            return

        target_str = args[0].strip()
        self._start_knb_flow(chat_id, target_str)

    def _start_knb_flow(self, chat_id: str, target_str: str):
        if not self.context or not hasattr(self.context, "get_special_watchers_gold"):
            self.send_message("⚠️ Ngữ cảnh hệ thống chưa sẵn sàng.", chat_id)
            return

        from modules.mail_sender import parse_player_id
        target_pid = parse_player_id(target_str)
        if target_pid is None:
            self.send_message(
                "❌ <b>ID người nhận không hợp lệ!</b>\n"
                "Hệ thống đã tắt tính năng gửi theo tên để chống gửi nhầm.\n"
                "👉 Vui lòng chỉ nhập <b>Player ID dạng số</b> (ví dụ: <code>1033027042</code>) hoặc <b>Area ID</b> (ví dụ: <code>u33027042</code>).",
                chat_id
            )
            return

        self.send_message(
            f"🔍 Đang truy vấn số dư KNB của các tài khoản đoạt Lì Xì...",
            chat_id
        )

        def _do_check():
            try:
                gold_map = self.context.get_special_watchers_gold()
                total_gold = sum(gold_map.values())

                lines = [
                    "💰 <b>XÁC NHẬN CHUYỂN TOÀN BỘ KNB TỪ ACC ĐOẠT LÌ XÌ</b>",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    f"🎯 <b>ID Người nhận:</b> <code>{target_pid}</code>" + (f" (Mã: <code>{target_str}</code>)" if str(target_pid) != str(target_str) else "")
                ]
                for u, g in gold_map.items():
                    lines.append(f"• <b>{u}:</b> {g:,} KNB")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                lines.append(f"🏆 <b>Tổng KNB dự kiến chuyển:</b> <code>{total_gold:,} KNB</code>")

                if total_gold <= 0:
                    lines.append("\n⚠️ <i>Hiện không có KNB nào trong các tài khoản đoạt Lì Xì.</i>")
                    self.send_message("\n".join(lines), chat_id)
                    with self.action_lock:
                        self.pending_actions.pop(chat_id, None)
                    return

                with self.action_lock:
                    self.pending_actions[chat_id] = {
                        "action": "gui_knb_test",
                        "state": "waiting_confirmation",
                        "target": target_str,
                        "timestamp": time.time()
                    }

                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "🧪 Gửi TEST (1 KNB / mỗi acc)", "callback_data": f"confirm:gui_knb_test:{target_str}"}
                        ],
                        [
                            {"text": "🚀 Chuyển TOÀN BỘ KNB", "callback_data": f"confirm:gui_knb_all:{target_str}"},
                            {"text": "❌ Hủy bỏ", "callback_data": "cancel"}
                        ]
                    ]
                }
                lines.append("\n👉 <b>Khuyên dùng:</b> Nhấn nút <b>[🧪 Gửi TEST (1 KNB / mỗi acc)]</b> bên dưới hoặc gõ <code>/xac_nhan</code> để gửi thử 1 KNB trước.")
                lines.append("Hoặc nhấn <b>[🚀 Chuyển TOÀN BỘ KNB]</b> nếu bạn muốn chuyển hết ngay!")
                self.send_message("\n".join(lines), chat_id, reply_markup=keyboard)
            except Exception as e:
                print(f"[TG BOT] Lỗi kiểm tra KNB lì xì: {e}")
                self.send_message(f"❌ Lỗi kiểm tra số dư KNB: <code>{html.escape(str(e))}</code>", chat_id)
                with self.action_lock:
                    self.pending_actions.pop(chat_id, None)

        threading.Thread(target=_do_check, daemon=True).start()

    # ---- XỬ LÝ KHI NGƯỜI DÙNG XÁC NHẬN GỬI HÀNG LOẠT ----
    def _cmd_confirm(self, chat_id: str):
        with self.action_lock:
            pending = self.pending_actions.get(chat_id)
        if not pending or pending.get("state") != "waiting_confirmation":
            self.send_message("❓ Hiện không có hành động nào đang chờ xác nhận.", chat_id)
            return

        action = pending.get("action")
        target = pending.get("target")
        max_mails = pending.get("max_mails")
        if action in ("gui_bac", "gui_exp"):
            extra = str(max_mails) if max_mails else "0"
        elif action == "gui_vp":
            extra = f"{pending.get('keyword', '')}|{max_mails if max_mails else 0}"
        else:
            extra = pending.get("keyword", "")
        self._execute_confirmed_action(chat_id, action, target, extra)

    def _execute_confirmed_action(self, chat_id: str, action: str, target: str, extra: str = ""):
        with self.action_lock:
            self.pending_actions.pop(chat_id, None)

        if not self.context:
            self.send_message("⚠️ Ngữ cảnh chưa sẵn sàng.", chat_id)
            return

        accounts = self.context.get_active_accounts()
        total_accs = len(accounts)

        # Giới hạn số luồng tối ưu (tối đa 25 luồng để bảo vệ VPS và tránh nghẽn socket/proxy)
        max_workers = min(total_accs, 25)

        if action == "gui_knb_test":
            self.send_message(
                f"🧪 <b>BẮT ĐẦU GỬI TEST 1 KNB TỪ CÁC ACC ĐOẠT LÌ XÌ</b>\n"
                f"• Người nhận: <code>{target}</code>\n"
                f"• Số lượng: <b>1 KNB / mỗi acc</b>\n"
                f"<i>Đang gửi thư test qua IP chính trực tiếp...</i>",
                chat_id
            )
            def _on_test_done(report_msg: str, res_data: Optional[Dict[str, Any]] = None):
                self.send_message(report_msg, chat_id)
                total = (res_data or {}).get("total_sent", 0)
                if total > 0:
                    with self.action_lock:
                        self.pending_actions[chat_id] = {
                            "action": "gui_knb_all",
                            "state": "waiting_confirmation",
                            "target": target,
                            "timestamp": time.time()
                        }
                    kb = {
                        "inline_keyboard": [
                            [
                                {"text": "🚀 Xác nhận chuyển nốt TOÀN BỘ KNB", "callback_data": f"confirm:gui_knb_all:{target}"},
                                {"text": "❌ Hủy bỏ", "callback_data": "cancel"}
                            ]
                        ]
                    }
                    self.send_message(
                        f"📬 <b>Bạn đã nhận được thư test (1 KNB) trong game chưa?</b>\n"
                        f"Nhấn nút <b>[🚀 Xác nhận chuyển nốt TOÀN BỘ KNB]</b> bên dưới (hoặc gõ <code>/xac_nhan</code>) để chuyển nốt toàn bộ số KNB còn lại!",
                        chat_id,
                        reply_markup=kb
                    )

            if hasattr(self.context, "send_knb_from_special_watchers"):
                self.context.send_knb_from_special_watchers(
                    target,
                    amount_per_account=1,
                    callback=_on_test_done
                )
            return

        if action in ("gui_knb", "gui_knb_all"):
            self.send_message(
                f"🚀 <b>BẮT ĐẦU CHUYỂN TOÀN BỘ KNB TỪ CÁC ACC ĐOẠT LÌ XÌ</b>\n"
                f"• Người nhận: <code>{target}</code>\n"
                f"<i>Đang gửi thư chuyển KNB qua IP chính trực tiếp...</i>",
                chat_id
            )
            if hasattr(self.context, "send_knb_from_special_watchers"):
                self.context.send_knb_from_special_watchers(
                    target,
                    amount_per_account=None,
                    callback=lambda msg, _res: self.send_message(msg, chat_id)
                )
            return

        if action == "gui_bac":
            max_mails = int(extra) if extra and extra.isdigit() and int(extra) > 0 else None
            limit_desc = f"{max_mails} thư" if max_mails else f"Toàn bộ ({total_accs} acc)"
            eff_workers = min(total_accs, max(3, max_mails + 3)) if max_mails else max_workers

            self.send_message(
                f"🚀 <b>BẮT ĐẦU GỬI BẠC HÀNG LOẠT</b>\n"
                f"• Người nhận: <code>{target}</code>\n"
                f"• Giới hạn số thư: <b>{limit_desc}</b>\n"
                f"• Số luồng: <code>{eff_workers}</code> luồng\n"
                f"<i>Tiến trình đang chạy ngầm, bạn sẽ nhận được thông báo khi hoàn tất...</i>\n"
                f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng khẩn cấp!</i>",
                chat_id
            )

            stop_ev = threading.Event()
            task_id = self.register_running_task("gui_bac", "Gửi Bạc Hàng Loạt (COD)", stop_ev, {"target": target, "limit": limit_desc})

            def _worker_silver():
                try:
                    from modules.silver_sender import run_all_silver_sender
                    from modules.free_proxy import FreeProxyManager
                    proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
                    res = run_all_silver_sender(proxy_accounts, target=target, max_workers=eff_workers, max_mails=max_mails, stop_event=stop_ev)
                    
                    silver_amt = res.get("total_silver", 0) if res else 0
                    success_cnt = res.get("total_success", 0) if res else 0
                    elapsed = res.get("elapsed", 0) if res else 0
                    is_canc = res.get("cancelled", False) if res else False
                    is_quota = res.get("quota_reached", False) if res else False

                    if is_canc:
                        header_str = "🛑 <b>TIẾN TRÌNH GỬI BẠC ĐÃ DỪNG THEO LỆNH HỦY</b>"
                        note_str = "⚠️ <i>Tiến trình đã được dừng an toàn theo lệnh /huy!</i>"
                    elif is_quota:
                        header_str = "🎉 <b>BÁO CÁO GỬI BẠC HOÀN TẤT (ĐÃ ĐỦ GIỚI HẠN)</b>"
                        note_str = f"✅ <i>Đã gửi đủ số thư yêu cầu ({limit_desc}). Tự động hoàn tất!</i>"
                    else:
                        header_str = "🎉 <b>BÁO CÁO GỬI BẠC HOÀN TẤT</b>"
                        note_str = "✅ <i>Toàn bộ danh sách đã được xử lý xong!</i>"

                    self.send_message(
                        f"{header_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Người nhận:</b> <code>{target}</code>\n"
                        f"📬 <b>Số thư đã gửi:</b> <b>{success_cnt} Thư</b> (Giới hạn: {limit_desc})\n"
                        f"💰 <b>Tổng số Bạc đã gửi:</b> <b>{silver_amt:,} Bạc</b>\n"
                        f"⏱ <b>Thời gian thực thi:</b> {elapsed} giây\n"
                        f"{note_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━",
                        chat_id
                    )
                except Exception as e:
                    self.send_message(f"❌ <b>Lỗi trong quá trình gửi Bạc:</b> {e}", chat_id)
                finally:
                    self.unregister_running_task(task_id)

            threading.Thread(target=_worker_silver, daemon=True, name="Thread-SilverSender").start()

        elif action == "gui_exp":
            max_mails = int(extra) if extra and extra.isdigit() and int(extra) > 0 else None
            limit_desc = f"{max_mails} thư" if max_mails else f"Toàn bộ ({total_accs} acc)"
            eff_workers = min(total_accs, max(3, max_mails + 3)) if max_mails else max_workers

            self.send_message(
                f"🚀 <b>BẮT ĐẦU GỬI VÉ EXP HÀNG LOẠT</b>\n"
                f"• Người nhận: <code>{target}</code>\n"
                f"• Giới hạn số thư: <b>{limit_desc}</b>\n"
                f"• Số luồng: <code>{eff_workers}</code> luồng\n"
                f"<i>Vui lòng đợi thông báo tổng kết...</i>\n"
                f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng khẩn cấp!</i>",
                chat_id
            )

            stop_ev = threading.Event()
            task_id = self.register_running_task("gui_exp", "Gửi Vé EXP Hàng Loạt", stop_ev, {"target": target, "limit": limit_desc})

            def _worker_exp():
                try:
                    from modules.exp_ticket_sender import run_all_exp_ticket_sender
                    from modules.free_proxy import FreeProxyManager
                    proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
                    res = run_all_exp_ticket_sender(
                        proxy_accounts,
                        target=target,
                        max_workers=eff_workers,
                        keywords=["vé exp nhân vật", "ve exp nhan vat"],
                        mail_title="Gửi Vé EXP",
                        log_title="VÉ EXP",
                        max_mails=max_mails,
                        stop_event=stop_ev
                    )
                    tickets = res.get("total_tickets", 0) if res else 0
                    mails = res.get("total_mails", 0) if res else 0
                    t_time = res.get("total_time", 0) if res else 0
                    is_canc = res.get("cancelled", False) if res else False
                    is_quota = res.get("quota_reached", False) if res else False

                    if is_canc:
                        header_str = "🛑 <b>TIẾN TRÌNH GỬI VÉ EXP ĐÃ DỪNG THEO LỆNH HỦY</b>"
                        note_str = "⚠️ <i>Tiến trình đã được dừng an toàn theo lệnh /huy!</i>"
                    elif is_quota:
                        header_str = "🎉 <b>BÁO CÁO GỬI VÉ EXP HOÀN TẤT (ĐÃ ĐỦ GIỚI HẠN)</b>"
                        note_str = f"✅ <i>Đã gửi đủ số thư yêu cầu ({limit_desc}). Tự động hoàn tất!</i>"
                    else:
                        header_str = "🎉 <b>BÁO CÁO GỬI VÉ EXP HOÀN TẤT</b>"
                        note_str = "✅ <i>Toàn bộ danh sách đã được xử lý xong!</i>"

                    self.send_message(
                        f"{header_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Người nhận:</b> <code>{target}</code>\n"
                        f"📜 <b>Tổng số vé EXP đã gửi:</b> <b>{tickets:,} Vé</b>\n"
                        f"📬 <b>Tổng số thư đã gửi:</b> <b>{mails} Thư</b> (Giới hạn: {limit_desc})\n"
                        f"⏱ <b>Thời gian thực thi:</b> {t_time} giây\n"
                        f"{note_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━",
                        chat_id
                    )
                except Exception as e:
                    self.send_message(f"❌ <b>Lỗi trong quá trình gửi vé EXP:</b> {e}", chat_id)
                finally:
                    self.unregister_running_task(task_id)

            threading.Thread(target=_worker_exp, daemon=True, name="Thread-ExpSender").start()

        elif action == "gui_vp":
            if "|" in extra:
                keyword, max_m_str = extra.rsplit("|", 1)
                max_mails = int(max_m_str) if max_m_str.isdigit() and int(max_m_str) > 0 else None
            else:
                keyword = extra
                max_mails = None

            keywords = [k.strip().lower() for k in keyword.split(",") if k.strip()]
            limit_desc = f"{max_mails} thư" if max_mails else f"Toàn bộ ({total_accs} acc)"
            eff_workers = min(total_accs, max(3, max_mails + 3)) if max_mails else max_workers

            self.send_message(
                f"🚀 <b>BẮT ĐẦU GỬI VẬT PHẨM '{keyword.upper()}' HÀNG LOẠT</b>\n"
                f"• Người nhận: <code>{target}</code>\n"
                f"• Giới hạn số thư: <b>{limit_desc}</b>\n"
                f"• Số luồng: <code>{eff_workers}</code> luồng\n"
                f"<i>Đang tiến hành gửi thư...</i>\n"
                f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng khẩn cấp!</i>",
                chat_id
            )

            stop_ev = threading.Event()
            task_id = self.register_running_task("gui_vp", f"Gửi Vật Phẩm ({keyword.upper()})", stop_ev, {"target": target, "keyword": keyword, "limit": limit_desc})

            def _worker_vp():
                try:
                    from modules.exp_ticket_sender import run_all_exp_ticket_sender
                    from modules.free_proxy import FreeProxyManager
                    proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
                    res = run_all_exp_ticket_sender(
                        proxy_accounts,
                        target=target,
                        max_workers=eff_workers,
                        keywords=keywords,
                        mail_title=f"Gửi {keyword}",
                        log_title=keyword.upper(),
                        max_mails=max_mails,
                        stop_event=stop_ev
                    )
                    tickets = res.get("total_tickets", 0) if res else 0
                    mails = res.get("total_mails", 0) if res else 0
                    t_time = res.get("total_time", 0) if res else 0
                    is_canc = res.get("cancelled", False) if res else False
                    is_quota = res.get("quota_reached", False) if res else False

                    if is_canc:
                        header_str = f"🛑 <b>TIẾN TRÌNH GỬI '{keyword.upper()}' ĐÃ DỪNG THEO LỆNH HỦY</b>"
                        note_str = "⚠️ <i>Tiến trình đã được dừng an toàn theo lệnh /huy!</i>"
                    elif is_quota:
                        header_str = f"🎉 <b>BÁO CÁO GỬI '{keyword.upper()}' HOÀN TẤT (ĐÃ ĐỦ GIỚI HẠN)</b>"
                        note_str = f"✅ <i>Đã gửi đủ số thư yêu cầu ({limit_desc}). Tự động hoàn tất!</i>"
                    else:
                        header_str = f"🎉 <b>BÁO CÁO GỬI '{keyword.upper()}' HOÀN TẤT</b>"
                        note_str = "✅ <i>Toàn bộ danh sách đã được xử lý xong!</i>"

                    self.send_message(
                        f"{header_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Người nhận:</b> <code>{target}</code>\n"
                        f"📦 <b>Tổng số vật phẩm đã gửi:</b> <b>{tickets:,}</b>\n"
                        f"📬 <b>Tổng số thư đã gửi:</b> <b>{mails} Thư</b> (Giới hạn: {limit_desc})\n"
                        f"⏱ <b>Thời gian:</b> {t_time} giây\n"
                        f"{note_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━",
                        chat_id
                    )
                except Exception as e:
                    self.send_message(f"❌ <b>Lỗi trong quá trình gửi vật phẩm:</b> {e}", chat_id)
                finally:
                    self.unregister_running_task(task_id)

            threading.Thread(target=_worker_vp, daemon=True, name="Thread-ItemSender").start()

    def _resume_with_target(self, chat_id: str, target_text: str):
        with self.action_lock:
            pending = self.pending_actions.pop(chat_id, None)
        if not pending:
            return

        action = pending.get("action")
        parts = target_text.strip().split()
        target_str = parts[0]
        max_mails = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0 else None

        if action == "gui_bac":
            self._start_silver_flow(chat_id, target_str, max_mails=max_mails)
        elif action == "gui_exp":
            self._start_exp_flow(chat_id, target_str, max_mails=max_mails)
        elif action == "gui_knb":
            self._start_knb_flow(chat_id, target_str)

    # ---- CÁC LỆNH HỖ TRỢ KHÁC ----
    def _format_mail_claim_report(self, summary: Dict[str, Any]) -> str:
        if not summary:
            return "✅ <b>Hoàn tất nhận toàn bộ thư!</b> (Không có dữ liệu phản hồi)"

        is_canc = summary.get("cancelled", False)
        title_str = "🛑 <b>BÁO CÁO NHẬN THƯ (ĐÃ DỪNG THEO LỆNH HỦY)</b>" if is_canc else "📬 <b>BÁO CÁO KẾT QUẢ NHẬN THƯ TOÀN HỆ THỐNG</b>"

        total_accs = summary.get("total_accounts", 0)
        total_chars = summary.get("total_chars_scanned", 0)
        total_mails = summary.get("total_mails", 0)
        total_silver = summary.get("total_silver", 0)
        items_summary = summary.get("items_summary", {})
        claimed_chars = summary.get("claimed_characters", [])
        elapsed = summary.get("elapsed", 0)

        lines = [
            title_str,
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"👥 <b>Tài khoản quét:</b> {total_accs} acc ({total_chars} nhân vật)",
            f"📨 <b>Tổng số thư đã nhận:</b> <b>{total_mails}</b> thư",
            f"💰 <b>Tổng Bạc thực nhận:</b> <b>+{total_silver:,}</b> Bạc",
            f"⏱ <b>Thời gian thực thi:</b> {elapsed} giây",
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]

        if total_mails == 0:
            lines.append("ℹ️ <i>Tất cả hộp thư đều trống, không có thư nào cần nhận.</i>")
            return "\n".join(lines)

        # Hàm kiểm tra vật phẩm phổ thông (Vé EXP, Sợi Ma Vân, Mở ô kho, Đá Ma Lực, Cuộn Giám Định)
        def _is_common_item(name: str) -> bool:
            n = name.lower()
            if any(k in n for k in ["vé exp", "ve exp"]):
                return True
            if any(k in n for k in ["sợi ma vân", "soi ma van"]):
                return True
            if any(k in n for k in ["mở 2 ô kho", "mở ô kho", "mo o kho", "ô kho", "o kho", "mở kho", "mo kho"]):
                return True
            if any(k in n for k in ["đá ma lực", "da ma luc"]):
                return True
            if any(k in n for k in ["cuộn giám định", "cuon giam dinh", "giám định", "giam dinh"]):
                return True
            return False

        # Gom danh sách tài khoản / nhân vật nhận các vật phẩm đặc biệt (không phải phổ thông)
        item_recipients: Dict[str, List[Tuple[str, str, int]]] = {}
        for c_entry in claimed_chars:
            u = c_entry.get("username", "")
            c = c_entry.get("char_name", "")
            for iname, q in c_entry.get("items", {}).items():
                if not _is_common_item(iname):
                    if iname not in item_recipients:
                        item_recipients[iname] = []
                    item_recipients[iname].append((u, c, q))

        # 1. Chi tiết vật phẩm nhận được (sắp xếp giảm dần theo số lượng)
        if items_summary:
            lines.append("🎁 <b>CHI TIẾT VẬT PHẨM ĐÃ NHẬN:</b>")
            for iname, qty in sorted(items_summary.items(), key=lambda x: -x[1]):
                if _is_common_item(iname):
                    lines.append(f"  • {iname}: <b>{qty:,}</b>")
                else:
                    rec_list = item_recipients.get(iname, [])
                    if rec_list:
                        rec_strs = []
                        max_rec = 10
                        for u, c, q in rec_list[:max_rec]:
                            q_str = f" x{q}" if q > 1 else ""
                            rec_strs.append(f"<code>{u}</code> - {c}{q_str}")
                        if len(rec_list) > max_rec:
                            rec_strs.append(f"...và {len(rec_list) - max_rec} NV khác")
                        rec_display = ", ".join(rec_strs)
                        lines.append(f"  • {iname}: <b>{qty:,}</b> ({rec_display})")
                    else:
                        lines.append(f"  • {iname}: <b>{qty:,}</b>")

        failed_accs = summary.get("failed_accounts", [])
        if failed_accs:
            lines.append("⚠️ <b>TÀI KHOẢN GẶP LỖI / CHƯA XONG:</b>")
            for fa in failed_accs[:15]:
                u = fa.get("username", "")
                err = fa.get("error", "Lỗi mạng hoặc không vào được game")
                lines.append(f"  • <code>{u}</code>: {err}")
            if len(failed_accs) > 15:
                lines.append(f"  <i>...và {len(failed_accs) - 15} tài khoản khác.</i>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")

        full_msg = "\n".join(lines)
        return full_msg

    def _cmd_nhan_thu(self, chat_id: str):
        if not self.context: return
        accounts = self.context.online_reward_accounts if (hasattr(self.context, "online_reward_accounts") and self.context.online_reward_accounts) else self.context.get_active_accounts()
        # Loại trừ các tài khoản watcher đoạt lì xì nếu đang chạy để không gây xung đột socket/IP chính
        if hasattr(self.context, "special_accounts") and self.context.special_accounts:
            special_users = set(acc[0] for acc in self.context.special_accounts)
            accounts = [acc for acc in accounts if acc[0] not in special_users]

        total_cnt = len(accounts)
        eff_workers = min(total_cnt, 20)
        eff_workers = max(15, eff_workers) if total_cnt >= 15 else total_cnt

        stop_ev = threading.Event()
        task_id = self.register_running_task("nhan_thu", "Nhận Toàn Bộ Thư", stop_ev, {"total": total_cnt})
        self.send_message(
            f"📬 Đang nhận toàn bộ thư cho <b>{total_cnt} acc</b> ({eff_workers} luồng song song)...\n"
            f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng!</i>",
            chat_id
        )

        def _do():
            try:
                # 1. Tạm dừng luồng treo online để giải phóng toàn bộ socket, tránh xung đột 2 luồng cùng lúc
                if self.context and hasattr(self.context, "stop_online_rewards_temporarily"):
                    self.context.stop_online_rewards_temporarily(timeout=6.0)
                    time.sleep(1.0)

                from modules.mail_claim import run_all_mail_claim
                from modules.free_proxy import FreeProxyManager
                proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
                summary = run_all_mail_claim(proxy_accounts, max_workers=eff_workers, stop_event=stop_ev)
                report_msg = self._format_mail_claim_report(summary)
                self.send_message(report_msg, chat_id)
            except Exception as e:
                self.send_message(f"❌ <b>Lỗi nhận thư:</b> {e}", chat_id)
            finally:
                self.unregister_running_task(task_id)
                # 2. Khôi phục lại luồng treo online
                if self.context and hasattr(self.context, "start_online_rewards"):
                    self.context.start_online_rewards()

        threading.Thread(target=_do, daemon=True, name="Thread-MailClaim").start()

    def _format_exp_nv_report(self, summary: Dict[str, Any]) -> str:
        if not summary:
            return "✅ <b>Hoàn tất sử dụng Vé EXP Nhân Vật (Khóa)!</b> (Không có dữ liệu phản hồi)"

        total_accs = summary.get("total_accounts", 0)
        total_chars = summary.get("total_chars_scanned", 0)
        total_exp = summary.get("total_exp_used", 0)
        used_chars = summary.get("used_characters", [])
        elapsed = summary.get("elapsed", 0)

        lines = [
            "⚡ <b>BÁO CÁO SỬ DỤNG VÉ EXP NHÂN VẬT (KHÓA)</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"👥 <b>Tài khoản quét:</b> {total_accs} acc ({total_chars} nhân vật)",
            f"🎫 <b>Tổng vé EXP đã dùng:</b> <b>{total_exp:,}</b> vé",
            f"👤 <b>Số nhân vật đã dùng:</b> <b>{len(used_chars)}</b> nhân vật",
            f"⏱ <b>Thời gian thực thi:</b> {elapsed} giây",
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]

        if total_exp == 0:
            lines.append("ℹ️ <i>Không tìm thấy Vé EXP Nhân Vật (Khóa) nào trong túi đồ của các nhân vật.</i>")
            return "\n".join(lines)

        if used_chars:
            lines.append("📋 <b>CHI TIẾT NHÂN VẬT ĐÃ DÙNG VÉ EXP:</b>")
            max_show = 35
            for item in used_chars[:max_show]:
                u = item.get("username", "")
                c = item.get("char_name", "")
                used = item.get("used_exp", 0)
                lines.append(f"  • <code>{u}</code> ({c}): <b>{used}</b> vé EXP khóa")

            if len(used_chars) > max_show:
                lines.append(f"  <i>... và {len(used_chars) - max_show} nhân vật khác.</i>")

        full_msg = "\n".join(lines)
        if len(full_msg) > 3800:
            lines = lines[:lines.index("📋 <b>CHI TIẾT NHÂN VẬT ĐÃ DÙNG VÉ EXP:</b>") + 1]
            for item in used_chars[:15]:
                u = item.get("username", "")
                c = item.get("char_name", "")
                used = item.get("used_exp", 0)
                lines.append(f"  • <code>{u}</code> ({c}): <b>{used}</b> vé EXP khóa")
            lines.append(f"  <i>... và {len(used_chars) - 15} nhân vật khác.</i>")
            full_msg = "\n".join(lines)

        return full_msg

    def _cmd_exp_nv(self, chat_id: str):
        if not self.context:
            self.send_message("⚠️ Ngữ cảnh hệ thống chưa sẵn sàng.", chat_id)
            return

        if hasattr(self.context, "trigger_exp_nv_event"):
            threading.Thread(
                target=self.context.trigger_exp_nv_event,
                args=(lambda msg: self.send_message(msg, chat_id),),
                daemon=True
            ).start()
        else:
            accounts = self.context.get_active_accounts()
            self.send_message(f"⚡ Đang kiểm tra và sử dụng Vé EXP Nhân Vật (Khóa) cho <b>{len(accounts)} acc</b>...", chat_id)

            def _do():
                try:
                    from modules.bag_cleaner import run_all_exp_nv
                    from modules.free_proxy import FreeProxyManager
                    proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
                    summary = run_all_exp_nv(proxy_accounts, max_workers=30)
                    report_msg = self._format_exp_nv_report(summary)
                    self.send_message(report_msg, chat_id)
                except Exception as e:
                    self.send_message(f"❌ <b>Lỗi sử dụng vé EXP:</b> {e}", chat_id)

            threading.Thread(target=_do, daemon=True).start()

    _cmd_don_dep = _cmd_exp_nv

    def _cmd_tao_nv(self, chat_id: str):
        if not self.context: return
        accounts = self.context.get_active_accounts()
        stop_ev = threading.Event()
        task_id = self.register_running_task("tao_nv", "Tạo 3 Nhân Vật Toàn Hệ Thống", stop_ev, {"total": len(accounts)})
        self.send_message(
            f"🧙‍♂️ Đang tự động tạo đủ 3 nhân vật cho <b>{len(accounts)} acc</b>...\n"
            f"💡 <i>Gõ <code>/huy</code> bất kỳ lúc nào nếu muốn dừng!</i>",
            chat_id
        )

        def _do():
            try:
                from modules.character_creator import run_mass_create_characters
                from modules.free_proxy import FreeProxyManager
                proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
                res = run_mass_create_characters(proxy_accounts, target_slots=3, max_workers=len(accounts), stop_event=stop_ev)
                is_canc = res.get("cancelled", False) if isinstance(res, dict) else False
                if is_canc:
                    self.send_message("🛑 <b>Tiến trình tạo nhân vật đã dừng theo lệnh /huy!</b>", chat_id)
                else:
                    created = res.get("total_created", 0) if isinstance(res, dict) else 0
                    done_accs = res.get("completed_accs", 0) if isinstance(res, dict) else len(accounts)
                    self.send_message(
                        f"✅ <b>HOÀN TẤT TẠO NHÂN VẬT!</b>\n"
                        f"• Tài khoản đủ 3 NV: <b>{done_accs}/{len(accounts)}</b>\n"
                        f"• Số NV mới tạo: <b>+{created} NV</b>",
                        chat_id
                    )
            except Exception as e:
                self.send_message(f"❌ <b>Lỗi tạo nhân vật:</b> {e}", chat_id)
            finally:
                self.unregister_running_task(task_id)

        threading.Thread(target=_do, daemon=True, name="Thread-CharCreator").start()

    def _cmd_dich_chuyen(self, chat_id: str, args: List[str]):
        if args:
            raw_target = " ".join(args).strip().lower()
            if any(k in raw_target for k in ["thoát lag", "thoat_lag", "thoat lag", "escape", "3"]):
                self.send_message("🔄 <b>Đang thực hiện Cài Đặt / H.Thống -> Thoát Lag cho toàn bộ tài khoản (Mọi cấp độ)...</b>", chat_id)
                if self.context:
                    self.context.teleport_all("thoat_lag", callback=lambda msg: self.send_message(msg, chat_id))
            elif any(k in raw_target for k in ["tân nguyệt", "tan nguyet", "tan_nguyet", "2"]):
                self.send_message("🌙 <b>Đang thực hiện dịch chuyển sang Tân Nguyệt Trấn (Yêu cầu Lv > 15)...</b>", chat_id)
                if self.context:
                    self.context.teleport_all("tan_nguyet", callback=lambda msg: self.send_message(msg, chat_id))
            else:
                self.send_message("🏛 <b>Đang thực hiện dịch chuyển sang Thánh Vực (Yêu cầu Lv ≥ 35)...</b>", chat_id)
                if self.context:
                    self.context.teleport_all("thanh_vuc", callback=lambda msg: self.send_message(msg, chat_id))
            return

        # Khi không có tham số: Hiển thị bảng chọn trực quan 3 Option
        msg = (
            "🚀 <b>LỰA CHỌN DỊCH CHUYỂN BẢN ĐỒ / THOÁT LAG (OPTION 3)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Vui lòng chọn 1 trong các chức năng dưới đây:\n\n"
            "1️⃣ <b>Thánh Vực (Map ID: 11)</b>\n"
            "   ⚠️ <i>Tự động lọc: Chỉ nhân vật cấp ≥ 35 mới dịch chuyển</i>\n\n"
            "2️⃣ <b>Tân Nguyệt Trấn (Map ID: 37)</b>\n"
            "   ⚠️ <i>Tự động lọc: Chỉ nhân vật cấp > 15 mới dịch chuyển</i>\n\n"
            "3️⃣ <b>Thoát Lag (Cài Đặt / H.Thống -> Thoát lag)</b>\n"
            "   ✅ <i>Không yêu cầu level: Áp dụng cho mọi cấp độ</i>"
        )
        keyboard = {
            "inline_keyboard": [
                [{"text": "🏛 1. Thánh Vực (Lv ≥ 35)", "callback_data": "teleport:thanh_vuc"}],
                [{"text": "🌙 2. Tân Nguyệt Trấn (Lv > 15)", "callback_data": "teleport:tan_nguyet"}],
                [{"text": "🔄 3. Thoát Lag (Mọi cấp)", "callback_data": "teleport:thoat_lag"}],
                [{"text": "❌ Đóng / Hủy", "callback_data": "cancel"}]
            ]
        }
        self.send_message(msg, chat_id, reply_markup=keyboard)

    # ================= HÀM DÙNG CHUNG CHO LỆNH KÍCH HOẠT SỰ KIỆN =================

    def _trigger_event_cmd(self, chat_id: str, label: str, trigger_method: str):
        """
        Hàm dùng chung cho các lệnh kích hoạt sự kiện từ Telegram.
        Thay thế code copy-paste ở _cmd_van_dap, _cmd_rut_thuong, _cmd_rut_login, _cmd_exp_nv.

        Args:
            chat_id: Telegram chat ID
            label: Thông báo xác nhận đã nhận lệnh (HTML)
            trigger_method: Tên method trên self.context (ví dụ: "trigger_quiz_event")
        """
        self.send_message(label, chat_id)
        if self.context and hasattr(self.context, trigger_method):
            method = getattr(self.context, trigger_method)
            threading.Thread(
                target=method,
                args=(lambda msg: self.send_message(msg, chat_id),),
                daemon=True
            ).start()
        else:
            self.send_message("⚠️ Ngữ cảnh hệ thống chưa sẵn sàng.", chat_id)

    def _cmd_van_dap(self, chat_id: str):
        self._trigger_event_cmd(chat_id,
            "🎓 <b>Đã nhận lệnh! Bắt đầu kích hoạt Vấn Đáp (Tại chỗ - Mọi cấp độ) cho toàn bộ tài khoản...</b>",
            "trigger_quiz_event")

    def _cmd_rut_thuong(self, chat_id: str):
        self._trigger_event_cmd(chat_id,
            "🎲 <b>Đã nhận lệnh! Bắt đầu kích hoạt Rút Thưởng May Mắn (Lv30+) cho toàn bộ tài khoản...</b>",
            "trigger_lottery_event")

    def _cmd_rut_login(self, chat_id: str):
        self._trigger_event_cmd(chat_id,
            "🎁 <b>Đã nhận lệnh! Bắt đầu kích hoạt Rút Thưởng Login (Lv >= 35) cho toàn bộ tài khoản...</b>",
            "trigger_login_lottery_event")

    def _cmd_exp_nv(self, chat_id: str):
        self._trigger_event_cmd(chat_id,
            "⚡ <b>Đã nhận lệnh! Bắt đầu sử dụng Vé EXP Nhân Vật (Khóa) cho toàn bộ tài khoản...</b>",
            "trigger_exp_nv_event")

    _cmd_don_dep = _cmd_exp_nv

    def _cmd_lich_su(self, chat_id: str):
        try:
            from modules.daily_history import DailyHistoryManager
            lot_sum = DailyHistoryManager.get_today_lottery_summary()
            quiz_sum = DailyHistoryManager.get_today_quiz_summary()
            login_lot_sum = DailyHistoryManager.get_today_login_lottery_summary()

            lot_records = lot_sum.get("records", [])
            quiz_records = quiz_sum.get("records", [])

            top_records = lot_sum.get("top_records", [])
            lucky_321 = lot_sum.get("lucky_321_records", [])

            msg = (
                "📜 <b>LỊCH SỬ HOÀN THÀNH HÔM NAY</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🎁 <b>Rút Thưởng Login (Vòng xoay Lv35+):</b>\n"
                f"• Đã quét: <b>{login_lot_sum.get('total_scanned', 0)}</b> nhân vật\n"
                f"• Rút thành công: <b>{login_lot_sum.get('total_success', 0)}</b> lượt\n"
                f"• Đã rút từ trước: <b>{login_lot_sum.get('total_already_drawn', 0)}</b> nhân vật\n"
            )
            items_won = login_lot_sum.get("items_won", [])
            valuable_lines = DailyHistoryManager.format_grouped_login_items(items_won, max_chars_per_item=8, bullet="  - ")
            if valuable_lines:
                total_val = sum(1 for it in items_won if not DailyHistoryManager.is_excluded_login_item(it.get("item_name", "")))
                msg += f"• <b>Vật phẩm giá trị ({total_val} món):</b>\n"
                msg += "\n".join(valuable_lines) + "\n"
            else:
                msg += "• <b>Vật phẩm giá trị:</b> <i>(Chỉ trúng vật phẩm thường)</i>\n"

            msg += (
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🎲 <b>Rút Thưởng May Mắn (08:01):</b>\n"
                f"• Đã hoàn thành: <b>{len(lot_records)}</b> nhân vật\n"
                f"• Điểm cao nhất: <b>{lot_sum.get('best_all', 0)}</b> | Trúng số 321: <b>{len(lucky_321)}</b>\n"
            )
            if top_records:
                top_acc = top_records[0]
                msg += f"• 👑 Điểm cao nhất: <b>{top_acc.get('char_name')}</b> (Acc: <code>{top_acc.get('username')}</code>) - <b>{top_acc.get('best_number')}</b> điểm\n"
            if lucky_321:
                lucky_items = ", ".join([f"<b>{r.get('char_name')}</b> (<code>{r.get('username')}</code>)" for r in lucky_321[:3]])
                msg += f"• 🎯 Trúng số 321: {lucky_items}\n"
            else:
                msg += "• 🎯 Trúng số 321: <i>(Chưa có)</i>\n"

            msg += (
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🎓 <b>Vấn Đáp (Tại chỗ):</b>\n"
                f"• Đã hoàn thành: <b>{quiz_sum.get('total_done', 0)}/{len(quiz_records)}</b> nhân vật\n"
                f"• Tổng câu đúng: <b>{quiz_sum.get('total_correct', 0):,}</b> câu\n"
            )
            if quiz_records:
                sample_quiz = quiz_records[:10]
                q_details = "\n".join([f"  - <code>{r.get('char_name', '')}</code>: {r.get('correct', 0)}/{r.get('questions', 0)} câu" for r in sample_quiz])
                if len(quiz_records) > 10:
                    q_details += f"\n  <i>... và {len(quiz_records) - 10} nhân vật khác.</i>"
                msg += f"{q_details}\n"
            else:
                msg += "  <i>(Chưa có nhân vật nào làm vấn đáp hôm nay)</i>\n"

            msg += (
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "💡 <i>Tất cả các nhân vật trên sẽ tự động BỎ QUA khi rút thưởng hoặc vấn đáp chạy lại để tránh lặp đi lặp lại!</i>"
            )
            self.send_message(msg, chat_id)
        except Exception as e:
            self.send_message(f"❌ Lỗi đọc lịch sử: {e}", chat_id)

    def _cmd_ls_mayman(self, chat_id: str, args: List[str]):
        try:
            from datetime import datetime, timezone, timedelta
            from modules.daily_history import DailyHistoryManager
            now_dt = datetime.now(timezone(timedelta(hours=7)))

            if args:
                raw_arg = args[0].strip()
                target_date = ""

                # Case 1: Số nguyên ngày trước (VD: 1 = hôm qua, 2 = 2 ngày trước)
                if raw_arg.isdigit():
                    days_ago = int(raw_arg)
                    target_dt = now_dt - timedelta(days=days_ago)
                    target_date = target_dt.strftime("%Y-%m-%d")
                # Case 2: DD/MM/YYYY hoặc DD/MM
                elif "/" in raw_arg:
                    parts = raw_arg.split("/")
                    if len(parts) == 3:
                        d, m, y = parts
                        if len(y) == 2:
                            y = "20" + y
                        target_date = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
                    elif len(parts) == 2:
                        d, m = parts
                        target_date = f"{now_dt.year:04d}-{int(m):02d}-{int(d):02d}"
                # Case 3: YYYY-MM-DD hoặc MM-DD
                elif "-" in raw_arg:
                    parts = raw_arg.split("-")
                    if len(parts) == 3:
                        target_date = raw_arg
                    elif len(parts) == 2:
                        target_date = f"{now_dt.year:04d}-{int(parts[0]):02d}-{int(parts[1]):02d}"

                if target_date:
                    self._show_lottery_history_for_date(chat_id, target_date)
                    return
                else:
                    self.send_message(
                        f"⚠️ Định dạng ngày '<code>{raw_arg}</code>' không hợp lệ.\n"
                        "💡 Vui lòng nhập theo định dạng: <code>/ls_mayman DD/MM/YYYY</code> hoặc <code>/ls_mayman 1</code> (xem hôm qua).",
                        chat_id
                    )
                    return

            # Nếu không có tham số: Hiển thị tóm tắt các ngày gần nhất kèm nút bấm xem chi tiết
            overview = DailyHistoryManager.get_recent_lottery_overview(limit=6)
            if not overview:
                self.send_message(
                    "🎲 <b>LỊCH SỬ RÚT SỐ MAY MẮN</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⚠️ <i>Hiện chưa có dữ liệu lịch sử rút thưởng nào được lưu.</i>",
                    chat_id
                )
                return

            lines = [
                "🎲 <b>LỊCH SỬ RÚT SỐ MAY MẮN CÁC NGÀY TRƯỚC ĐÓ</b>",
                "━━━━━━━━━━━━━━━━━━━━━━"
            ]

            keyboard_buttons = []
            row = []
            for item in overview:
                d_str = item["date"]
                disp_d = item["display_date"]
                total = item["total_chars"]
                best = item["best_all"]
                hit321 = item["hit_target"]
                hit1000 = item["hit_max"]

                lines.append(
                    f"📅 <b>Ngày {disp_d}:</b>\n"
                    f"  • Tham gia: <b>{total}</b> NV | Điểm cao nhất: <b>{best}</b>\n"
                    f"  • Trúng 321: <b>{hit321}</b> NV | Đạt max 1000: <b>{hit1000}</b> NV"
                )

                short_label = f"📅 {disp_d[:5]}" if disp_d != now_dt.strftime("%d/%m/%Y") else f"📅 Hôm nay ({disp_d[:5]})"
                row.append({"text": short_label, "callback_data": f"ls_mayman_date:{d_str}"})
                if len(row) == 2:
                    keyboard_buttons.append(row)
                    row = []
            if row:
                keyboard_buttons.append(row)

            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(
                "💡 <i>Bấm nút bên dưới để xem chi tiết từng ngày, hoặc gõ:</i>\n"
                "👉 <code>/ls_mayman &lt;ngày&gt;</code> <i>(VD: <code>/ls_mayman 15/10/2026</code> hoặc <code>/ls_mayman 1</code> để xem hôm qua)</i>"
            )

            keyboard_buttons.append([{"text": "🔙 Quay Lại Menu", "callback_data": "menu:main"}])
            reply_markup = {"inline_keyboard": keyboard_buttons}

            self.send_message("\n".join(lines), chat_id, reply_markup=reply_markup)
        except Exception as e:
            self.send_message(f"❌ Lỗi tải lịch sử rút số may mắn: {e}", chat_id)

    def _show_lottery_history_for_date(self, chat_id: str, target_date: str):
        try:
            from modules.daily_history import DailyHistoryManager
            msg = DailyHistoryManager.format_lottery_summary_message(
                title="🎲 <b>CHI TIẾT LỊCH SỬ RÚT THƯỞNG MAY MẮN</b>",
                target_date=target_date
            )
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "📅 Xem Tất Cả Các Ngày", "callback_data": "menu:ls_mayman"},
                        {"text": "🔙 Menu Chính", "callback_data": "menu:main"}
                    ]
                ]
            }
            self.send_message(msg, chat_id, reply_markup=keyboard)
        except Exception as e:
            self.send_message(f"❌ Lỗi hiển thị lịch sử ngày {target_date}: {e}", chat_id)

    def _cmd_treo_online(self, chat_id: str):
        if self.context:
            self.context.restart_online_rewards()
            self.send_message("🎁 Đã khởi động lại luồng treo online & nhận quà cho toàn bộ tài khoản.", chat_id)

    def _cmd_ban_vp(self, chat_id: str, args: List[str]):
        if not args:
            self.send_message(
                "💰 <b>HƯỚNG DẪN BÁN VẬT PHẨM VÀO SHOP</b>\n"
                "👉 Cú pháp: <code>/ban_vp &lt;từ khóa&gt;</code>\n\n"
                "<i>Ví dụ:</i>\n"
                "• <code>/ban_vp kim cương</code>\n"
                "• <code>/ban_vp dược thảo</code>\n"
                "• <code>/ban_vp thời trang</code>\n"
                "• <code>/ban_vp thuốc, phù</code> <i>(Nhiều từ khóa cách nhau bởi dấu phẩy)</i>\n\n"
                "💡 <i>Vật phẩm sẽ được bán trực tiếp vào Shop NPC để thu về Bạc (tương đương nút [Bán] trên giao diện game). Trang bị đang mặc trên người luôn được bảo vệ tuyệt đối.</i>",
                chat_id
            )
            return
        keywords = [k.strip() for k in " ".join(args).split(",") if k.strip()]
        self.send_message(f"💰 Đang tiến hành bán các vật phẩm chứa: <code>{keywords}</code> trong túi đồ toàn bộ acc vào Shop...", chat_id)
        if self.context:
            self.context.delete_items(keywords, callback=lambda msg: self.send_message(msg, chat_id))

    def _cmd_xoa_vp(self, chat_id: str, args: List[str]):
        self._cmd_ban_vp(chat_id, args)

    def _cmd_logs(self, chat_id: str):
        log_file = "bot_247.log"
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                # Lọc bỏ các dòng heartbeat/spam để hiển thị các sự kiện hoạt động thực tế
                meaningful = [
                    l for l in lines
                    if "[THEO DÕI]" not in l and "DEBUG 0x2B65" not in l
                ]
                recent = "".join(meaningful[-25:]) if meaningful else "".join(lines[-25:])
                safe_recent = html.escape(recent[-3500:])
                self.send_message(f"📜 <b>25 DÒNG LOG HOẠT ĐỘNG GẦN NHẤT:</b>\n<pre>{safe_recent}</pre>", chat_id)
            except Exception as e:
                self.send_message(f"Lỗi đọc log: {e}", chat_id)
        else:
            self.send_message("Hiện chưa có file log.", chat_id)

    def _cmd_stop(self, chat_id: str):
        self.cancel_all_running_tasks()
        if self.context and hasattr(self.context, "pause_automation"):
            self.context.pause_automation()

        kb = {
            "inline_keyboard": [
                [{"text": "🚀 Khởi Động Lại Tool Ngay", "callback_data": "menu:restart"}]
            ]
        }
        self.send_message(
            "🛑 <b>Đã tạm dừng toàn bộ luồng treo game & tác vụ chạy ngầm!</b>\n"
            "• Các tài khoản game đã ngắt kết nối an toàn.\n"
            "• Bot Telegram vẫn trực tuyến ở chế độ chờ.\n\n"
            "👉 Nhấn nút bên dưới hoặc gõ <code>/khoi_dong</code> (hay <code>/restart</code>) bất kỳ lúc nào để chạy lại tool!",
            chat_id,
            reply_markup=kb
        )

    def _cmd_restart(self, chat_id: str):
        with self.action_lock:
            if self._restarting:
                self.send_message("⏳ Hệ thống đang trong quá trình khởi động lại, vui lòng đợi vài giây...", chat_id)
                return
            self._restarting = True

        # Xác nhận ngay offset lên Telegram để lệnh /restart này không bị lặp lại sau khi bot bật lại
        try:
            self._api_call("getUpdates", {"offset": self.last_update_id + 1, "timeout": 0}, timeout=5)
        except Exception:
            pass

        self.cancel_all_running_tasks()
        self.send_message(
            "🔄 <b>Đang khởi động lại toàn bộ hệ thống PVTK 24/7...</b>\n"
            "<i>Vui lòng đợi khoảng 5-10 giây, bot sẽ tự động gửi thông báo khi trực tuyến trở lại!</i>",
            chat_id
        )

        def _do_restart():
            time.sleep(0.8)
            if self.context and hasattr(self.context, "restart_system"):
                self.context.restart_system()
            else:
                self.stop()
                time.sleep(0.3)
                if os.environ.get("INVOCATION_ID"):
                    os._exit(1)
                else:
                    os.execv(sys.executable, [sys.executable] + sys.argv)

        threading.Thread(target=_do_restart, daemon=True, name="Thread-SystemRestart").start()

    def _get_accounts_safe(self) -> List[Tuple[str, str]]:
        if self.context and hasattr(self.context, "get_active_accounts"):
            accs = self.context.get_active_accounts()
            if accs:
                return accs
        # Fallback reading accounts file
        try:
            from core.config import settings
            fn = getattr(settings, "accounts_file", "accounts_tuyet_vo_than.txt")
            if not os.path.isabs(fn):
                fn = os.path.join(self.base_dir, fn)
            accounts = []
            if os.path.exists(fn):
                with open(fn, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            parts = line.split("|") if "|" in line else line.split()
                            if len(parts) >= 2:
                                accounts.append((parts[0].strip(), parts[1].strip()))
            return accounts
        except Exception:
            return []

    def _cmd_vao_thanh(self, chat_id: str, args: List[str]):
        """
        Xem thông tin & vào Thành của ID chỉ định:
        /vao_thanh <PlayerID | AreaID>
        Ví dụ: /vao_thanh am6278888 hoặc /vao_thanh 1906278888
        """
        if not args:
            with self.action_lock:
                self.pending_actions[chat_id] = {
                    "action": "vao_thanh",
                    "state": "waiting_city_target",
                    "timestamp": time.time()
                }
            msg = (
                "🏰 <b>VÀO THÀNH CỦA NGƯỜI CHƠI CHỈ ĐỊNH</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Vui lòng nhập ID người chơi hoặc Area ID.\n"
                "Ví dụ: <code>/vao_thanh am6278888</code> hoặc <code>/vao_thanh 1906278888</code>\n\n"
                "👉 <i>Hoặc gõ trực tiếp ID vào khung chat này để tiếp tục.</i>"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "❌ Hủy", "callback_data": "cancel"}]
                ]
            }
            self.send_message(msg, chat_id, reply_markup=keyboard)
            return

        target_str = args[0].strip()
        from modules.city_manager import CityManager
        target_id = CityManager.parse_target_id(target_str)
        if not target_id:
            self.send_message(
                f"❌ <b>[ID KHÔNG HỢP LỆ]</b>\n"
                f"ID <code>{target_str}</code> không đúng định dạng.\n"
                f"👉 Hỗ trợ dạng số nguyên (VD: <code>1906278888</code>) hoặc Area ID (VD: <code>am6278888</code>).",
                chat_id
            )
            return

        self.send_message(f"🔍 <b>Đang truy vấn thông tin Thành của ID <code>{target_str}</code> ({target_id})...</b>\n<i>Vui lòng đợi vài giây...</i>", chat_id)

        def _worker_query():
            try:
                accounts = self._get_accounts_safe()
                ok, data = CityManager.query_city_by_account(target_str, accounts)
                if not ok:
                    err = data.get("error", "Không thể lấy thông tin thành")
                    self.send_message(f"❌ <b>[KHÔNG THỂ XEM THÀNH]</b>\n{err}", chat_id)
                    return

                city_name = data.get("city_name", "Chưa đặt tên")
                scale_name = data.get("scale_name", "")
                status_name = data.get("status_name", "Bình thường")
                sign = data.get("sign", "") or "Chủ thành rất biếng, không để lại gì..."
                master_id = data.get("master_id", target_id)
                master_name = data.get("master_name", target_str)
                master_level = data.get("master_level", 0)
                prosperity = data.get("prosperity", 0)
                army = data.get("army", 0)
                country = data.get("country", "Không")
                union_name = data.get("union_name", "Không")
                gold = data.get("gold", 0)
                leaf = data.get("leaf", 0)
                silver = data.get("silver", 0)

                card = (
                    f"🏰 <b>THÔNG TIN THÀNH CỦA {master_name}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏛 <b>Tên thành:</b> <b>{city_name}</b> (<code>{scale_name}</code>)\n"
                    f"👑 <b>Chủ thành:</b> <b>{master_name}</b> (ID: <code>{master_id}</code> | Lv.<code>{master_level}</code>)\n"
                    f"📍 <b>Trạng thái:</b> <code>{status_name}</code>\n"
                    f"🚩 <b>Quốc gia:</b> {country} | <b>Thế lực:</b> {union_name}\n"
                    f"🌾 <b>Phồn vinh:</b> <code>{prosperity}</code> | ⚔️ <b>Quân lực:</b> <code>{army}</code>\n"
                    f"💰 <b>Quỹ:</b> {gold} Vàng | {leaf} Lá | {silver:,} Bạc\n"
                    f"📝 <b>Ký tên:</b> <i>{sign}</i>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👉 <b>Bạn có muốn đưa nhân vật vào Thành này?</b>"
                )

                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "🚀 Cho Toàn Bộ Bot Vào Thành", "callback_data": f"city_enter:all:{target_str}"}
                        ],
                        [
                            {"text": "🏰 Vào 1 Acc Test", "callback_data": f"city_enter:single:{target_str}"},
                            {"text": "❌ Đóng / Hủy", "callback_data": "cancel"}
                        ]
                    ]
                }
                self.send_message(card, chat_id, reply_markup=keyboard)
            except Exception as e:
                self.send_message(f"❌ <b>Lỗi truy vấn thành:</b> {e}", chat_id)

        threading.Thread(target=_worker_query, daemon=True, name="Thread-QueryCity").start()

