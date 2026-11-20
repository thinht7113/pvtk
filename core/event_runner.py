"""
PVTK Shared Event Runner — Hàm dùng chung cho tất cả trigger_*_event().

Module này loại bỏ ~90% code trùng lặp giữa 4 hàm trigger trong run_247.py:
  - trigger_quiz_event
  - trigger_lottery_event
  - trigger_login_lottery_event
  - trigger_exp_nv_event

Tất cả đều chia sẻ cùng 1 flow chuẩn:
  1. Khóa event_lock → kiểm tra is_event_running
  2. Lấy + lọc danh sách tài khoản (loại bỏ special accounts)
  3. Gửi thông báo bắt đầu qua callback / Telegram
  4. Tạm dừng online rewards
  5. Tính max_workers từ config
  6. Chạy batch_fn (phần khác biệt duy nhất mỗi sự kiện)
  7. Tổng hợp report + gửi kết quả
  8. finally: Khởi động lại online rewards + reset is_event_running

Usage:
    from core.event_runner import run_event, get_target_accounts, get_max_workers
"""

import time
import logging
from typing import Optional, Callable, List, Tuple, Any, Dict

logger = logging.getLogger("PVTK_247")


# ==================== HÀM TIỆN ÍCH DÙNG CHUNG ====================

def get_target_accounts(automation) -> List[Tuple]:
    """
    Lấy danh sách tài khoản thực thi, tự động loại bỏ các acc đặc biệt.
    Hàm dùng chung cho tất cả event và command.

    Args:
        automation: PVTKAutomation247 instance (có get_active_accounts() và special_accounts)

    Returns:
        List[Tuple]: Danh sách tài khoản (username, password, proxy) đã lọc.
    """
    all_active = automation.get_active_accounts()
    special_set = {acc[0].strip().lower() for acc in automation.special_accounts}
    return [acc for acc in all_active if acc[0].strip().lower() not in special_set]


def get_max_workers(automation, total_accounts: int) -> int:
    """
    Tính số luồng tối ưu từ config threading.max_concurrent.
    Mặc định: min(total_accounts, 30). Hàm dùng chung cho tất cả event.

    Args:
        automation: PVTKAutomation247 instance (có self.config)
        total_accounts: Tổng số tài khoản cần xử lý

    Returns:
        int: Số luồng tối ưu.
    """
    cfg = automation.config.get("threading", {})
    max_w = cfg.get("max_concurrent", 0)
    if max_w <= 0:
        max_w = min(max(1, total_accounts), 30)
    return max_w


# ==================== HÀM CHẠY SỰ KIỆN DÙNG CHUNG ====================

def run_event(
    automation,
    event_name: str,
    emoji: str,
    start_description: str,
    batch_fn: Callable[[List[Tuple], int], Optional[Dict[str, Any]]],
    report_fn: Callable[[float, int, Optional[Dict[str, Any]]], str],
    callback: Optional[Callable[[str], None]] = None,
):
    """
    Hàm chạy sự kiện dùng chung — thay thế cho tất cả trigger_*_event() riêng lẻ.

    Flow thực thi chuẩn:
      1. Khóa event_lock → kiểm tra is_event_running (chống chạy song song)
      2. Lấy + lọc danh sách tài khoản (loại bỏ special accounts)
      3. Gửi thông báo bắt đầu qua callback hoặc Telegram
      4. Tạm dừng online rewards (giải phóng socket sạch sẽ)
      5. Tính max_workers + chạy batch_fn (logic khác biệt của mỗi sự kiện)
      6. Format + gửi report kết quả
      7. finally: Luôn khởi động lại online rewards + reset is_event_running

    Args:
        automation: PVTKAutomation247 instance
        event_name: Tên sự kiện (ví dụ: "SỰ KIỆN VẤN ĐÁP TOÀN BỘ TÀI KHOẢN")
        emoji: Icon emoji (ví dụ: "🎓")
        start_description: Mô tả ngắn trạng thái (ví dụ: "Tạm dừng treo quà → Chạy Vấn Đáp...")
        batch_fn: Hàm thực thi chính. Nhận (target_accounts, max_workers) → trả về summary dict hoặc None.
        report_fn: Hàm tạo báo cáo. Nhận (elapsed, total_accounts, summary) → trả về chuỗi HTML report.
        callback: Optional callback gửi message về Telegram.
    """
    # --- 1. Acquire event lock (chống chạy song song) ---
    with automation.event_lock:
        if automation.is_event_running:
            msg = "⚠️ Hiện đang có một sự kiện/tiến trình khác đang chạy. Vui lòng đợi hoàn tất."
            _send(automation, callback, msg)
            return
        automation.is_event_running = True

    try:
        start_t = time.time()

        # --- 2. Lấy + lọc danh sách tài khoản ---
        target_accounts = get_target_accounts(automation)
        total_accounts = len(target_accounts)

        # --- 3. Gửi thông báo bắt đầu ---
        start_msg = (
            f"{emoji} <b>BẮT ĐẦU {event_name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Số tài khoản thực thi:</b> {total_accounts} acc\n"
            f"⚡ <b>Trạng thái:</b> {start_description}\n"
            f"<i>Tiến trình đang bắt đầu...</i>"
        )
        logger.info(f"{emoji} [{event_name}] Bắt đầu xử lý {total_accounts} tài khoản...")
        _send(automation, callback, start_msg)

        # --- 4. Tạm dừng luồng treo online (giải phóng socket) ---
        logger.info(f"⏸ [{event_name}] Tạm dừng luồng treo online nhận quà...")
        automation.stop_online_rewards_temporarily(timeout=6.0)
        time.sleep(1.0)

        # --- 5. Tính max_workers + chạy batch function ---
        max_w = get_max_workers(automation, total_accounts)
        logger.info(f"🚀 [{event_name}] Bắt đầu thực thi ({total_accounts} acc, {max_w} luồng)...")

        summary = None
        try:
            summary = batch_fn(target_accounts, max_w)
        except Exception as ex_run:
            logger.error(f"[{event_name} BATCH ERR] {ex_run}")

        # --- 6. Tạo báo cáo + gửi kết quả ---
        elapsed = round(time.time() - start_t, 1)
        report_msg = report_fn(elapsed, total_accounts, summary)
        logger.info(report_msg)
        _send(automation, callback, report_msg)

    except Exception as e:
        err_msg = f"❌ Lỗi thực thi {event_name}: {e}"
        logger.error(err_msg, exc_info=True)
        _send(automation, callback, err_msg)
    finally:
        # --- 7. Luôn khởi động lại online rewards + giải phóng lock ---
        try:
            logger.info(f"▶️ [{event_name}] Khởi động lại luồng treo online nhận quà...")
            automation.start_online_rewards()
        except Exception as e_res:
            logger.error(f"Lỗi khởi động lại luồng treo online: {e_res}")
        with automation.event_lock:
            automation.is_event_running = False


def _send(automation, callback, msg):
    """Helper gửi message qua callback (ưu tiên) hoặc telegram_bot (fallback)."""
    if callback:
        callback(msg)
    elif automation.telegram_bot:
        automation.telegram_bot.send_message(msg)
