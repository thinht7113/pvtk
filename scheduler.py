"""
Job Scheduler for PVTK 24/7 Automation — PROFESSIONAL ENTERPRISE EDITION.
Manages automated schedules according to Vietnam Timezone (Asia/Ho_Chi_Minh - GMT+7):
1. 00:01 - 07:59: Daily re-login & fresh online reward keep-alive for all accounts.
2. 22:01 - 22:30: Quiz Event (Vấn Đáp Thánh Vực) window checking for all accounts.
3. 08:00 - 23:59: Lottery Event (Rút Thưởng May Mắn) for characters Lv >= 30.
4. Periodic health check every 5 minutes.
5. Periodic status report to Telegram (Configurable, default every 30 minutes).

Features:
- Window-based trigger: Never misses an event even if bot is restarted or delayed.
- Persistent state (storage/scheduler_state.json): Prevents duplicate runs across restarts.
- Non-blocking execution via isolated worker threads.
"""

import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

VN_TZ = timezone(timedelta(hours=7))


class PVTKScheduler:
    def __init__(self, context=None, telegram_bot=None):
        self.context = context
        self.telegram_bot = telegram_bot
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.state_file = os.path.join(self.base_dir, "storage", "scheduler_state.json")

        # Load persistent state
        self.last_triggered_relogin_date = ""
        self.last_triggered_quiz_date = ""
        self.last_triggered_lottery_date = ""
        self.last_triggered_login_lottery_date = ""
        self.last_triggered_auto_sell_date = ""
        self.last_health_check_time = 0
        self.last_status_report_time = time.time()
        self._load_state()

        # Dynamic Schedule Configuration (from config_247.json)
        self.reload_config()

    def reload_config(self):
        """Đọc lại cấu hình giờ chạy từ config_247.json."""
        cfg_schedule: Dict[str, Any] = {}
        if self.context and hasattr(self.context, "config"):
            cfg_schedule = self.context.config.get("schedule", {})

        self.relogin_time = cfg_schedule.get("daily_relogin", "00:01")
        self.quiz_start = cfg_schedule.get("quiz_start", "22:01")
        self.quiz_end = cfg_schedule.get("quiz_end", "22:30")
        self.lottery_start = cfg_schedule.get("lottery_start", "08:01")
        self.auto_sell_time = cfg_schedule.get("auto_sell_time", "23:01")
        self.auto_sell_keywords = cfg_schedule.get("auto_sell_keywords", ["Dược", "Thuốc", "Ngày", "Loa"])
        self.login_lottery_time = cfg_schedule.get("login_lottery_time", "07:01")
        self.login_lottery_min_level = cfg_schedule.get("login_lottery_min_level", 35)
        
        report_mins = cfg_schedule.get("status_report_interval_minutes", 180)
        self.status_report_interval = max(60, int(report_mins * 60))

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.last_triggered_relogin_date = data.get("last_relogin_date", "")
                    self.last_triggered_quiz_date = data.get("last_quiz_date", "")
                    self.last_triggered_lottery_date = data.get("last_lottery_date", "")
                    self.last_triggered_login_lottery_date = data.get("last_login_lottery_date", "")
                    self.last_triggered_auto_sell_date = data.get("last_auto_sell_date", "")
            except Exception:
                pass

    def _save_state(self):
        try:
            from core.storage_utils import atomic_write_json
            atomic_write_json(self.state_file, {
                "last_relogin_date": self.last_triggered_relogin_date,
                "last_quiz_date": self.last_triggered_quiz_date,
                "last_lottery_date": self.last_triggered_lottery_date,
                "last_login_lottery_date": self.last_triggered_login_lottery_date,
                "last_auto_sell_date": self.last_triggered_auto_sell_date,
                "updated_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
            }, indent=2)
        except Exception:
            pass

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="SchedulerThread")
        self.thread.start()

        mins_report = int(self.status_report_interval // 60)
        report_str = f"{mins_report // 60} giờ" if mins_report % 60 == 0 else f"{mins_report} phút"
        print(f"[SCHEDULER] ✅ Đã khởi động bộ lập lịch thông minh (Múi giờ VN GMT+7):")
        print(f"  • {self.relogin_time} hàng ngày: Re-login toàn bộ Acc & Treo nhận quà Online ngày mới")
        print(f"  • {self.login_lottery_time} hàng ngày: Tự động Rút Thưởng Login (Nhân vật Lv >= {self.login_lottery_min_level})")
        print(f"  • {self.quiz_start} - {self.quiz_end}: Chạy Vấn Đáp (Tại chỗ - Mọi cấp độ, kiểm tra theo khung giờ)")
        print(f"  • {self.auto_sell_time} hàng ngày: Tự động bán vật phẩm vào Shop ({', '.join(self.auto_sell_keywords)})")
        print(f"  • {self.lottery_start} - 23:59: Chạy Rút Thưởng May Mắn (Nhân vật Lv30+)")
        print(f"  • Định kỳ mỗi {report_str}: Gửi báo cáo trạng thái hệ thống qua Telegram")

    def stop(self):
        self.is_running = False

    def _scheduler_loop(self):
        while self.is_running:
            try:
                now = datetime.now(VN_TZ)
                now_str = now.strftime("%H:%M")
                today_str = now.strftime("%Y-%m-%d")

                # Cập nhật lại config nếu có thay đổi từ bên ngoài
                self.reload_config()

                # 1. Job Daily Re-login (Khung giờ từ relogin_time trở đi trong ngày)
                if now_str >= self.relogin_time and now_str <= "07:59" and self.last_triggered_relogin_date != today_str:
                    self.last_triggered_relogin_date = today_str
                    self._save_state()
                    print(f"\n[SCHEDULER {now_str}] ⏰ Bắt đầu Job Daily Re-login ngày mới ({today_str})...")
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"⏰ <b>[{now_str}] TỰ ĐỘNG RE-LOGIN NGÀY MỚI</b>\n"
                            f"Đang ngắt kết nối và đăng nhập lại toàn bộ tài khoản để nhận quà ngày mới..."
                        )
                    if self.context and hasattr(self.context, "daily_relogin"):
                        threading.Thread(target=self.context.daily_relogin, daemon=True, name="JobReloginThread").start()

                # 2. Job Login Lottery (Rút Thưởng Login lúc 07:01 - Nhân vật Lv >= 35)
                if now_str >= self.login_lottery_time and now_str <= "23:59" and self.last_triggered_login_lottery_date != today_str:
                    self.last_triggered_login_lottery_date = today_str
                    self._save_state()
                    print(f"\n[SCHEDULER {now_str}] 🎁 Bắt đầu Job Tự động Rút Thưởng Login (Lv >= {self.login_lottery_min_level})...")
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"🎁 <b>[{now_str}] BẮT ĐẦU TỰ ĐỘNG RÚT THƯỞNG LOGIN</b>\n"
                            f"Hệ thống đang tự động quay thưởng đăng nhập cho các nhân vật có cấp độ &gt;= {self.login_lottery_min_level}..."
                        )
                    if self.context and hasattr(self.context, "trigger_login_lottery_event"):
                        threading.Thread(target=self.context.trigger_login_lottery_event, daemon=True, name="JobLoginLotteryThread").start()

                # 3. Job Quiz Event (Vấn Đáp - Kiểm tra trong toàn bộ cửa sổ quiz_start -> quiz_end)
                if self.quiz_start <= now_str <= self.quiz_end and self.last_triggered_quiz_date != today_str:
                    self.last_triggered_quiz_date = today_str
                    self._save_state()
                    print(f"\n[SCHEDULER {now_str}] 🎓 Bắt đầu Job Vấn Đáp Tại Chỗ ({self.quiz_start} - {self.quiz_end})...")
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"🎓 <b>[{now_str}] BẮT ĐẦU SỰ KIỆN VẤN ĐÁP TỰ ĐỘNG</b>\n"
                            f"Hệ thống tự động kích hoạt Vấn Đáp cho toàn bộ tài khoản (mọi cấp độ)..."
                        )
                    if self.context and hasattr(self.context, "trigger_quiz_event"):
                        threading.Thread(target=self.context.trigger_quiz_event, daemon=True, name="JobQuizThread").start()

                # 3. Job Auto Sell (Bán vật phẩm vào Shop - auto_sell_time đến 23:59)
                if now_str >= self.auto_sell_time and now_str <= "23:59" and self.last_triggered_auto_sell_date != today_str:
                    self.last_triggered_auto_sell_date = today_str
                    self._save_state()
                    kw_display = ", ".join(self.auto_sell_keywords)
                    print(f"\n[SCHEDULER {now_str}] 💰 Bắt đầu Job Tự động bán vật phẩm theo lịch ({self.auto_sell_time} - [{kw_display}])...")
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"💰 <b>[{now_str}] TỰ ĐỘNG BÁN VẬT PHẨM THEO LỊCH TRÌNH</b>\n"
                            f"Hệ thống đang tự động bán các vật phẩm khớp từ khóa: <code>{kw_display}</code> cho toàn bộ tài khoản..."
                        )
                    if self.context and hasattr(self.context, "delete_items"):
                        self.context.delete_items(
                            self.auto_sell_keywords,
                            callback=lambda msg: self.telegram_bot.send_message(msg) if self.telegram_bot else None
                        )

                # 4. Job Lottery Event (Rút Thưởng May Mắn - Kiểm tra trong cửa sổ lottery_start -> 23:59)
                if now_str >= self.lottery_start and now_str <= "23:59" and self.last_triggered_lottery_date != today_str:
                    self.last_triggered_lottery_date = today_str
                    self._save_state()
                    print(f"\n[SCHEDULER {now_str}] 🎲 Bắt đầu Job Rút Thưởng May Mắn (Lv30+)...")
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"🎲 <b>[{now_str}] BẮT ĐẦU RÚT THƯỞNG MAY MẮN TỰ ĐỘNG</b>\n"
                            f"Đang tự động thực hiện rút thưởng cho các nhân vật đủ điều kiện..."
                        )
                    if self.context and hasattr(self.context, "trigger_lottery_event"):
                        threading.Thread(target=self.context.trigger_lottery_event, daemon=True, name="JobLotteryThread").start()

                # 4. Periodic Health Check (mỗi 5 phút)
                if time.time() - self.last_health_check_time >= 300:
                    self.last_health_check_time = time.time()
                    if self.context and hasattr(self.context, "health_check"):
                        self.context.health_check()

                # 5. Periodic Status Report qua Telegram (mỗi status_report_interval giây)
                if time.time() - self.last_status_report_time >= self.status_report_interval:
                    self.last_status_report_time = time.time()
                    if self.telegram_bot and self.context and hasattr(self.context, "get_formatted_status_message"):
                        mins_r = int(self.status_report_interval // 60)
                        title_interval = f"{mins_r // 60} GIỜ" if mins_r % 60 == 0 else f"{mins_r} PHÚT"
                        report_title = f"BÁO CÁO TRẠNG THÁI TỰ ĐỘNG (ĐỊNH KỲ {title_interval})"
                        status_msg = self.context.get_formatted_status_message(title=report_title)
                        self.telegram_bot.send_message(status_msg)

            except Exception as e:
                print(f"[SCHEDULER ERR] {e}")
                try:
                    from core.tele_notifier import TeleNotifier
                    TeleNotifier.get_instance().send_error_alert("Lỗi Lập Lịch (Scheduler 24/7)", str(e), source="PVTKScheduler", cooldown=60.0)
                except Exception:
                    pass

            time.sleep(3)
