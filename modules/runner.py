"""
PVTK Multi-Account Concurrent Runner Module
Handles parallel execution of multiple accounts with comprehensive exception handling.
"""

import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Dict, Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from core.account_cache import account_cache
from core.device_faker import DeviceFaker
from modules.auth import AuthManager
from modules.quiz_solver import QuizManager

print_lock = threading.Lock()

def safe_print(msg: str):
    with print_lock:
        print(msg)


from modules.base_runner import BaseAccountRunner

class QuizRunner(BaseAccountRunner):
    """
    Executes the automated workflow for 1 account with full exception containment:
    1. Check cache (skip if already completed today)
    2. Iterate through all characters sequentially
    3. Solves 20 quiz questions directly at current character location (No map teleport needed)
    4. Solve quiz questions / check time status
    5. Safely switch characters and update cache
    """
    
    def process_account(self, game_conn, task: Tuple, thread_tag: str, login_setting: int, dev: Dict[str, str], account_result: Dict[str, Any]):
        username = task[2]
        password = task[3]
        proxy = task[4] if len(task) >= 5 else None

        active_conn = game_conn

        # Step 1: Check cache (only skip if account is marked completed AND all characters are recorded)
        cached_chars = account_cache.get_account_quiz_characters(username)
        if account_cache.is_account_completed(username) and len(cached_chars) >= 3:
            self.log(f"{thread_tag} \033[92m[ĐÃ HOÀN THÀNH HÔM NAY - BỎ QUA]\033[0m Toàn bộ nhân vật đã hoàn thành vấn đáp. Bỏ qua.")
            account_result["completed"] = True
            account_result["error"] = "Đã hoàn thành hôm nay"
            for cname, cinfo in cached_chars.items():
                account_result["characters_results"].append({
                    "name": cname,
                    "level": cinfo.get("level", 0),
                    "answered": cinfo.get("total", 20),
                    "correct": cinfo.get("correct", 20),
                    "is_done": True,
                    "message": f"Đã hoàn thành lúc {cinfo.get('completed_at', '--')} (Bộ nhớ đệm)"
                })
            return

        try:
            characters = AuthManager.fetch_character_list(active_conn)
            if characters is None:
                raise Exception("Không thể lấy danh sách nhân vật từ máy chủ, yêu cầu đổi proxy và chạy lại")
                
            if not characters:
                account_result["error"] = "Chưa tạo nhân vật"
                self.log(f"{thread_tag} [CẢNH BÁO] Tài khoản chưa tạo nhân vật nào.")
                account_cache.mark_account_failed_auth(username, "Chưa tạo nhân vật")
                account_result["completed"] = True
                return

            completed_chars = 0
            for char_idx, char in enumerate(characters):
                char_tag = f"{thread_tag} [Nhân vật {char_idx + 1}/{len(characters)} - {char.name}]"

                # 1. KIỂM TRA BỘ NHỚ ĐỆM
                if account_cache.is_character_quiz_completed(username, char.name):
                    self.log(f"{char_tag} \033[92m[ĐÃ XONG HÔM NAY - BỎ QUA]\033[0m Nhân vật đã hoàn thành 20 câu vấn đáp trong ngày. Bỏ qua không cần vào game.")
                    completed_chars += 1
                    cached_chars = account_cache.get_account_quiz_characters(username)
                    cinfo = cached_chars.get(char.name, {})
                    account_result["characters_results"].append({
                        "name": char.name,
                        "level": char.level,
                        "answered": cinfo.get("total", 20),
                        "correct": cinfo.get("correct", 20),
                        "is_done": True,
                        "message": f"Đã hoàn thành lúc {cinfo.get('completed_at', '--')} (Bộ nhớ đệm)"
                    })
                    continue

                # 2. KIỂM TRA LỊCH SỬ HOÀN THÀNH HÔM NAY (Mọi cấp độ đều có thể vấn đáp)
                from modules.daily_history import DailyHistoryManager
                if DailyHistoryManager.is_quiz_done_today(username, char.name):
                    self.log(f"{char_tag} \033[92m[BỎ QUA - ĐÃ HOÀN THÀNH HÔM NAY]\033[0m Nhân vật '{char.name}' đã làm vấn đáp hôm nay.")
                    account_result["characters_results"].append({
                        "name": char.name,
                        "level": char.level,
                        "answered": 20,
                        "correct": 20,
                        "is_done": True,
                        "message": "Đã làm xong trước đó hôm nay"
                    })
                    continue

                # Enter Character (with auto-reconnect if desynced)
                enter_ok = False
                try:
                    enter_ok = AuthManager.enter_character(active_conn, char.id, login_setting, device_id=dev["android_id"])
                except Exception:
                    enter_ok = False

                if not enter_ok:
                    try:
                        active_conn.close()
                    except Exception:
                        pass
                    time.sleep(0.3)
                    
                    new_conn, new_login = AuthManager.reconnect_game_session(
                        settings.game_host, settings.game_port,
                        settings.master_host, settings.master_port,
                        username, password, proxy=proxy
                    )
                    if new_conn:
                        active_conn = new_conn
                        login_setting = new_login
                        try:
                            enter_ok = AuthManager.enter_character(active_conn, char.id, login_setting, device_id=dev["android_id"])
                        except Exception:
                            enter_ok = False

                if not enter_ok:
                    self.log(f"{char_tag} \033[91m[CHƯA VÀO ĐƯỢC]\033[0m Bắt buộc đổi proxy và thử lại toàn bộ tài khoản.")
                    raise Exception("Không vào được nhân vật, đổi proxy và chạy lại toàn bộ")

                self.log(f"{char_tag} Đã vào thế giới game thành công (Vị trí: {char.map_name}).")

                # Thực hiện Vấn Đáp trực tiếp tại vị trí hiện tại (Không dịch chuyển map)
                active_conn.drain_buffer(max_duration=0.5)

                # Solve Quiz Loop
                q_cnt = 0
                c_cnt = 0
                is_done = False
                msg_text = ""
                try:
                    is_done, q_cnt, c_cnt, msg_text = QuizManager.solve_character_quiz(active_conn, char_tag=char_tag, delay_sec=0.05)
                    if is_done or q_cnt >= 20:
                        self.log(f"{char_tag} -> \033[92m[KẾT QUẢ: {q_cnt}/20 CÂU]\033[0m Đúng: \033[93m{c_cnt}/{q_cnt}\033[0m | {msg_text}")
                        completed_chars += 1
                        account_cache.mark_character_quiz_completed(
                            username, char.name, correct_count=c_cnt,
                            total_answered=max(q_cnt, 20), level=char.level
                        )
                        try:
                            from modules.daily_history import DailyHistoryManager
                            DailyHistoryManager.save_quiz_done(
                                username=username,
                                char_name=char.name,
                                level=char.level,
                                questions=max(q_cnt, 20),
                                correct=c_cnt,
                                is_done=True,
                                message=msg_text
                            )
                        except Exception:
                            pass
                    elif q_cnt == 0 and "không nhận được" in msg_text.lower():
                        self.log(f"{char_tag} -> \033[91m[LỖI SERVER]\033[0m {msg_text} → Yêu cầu đổi proxy.")
                        raise Exception("Lỗi máy chủ không phản hồi, đổi proxy và chạy lại toàn bộ")
                    else:
                        self.log(f"{char_tag} -> \033[93m[KẾT QUẢ: {q_cnt}/20 CÂU]\033[0m Đúng: {c_cnt}/{q_cnt} | {msg_text}")
                except Exception as e:
                    self.log(f"{char_tag} [LỖI VẤN ĐÁP] Ngoại lệ khi giải câu hỏi: {e}")
                    raise Exception(f"Ngoại lệ khi giải câu hỏi: {e}")

                account_result["characters_results"].append({
                    "name": char.name,
                    "level": char.level,
                    "answered": q_cnt,
                    "correct": c_cnt,
                    "is_done": is_done,
                    "message": msg_text
                })

                # Leave Character
                try:
                    AuthManager.leave_character(active_conn)
                    self.log(f"{char_tag} Đã thoát nhân vật an toàn.")
                except Exception as e:
                    self.log(f"{char_tag} [CẢNH BÁO] Thoát nhân vật gặp lỗi: {e}")

            # Summary log per account
            summary_str = " | ".join([f"{c['name']}: {c.get('answered', 0)}/20 ({c.get('correct', 0)} đúng)" for c in account_result["characters_results"]])
            account_result["completed"] = True
            account_result["error"] = ""
            if completed_chars >= len(characters):
                account_cache.mark_account_completed(username, characters_done=completed_chars, total_chars=len(characters))
            else:
                account_cache.mark_account_in_progress(username, characters_done=completed_chars, total_chars=len(characters))
            self.log(f"{thread_tag} \033[92m[HOÀN THÀNH TOÀN BỘ]\033[0m Tiến độ: {summary_str}")

        finally:
            if active_conn and active_conn != game_conn:
                try:
                    active_conn.close()
                except Exception:
                    pass


def print_summary_report(total_accounts: int, elapsed_sec: float, all_results: List[Dict[str, Any]] = None):
    summary = account_cache.get_summary()

    safe_print("\n" + "=" * 90)
    safe_print("                    BẢNG TỔNG KẾT TIẾN TRÌNH SỰ KIỆN VẤN ĐÁP")
    safe_print("=" * 90)
    safe_print(f"{'TÀI KHOẢN':<15} | {'TÊN NHÂN VẬT':<15} | {'CẤP':<5} | {'TIẾN ĐỘ':<12} | {'ĐÚNG/ĐÃ LÀM':<14} | {'TRẠNG THÁI'}")
    safe_print("-" * 90)

    total_chars_count = 0
    total_answered_all = 0
    total_correct_all = 0

    if all_results:
        for acc in all_results:
            u = acc.get("username", "")
            err = acc.get("error", "")
            chars = acc.get("characters_results", [])
            if not chars and err:
                safe_print(f"{u:<15} | {'(Lỗi đăng nhập)':<15} | {'-':<5} | {'0/20':<12} | {'-':<14} | \033[91m{err}\033[0m")
                continue

            for c in chars:
                total_chars_count += 1
                cname = c.get("name", "")
                clevel = c.get("level", 0)
                ans = c.get("answered", 0)
                cor = c.get("correct", 0)
                msg = c.get("message", "")
                total_answered_all += ans
                total_correct_all += cor

                prog_str = f"{ans}/20"
                cor_str = f"{cor}/{ans} đúng" if ans > 0 else "-"
                if ans >= 20 or c.get("is_done"):
                    status_str = "\033[92mHOÀN THÀNH (20/20)\033[0m"
                    prog_colored = f"\033[92m{prog_str:<12}\033[0m"
                elif ans > 0:
                    status_str = f"\033[93mĐÃ LÀM {ans}/20\033[0m"
                    prog_colored = f"\033[93m{prog_str:<12}\033[0m"
                else:
                    status_str = msg if msg else "Chưa làm"
                    prog_colored = f"{prog_str:<12}"

                safe_print(f"{u:<15} | {cname:<15} | {clevel:<5} | {prog_colored} | {cor_str:<14} | {status_str}")

    safe_print("-" * 90)
    safe_print(f"-> Tổng nhân vật đã chạy: {total_chars_count} | Tổng số câu đã giải: \033[93m{total_answered_all}\033[0m câu (Đúng: \033[92m{total_correct_all}\033[0m câu)")
    safe_print(f"-> Tổng thời gian hoàn thành: {elapsed_sec:.1f} giây")
    safe_print("=" * 90)


def run_all_accounts(accounts: List[Tuple], max_workers: int = None):
    """
    Spawns concurrent worker threads to process accounts in parallel.
    Each account item can be (username, password) or (username, password, proxy).
    """
    total = len(accounts)
    safe_print("=" * 65)
    safe_print(f"BẮT ĐẦU CHẠY SONG SONG {total} TÀI KHOẢN")
    safe_print("=" * 65)

    start_time = time.time()
    
    account_tasks = [
        (i + 1, total, acc[0], acc[1], acc[2] if len(acc) >= 3 else None)
        for i, acc in enumerate(accounts)
    ]

    all_results = []
    try:
        runner = QuizRunner(account_tasks, thread_name="Tài khoản", max_retries=10, max_workers=max_workers)
        all_results = runner.run_all()
    except KeyboardInterrupt:
        safe_print("\n\033[93m[ĐÃ HỦY]\033[0m Người dùng nhấn Ctrl+C. Đang dừng toàn bộ luồng...")
        import os
        os._exit(0)

    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}
    final_results = [results_by_user.get(acc[0], {"username": acc[0], "error": "Chưa hoàn thành"}) for acc in accounts]
    elapsed = time.time() - start_time
    print_summary_report(total, elapsed, final_results)
