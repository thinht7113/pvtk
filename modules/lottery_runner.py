"""
PVTK Lucky Lottery Draw Automation Module (Rút Thưởng May Mắn)
Workflow:
1. Log in to Account & Enter Character (Slot 1 -> Slot 2 -> Slot 3)
2. Check and Teleport to 'Quảng Trường Tân Nguyệt' (Map ID 38)
3. Fetch Lottery Status (0x2B38) to get Lucky Target Number and free roll count
4. Auto-Roll (0x2B37):
   - First 3 free rolls (eventID = 1)
   - Subsequent rolls using Silver / Bạc (eventID = 3)
   - Stops when rolled number == Target Lucky Number or == 1000 or Server error / Out of silver
5. Output and persist the highest achieved roll number for each character & account
"""

import os
import sys
import json
import time
import threading
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from core.message import Message
from core.network import PVTKConnection
import core.protocol as proto
from modules.auth import AuthManager, CharacterInfo
from modules.teleport import MapTeleporter
from modules.base_runner import CharacterLoopRunner

PRINT_LOCK = threading.Lock()

def log(msg: str):
    with PRINT_LOCK:
        print(msg, flush=True)


class LotteryManager:
    @staticmethod
    def get_lottery_info(conn: PVTKConnection) -> Tuple[bool, Dict[str, Any], str]:
        """
        Sends Opcode 0x2B38 (CG_ACTOR_LOTTERY_DRAW_LIST) to request lottery status, lucky target, free count, etc.
        Returns: (success, data_dict, error_message)
        """
        for attempt in range(4):
            try:
                # Drain residual packets
                while conn.receive_message(timeout=0.03):
                    pass

                msg = Message(0x2B38)
                msg.putByte(5)   # Page size
                msg.putShort(1)  # Page num

                res = conn.send_and_wait(msg, expected_type=0x2B38, timeout=4.0)
                if not res or res.type != 0x2B38:
                    if attempt < 3:
                        # If first attempt didn't return, re-ping map teleport to ensure map transition is recognized
                        MapTeleporter.teleport_to_quang_truong(conn)
                        time.sleep(1.0)
                        continue
                    return False, {}, "Không nhận được phản hồi thông tin rút thưởng từ Server"

                all_size = res.getShort()
                if all_size < 0:
                    err = res.getString() if res.hasRemaining() else "Lỗi lấy thông tin rút thưởng"
                    return False, {}, err

                lucky_target = res.getInt()
                my_best_num = res.getInt()
                my_count = res.getByte()
                cost_la = res.getInt()
                cost_bac = res.getInt()
                free_count = res.getByte()
                all_count = res.getByte()
                reward_la = res.getInt()
                reward_bac = res.getInt()

                data = {
                    "lucky_target": lucky_target,
                    "my_best_num": my_best_num,
                    "my_count": my_count,
                    "cost_la": cost_la,
                    "cost_bac": cost_bac,
                    "free_count": free_count,
                    "all_count": all_count,
                    "reward_la": reward_la,
                    "reward_bac": reward_bac
                }
                return True, data, ""
            except Exception as e:
                if attempt < 3:
                    time.sleep(1.0)
                    continue
                return False, {}, f"Ngoại lệ lấy thông tin rút thưởng: {e}"
        return False, {}, "Không nhận được phản hồi thông tin rút thưởng từ Server"

    @staticmethod
    def roll_lottery(conn: PVTKConnection, event_id: int) -> Tuple[bool, int, int, int, str]:
        """
        Sends Opcode 0x2B37 to perform 1 lottery draw roll.
        event_id: 1 for free roll, 3 for silver roll.
        Returns: (success, server_best_num, drawn_num, my_count, message)
        """
        try:
            msg = Message(0x2B37)
            msg.putByte(event_id)

            res = conn.send_and_wait(msg, expected_type=0x2B37, timeout=6.0)
            if not res:
                return False, 0, 0, 0, "Không nhận được phản hồi kết quả quay"

            size = res.getShort()
            if size < 0:
                err = res.getString() if res.hasRemaining() else f"Lỗi rút thưởng (Mã: {size})"
                return False, 0, 0, 0, err

            result_code = res.getInt()     # Status code (>0 = OK)
            server_best_num = res.getInt() # Số cao nhất kỷ lục của nhân vật
            drawn_num = res.getInt()       # Số vừa rút được trong lượt này
            my_count = res.getByte()       # Tổng số lần đã quay
            remain_count = res.getByte()   # Số lần còn lại nhận thưởng bí mật

            if result_code <= 0:
                return False, 0, 0, 0, f"Server báo lỗi rút thưởng (code: {result_code})"

            return True, server_best_num, drawn_num, my_count, "Thành công"
        except Exception as e:
            return False, 0, 0, 0, f"Ngoại lệ khi quay: {e}"

    @classmethod
    def execute_lottery_for_character(
        cls, 
        conn: PVTKConnection, 
        char_tag: str, 
        delay_sec: float = 0.25,
        auto_escape_lag: bool = True
    ) -> Dict[str, Any]:
        """
        Executes the full lottery loop for 1 character until target reached or silver runs out.
        delay_sec: default 0.25s (250ms) between rolls.
        auto_escape_lag: if True, automatically calls escape_lag (0x290A) to return to reborn map.
        """
        result = {
            "lucky_target": 0,
            "best_number": 0,
            "rolls_done": 0,
            "hit_target": False,
            "hit_max_1000": False,
            "status": "UNKNOWN",
            "message": ""
        }

        def _do_escape_lag():
            if not auto_escape_lag or not conn or not getattr(conn, "is_connected", False):
                return
            try:
                log(f"{char_tag} 🔄 [THOÁT LAG] Đang thực hiện thoát lag sau khi rút thưởng...")
                esc_ok, new_map_id, gx, gy, esc_err = MapTeleporter.escape_lag(conn)
                err_lower = (esc_err or "").lower()
                if esc_ok:
                    log(f"{char_tag} 🔄 \033[92m[THOÁT LAG THÀNH CÔNG]\033[0m Đã về map hồi sinh an toàn.")
                elif any(kw in err_lower for kw in ["bản đồ này rồi", "ban do nay", "hồi sinh", "hoi sinh", "bản đồ này", "đã ở", "da o"]):
                    log(f"{char_tag} 🔄 [THOÁT LAG] Đã ở sẵn map hồi sinh!")
                else:
                    log(f"{char_tag} 🔄 [THOÁT LAG] {esc_err or 'Đã gửi lệnh thoát lag'}")
                time.sleep(0.8)
                while conn.receive_message(timeout=0.05):
                    pass
            except Exception as e_esc:
                log(f"{char_tag} ⚠️ [LỖI THOÁT LAG] {e_esc}")

        # 1. Get initial status
        ok, info, err = cls.get_lottery_info(conn)
        if not ok:
            # If not in shop / not in map, teleport to Quảng Trường Tân Nguyệt and retry
            MapTeleporter.teleport_to_quang_truong(conn)
            time.sleep(1.2)
            while conn.receive_message(timeout=0.1):
                pass
            ok, info, err = cls.get_lottery_info(conn)
            if not ok:
                result["status"] = "INFO_FAILED"
                result["message"] = err
                log(f"{char_tag} [THÔNG BÁO] {err}")
                _do_escape_lag()
                return result

        server_lucky_target = info["lucky_target"]
        my_best = info["my_best_num"]
        free_cnt = info["free_count"]
        
        # Priority target: Use configured lucky number (e.g. 321) if set > 0, else use server's target
        target_lucky = settings.lottery_target_lucky_number if settings.lottery_target_lucky_number > 0 else server_lucky_target
        max_stop = settings.lottery_max_stop_number

        result["lucky_target"] = target_lucky
        result["best_number"] = my_best

        log(f"{char_tag} [THÔNG TIN RÚT THƯỞNG] Mục tiêu may mắn: \033[93m{target_lucky}\033[0m (Server: {server_lucky_target}) | Số cao nhất hiện tại: \033[92m{my_best}\033[0m | Miễn phí: {free_cnt} lần")

        # If already hit target or max_stop
        if my_best == target_lucky and target_lucky > 0:
            result["hit_target"] = True
            result["status"] = "ALREADY_HIT_TARGET"
            result["message"] = f"Nhân vật đã đạt đúng số may mắn {target_lucky}!"
            log(f"{char_tag} \033[92m[ĐÃ ĐẠT]\033[0m Đã có số may mắn {target_lucky}.")
            _do_escape_lag()
            return result

        if my_best >= max_stop:
            result["hit_max_1000"] = True
            result["status"] = "ALREADY_HIT_MAX"
            result["message"] = f"Nhân vật đã đạt điểm tối đa {max_stop}!"
            log(f"{char_tag} \033[92m[ĐÃ ĐẠT]\033[0m Đã có điểm tối đa {max_stop}.")
            _do_escape_lag()
            return result

        # 2. Loop rolling
        rolls_count = 0
        while True:
            # Determine event_id: 1 for free roll if available, else 3 for silver
            event_id = 1 if free_cnt > 0 else 3
            roll_type_name = "Miễn phí" if event_id == 1 else "Bạc"

            ok_roll, server_best_from_roll, drawn_num, server_count, msg_roll = cls.roll_lottery(conn, event_id=event_id)
            if not ok_roll:
                result["status"] = "STOP_ROLL"
                result["message"] = msg_roll
                log(f"{char_tag} [DỪNG QUAY] {msg_roll}")
                break

            rolls_count += 1
            if event_id == 1 and free_cnt > 0:
                free_cnt -= 1

            old_best = my_best
            # Update personal highest score
            if drawn_num > my_best:
                my_best = drawn_num
            if server_best_from_roll > my_best:
                my_best = server_best_from_roll

            is_new_record = (drawn_num > old_best and old_best > 0) or (old_best == 0 and drawn_num > 0)

            result["best_number"] = my_best
            result["rolls_done"] = rolls_count

            record_tag = " \033[93m★ KỶ LỤC MỚI!\033[0m" if is_new_record else ""
            log(f"{char_tag} -> Lần {rolls_count} ({roll_type_name}): Số vừa rút: \033[96m{drawn_num}\033[0m | Số cao nhất: \033[92m{my_best}\033[0m{record_tag}")

            # Check if hit exact target lucky number (e.g. 321)
            if drawn_num == target_lucky and target_lucky > 0:
                result["hit_target"] = True
                result["status"] = "HIT_LUCKY_TARGET"
                result["message"] = f"🎉 TRÚNG SỐ MAY MẮN: {target_lucky}!"
                log(f"{char_tag} \033[92m[TRÚNG SỐ MAY MẮN]\033[0m Chúc mừng! Đã quay trúng số may mắn: \033[93m{target_lucky}\033[0m!")
                break

            # Check if hit max stop number (e.g. 1000)
            if my_best >= max_stop:
                result["hit_max_1000"] = True
                result["status"] = "HIT_MAX_1000"
                result["message"] = f"🌟 ĐẠT ĐIỂM SỐ TỐI ĐA: {my_best} (>= {max_stop})!"
                log(f"{char_tag} \033[92m[ĐẠT ĐIỂM TỐI ĐA]\033[0m Đã quay đạt mức tối đa \033[93m{my_best}\033[0m. Dừng quay!")
                break

            # Delay between rolls: 250ms (0.25s)
            time.sleep(delay_sec)

        _do_escape_lag()
        return result


class LotteryRunner(CharacterLoopRunner):
    def should_process_character(self, char, task: Tuple) -> bool:
        if char.level < 30:
            self.log(f"  \033[90m[BỎ QUA - CẤP {char.level} < 30]\033[0m Nhân vật '{char.name}' chưa đủ cấp 30 để rút thưởng.")
            return False
        username = task[2] if len(task) >= 3 else ""
        from modules.daily_history import DailyHistoryManager
        if DailyHistoryManager.is_lottery_done_today(username, char.name):
            rec = DailyHistoryManager.get_lottery_record(username, char.name)
            best_num = rec.get("best_number", 0) if rec else 0
            self.log(f"  \033[90m[BỎ QUA - ĐÃ HOÀN THÀNH HÔM NAY]\033[0m Nhân vật '{char.name}' đã rút thưởng hôm nay (Điểm: {best_num}). Bỏ qua tránh lặp lại.")
            return False
        return True

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        # Check and Teleport to Quảng Trường Tân Nguyệt
        if not ("quảng trường" in char.map_name.lower() or "quang truong" in char.map_name.lower()):
            self.log(f"{char_tag} [DỊCH CHUYỂN] Đang chuyển từ '{char.map_name}' đến '{settings.lottery_target_map_name}'...")
            MapTeleporter.teleport_to_quang_truong(game_conn)
            char.map_name = "Quảng Trường Tân Nguyệt"
            time.sleep(1.2)
            while game_conn.receive_message(timeout=0.1):
                pass

        # Run Lottery Draw
        char_res = LotteryManager.execute_lottery_for_character(game_conn, char_tag, delay_sec=settings.question_delay_sec, auto_escape_lag=True)
        char_res["name"] = char.name
        char_res["level"] = char.level

        if char_res.get("status") == "INFO_FAILED":
            self.log(f"{char_tag} \033[93m[LỖI THÔNG TIN]\033[0m Sẽ kết nối lại phiên và chạy lại nhân vật...")
            raise Exception("Lỗi lấy thông tin Lottery, cần thử lại.")
        else:
            char.map_name = "Bản đồ hồi sinh"
            account_result["characters_results"].append(char_res)
            # Lưu lịch sử hoàn thành hôm nay
            username = task[2] if len(task) >= 3 else ""
            try:
                from modules.daily_history import DailyHistoryManager
                DailyHistoryManager.save_lottery_done(
                    username=username,
                    char_name=char.name,
                    level=char.level,
                    best_number=char_res.get("best_number", 0),
                    rolls_done=char_res.get("rolls_done", 0),
                    hit_target=char_res.get("hit_target", False),
                    hit_max_1000=char_res.get("hit_max_1000", False),
                    status=char_res.get("status", "COMPLETED"),
                    message=char_res.get("message", "")
                )
            except Exception:
                pass


def save_lottery_results(results: List[Dict[str, Any]]):
    import os, json
    from datetime import datetime, timezone, timedelta
    storage_path = os.path.join(settings.storage_dir, "lottery_results.json")
    vn_tz = timezone(timedelta(hours=7))
    today_str = datetime.now(vn_tz).strftime("%Y-%m-%d")
    existing_data = {}
    if os.path.exists(storage_path):
        try:
            with open(storage_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = {}

    from core.storage_utils import atomic_write_json, prune_old_date_keys
    existing_data[today_str] = results
    prune_old_date_keys(existing_data, keep_days=14)
    try:
        atomic_write_json(storage_path, existing_data, indent=2)
    except Exception:
        pass


def run_all_lottery_accounts(accounts: List[Tuple], max_workers: int = None):
    total = len(accounts)
    print("\n" + "=" * 65)
    print(f"BẮT ĐẦU CHẠY SONG SONG {total} TÀI KHOẢN")
    print("=" * 65)

    start_time = time.time()
    
    account_tasks = [
        (i + 1, total, acc[0], acc[1], acc[2] if len(acc) >= 3 else None)
        for i, acc in enumerate(accounts)
    ]

    try:
        runner = LotteryRunner(account_tasks, thread_name="Tài khoản", max_retries=10, max_workers=max_workers)
        all_results = runner.run_all()
    except KeyboardInterrupt:
        print("\n\033[93m[ĐÃ HỦY]\033[0m Người dùng nhấn Ctrl+C. Đang dừng toàn bộ luồng...")
        import os
        os._exit(0)

    elapsed = round(time.time() - start_time, 1)
    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}
    final_results = [results_by_user.get(acc[0], {"username": acc[0], "error": "Chưa hoàn thành"}) for acc in accounts]

    # Save to storage
    save_lottery_results(final_results)

    # Filter for Summary Table:
    # 1. Accounts that hit target lucky number (321)
    # 2. Accounts that achieved the highest score among all characters
    target_lucky = settings.lottery_target_lucky_number
    lucky_hits = []
    all_char_scores = []

    for acc in final_results:
        username = acc["username"]
        for c in acc.get("characters_results", []):
            item = {
                "username": username,
                "name": c.get("name", ""),
                "level": c.get("level", 0),
                "best_number": c.get("best_number", 0),
                "rolls_done": c.get("rolls_done", 0),
                "status": c.get("status", ""),
                "message": c.get("message", "")
            }
            if c.get("hit_target") or c.get("best_number") == target_lucky:
                lucky_hits.append(item)
            all_char_scores.append(item)

    # Find highest score
    max_score = 0
    top_scorers = []
    if all_char_scores:
        max_score = max(x["best_number"] for x in all_char_scores)
        if max_score > 0:
            top_scorers = [x for x in all_char_scores if x["best_number"] == max_score]

    # Print Filtered Output Table
    print("\n" + "=" * 80)
    print("            BẢNG TỔNG KẾT: TÀI KHOẢN TRÚNG SỐ MAY MẮN & ĐIỂM CAO NHẤT")
    print("=" * 80)
    
    # 1. Lucky hits section
    print(f"\n🎯 [1] DANH SÁCH TRÚNG SỐ MAY MẮN (SỐ {target_lucky}):")
    if lucky_hits:
        print(f"{'TÀI KHOẢN':<15} | {'TÊN NHÂN VẬT':<15} | {'SỐ MAY MẮN':<12} | {'SỐ LẦN QUAY':<12} | {'KẾT QUẢ'}")
        print("-" * 80)
        for h in lucky_hits:
            print(f"{h['username']:<15} | {h['name']:<15} | \033[93m{h['best_number']:<12}\033[0m | {h['rolls_done']:<12} | \033[92m{h['message']}\033[0m")
    else:
        print(f"   (Không có nhân vật nào quay trúng số may mắn hôm nay)")

    # 2. Top scorers section
    print(f"\n🏆 [2] DANH SÁCH ĐẠT ĐIỂM SỐ CAO NHẤT TRONG TẤT CẢ TÀI KHOẢN (TOP: {max_score}):")
    if top_scorers:
        print(f"{'TÀI KHOẢN':<15} | {'TÊN NHÂN VẬT':<15} | {'ĐIỂM CAO NHẤT':<14} | {'SỐ LẦN QUAY':<12} | {'TRẠNG THÁI'}")
        print("-" * 80)
        for ts in top_scorers:
            print(f"{ts['username']:<15} | {ts['name']:<15} | \033[92m{ts['best_number']:<14}\033[0m | {ts['rolls_done']:<12} | {ts['status']}")
    else:
        print("   (Chưa có dữ liệu điểm số lớn hơn 0)")

    print("\n" + "=" * 80)
    print(f"-> Đã quét xong: {len(all_char_scores)} nhân vật | Thời gian: {elapsed}s | Chi tiết: storage/lottery_results.json")
    print("=" * 80)
