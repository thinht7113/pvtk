import time
import copy
from typing import List, Tuple, Dict, Any, Optional

from core.network import PVTKConnection, Message
from modules.auth import AuthManager
from modules.mail_sender import MailManager
from core.config import settings
from modules.base_runner import CharacterLoopRunner

def safe_print(*args, **kwargs):
    import sys
    try:
        print(*args, **kwargs)
        sys.stdout.flush()
    except Exception:
        pass


class SilverSender:
    """Manages getting silver and sending bulk silver mails."""

    @staticmethod
    def get_character_silver(conn: PVTKConnection) -> int:
        """Requests player money using 0x2EE3 (CG_BAG_CLEAN / REFRESH)."""
        import time
        while conn.receive_message(timeout=0.01):
            pass

        msg = Message(0x2EE3)
        msg.putInt(0x02) # Block 2: Money
        conn.send_message(msg)

        start_time = time.time()
        while time.time() - start_time < 4.5:
            res = conn.receive_message(timeout=1.0)
            if not res or res.type != 0x2EE3:
                continue

            try:
                code = res.getByte()
                if code != 0:
                    continue

                new_map = res.getInt()
                if new_map >= 20000:
                    res.getShort()
                gx = res.getByte()
                gy = res.getByte()
                
                block_flag = res.getInt()
                char_id = res.getInt()
                name = res.getString()
                
                # Bỏ qua 76 byte chỉ số nhân vật để lấy đúng vị trí tiền tệ
                res.offset += 76
                
                gold = res.getInt()
                leaf = res.getInt()
                silver = res.getInt()
                
                return silver
            except Exception as e:
                pass

        return 0

    @staticmethod
    def send_silver_mail(
        conn: PVTKConnection,
        target_str: str,
        send_silver: int,
        content: str = "Bạc Tool"
    ) -> Tuple[bool, str]:
        return MailManager.send_cod_mail(
            conn=conn,
            receiver_target=target_str,
            receive_silver=0,
            send_silver=send_silver,
            content=content
        )


def run_test_silver_mail(
    accounts: List[Tuple],
    target_str: str,
    max_workers: int = 15,
    stop_event: Optional[threading.Event] = None
) -> bool:
    """
    Quét đa luồng song song qua các tài khoản để tìm nhân vật đầu tiên có đủ Bạc.
    Gửi 1 Bạc test đến target_str.
    Trả về True nếu gửi thành công, False nếu không tìm thấy hoặc bị hủy.
    """
    from modules.free_proxy import FreeProxyManager
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if stop_event is None:
        stop_event = threading.Event()

    found_lock = threading.Lock()
    test_sent = threading.Event()

    def _check_acc(acc):
        if stop_event.is_set() or test_sent.is_set():
            return False

        username = acc[0]
        password = acc[1]
        acc_proxy = acc[4] if len(acc) >= 5 and acc[4] else None
        proxies_to_try = [acc_proxy, None] if acc_proxy else [None, FreeProxyManager.get_random_proxy()]

        for proxy in proxies_to_try:
            if stop_event.is_set() or test_sent.is_set():
                return False
            game_conn = None
            try:
                login_data, err = AuthManager.master_login(settings.master_host, settings.master_port, username, password, proxy=proxy)
                if not login_data:
                    continue
                ukey, session_id, login_setting = login_data
                game_conn = AuthManager.connect_game_server(settings.game_host, settings.game_port, ukey, session_id, proxy=proxy)
                if not game_conn:
                    continue
                chars = AuthManager.fetch_character_list(game_conn)
                if not chars:
                    continue

                for char in chars:
                    if stop_event.is_set() or test_sent.is_set():
                        break
                    if str(char.id) == target_str or char.name == target_str:
                        continue

                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(game_conn, char.id, login_setting)
                    except Exception:
                        pass
                    if not enter_ok:
                        continue

                    silver = SilverSender.get_character_silver(game_conn)
                    if silver >= 12:
                        with found_lock:
                            if test_sent.is_set():
                                try:
                                    AuthManager.leave_character(game_conn)
                                except Exception:
                                    pass
                                return False

                            test_send = 1
                            safe_print(f"  \033[92m[TÌM THẤY]\033[0m Nhân vật '{char.name}' (Acc: {username}) đang có {silver:,} Bạc. Đang gửi 1 Bạc test...")
                            ok, msg_res = SilverSender.send_silver_mail(game_conn, target_str, test_send, content="Thư Test Bạc")

                            try:
                                AuthManager.leave_character(game_conn)
                            except Exception:
                                pass

                            if ok:
                                test_sent.set()
                                safe_print(f"  \033[92m[GỬI TEST THÀNH CÔNG]\033[0m Đã gửi 1 Bạc test đến '{target_str}'. Vui lòng kiểm tra!")
                                return True
                            else:
                                safe_print(f"  \033[91m[GỬI TEST THẤT BẠI]\033[0m Lỗi: {msg_res}")
                                return False

                    try:
                        AuthManager.leave_character(game_conn)
                    except Exception:
                        pass

                break # Acc login ok
            except Exception:
                continue
            finally:
                if game_conn:
                    try:
                        game_conn.close()
                    except Exception:
                        pass
        return False

    eff_workers = min(len(accounts), max(3, max_workers))
    with ThreadPoolExecutor(max_workers=eff_workers) as executor:
        futures = [executor.submit(_check_acc, acc) for acc in accounts]
        for f in as_completed(futures):
            if test_sent.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                return True
            if stop_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                return False

    return test_sent.is_set()


import threading

class SilverSenderRunner(CharacterLoopRunner):
    def __init__(self, accounts: List[Tuple], thread_name: str = "Tài khoản", max_workers: int = None, max_mails: Optional[int] = None, stop_event: Optional[threading.Event] = None):
        super().__init__(accounts, thread_name=thread_name, max_workers=max_workers, stop_event=stop_event)
        self.max_mails = max_mails
        self.quota_reached = False
        self.quota_lock = threading.Lock()
        self.sent_mails = 0
        self.sent_silver = 0
        self.reserved_mails = 0

    def acquire_mail_permit(self) -> bool:
        if self.is_stopped():
            return False
        with self.quota_lock:
            if self.max_mails is not None and (self.sent_mails + self.reserved_mails) >= self.max_mails:
                return False
            self.reserved_mails += 1
            return True

    def release_mail_permit(self, success: bool = False, count: int = 1, silver: int = 0):
        with self.quota_lock:
            self.reserved_mails = max(0, self.reserved_mails - 1)
            if success:
                self.sent_mails += count
                self.sent_silver += silver
                if self.max_mails is not None and self.sent_mails >= self.max_mails:
                    self.quota_reached = True
                    self.stop_event.set()

    def should_process_character(self, char, task: Tuple) -> bool:
        if self.is_stopped():
            return False
        target_str = task[5]
        if str(char.id) == target_str or char.name == target_str:
            pass
        return True

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        if self.is_stopped():
            return

        target_str = task[5]
        
        if str(char.id) == target_str or char.name == target_str:
            account_result["characters_results"].append({
                "name": char.name,
                "id": char.id,
                "sent_silver": 0,
                "status": "SKIP",
                "message": "Trùng ID người nhận"
            })
            return

        silver = SilverSender.get_character_silver(game_conn)
        
        if silver <= 10:
            account_result["characters_results"].append({
                "name": char.name,
                "id": char.id,
                "sent_silver": 0,
                "status": "EMPTY",
                "message": f"Chỉ có {silver} Bạc (Không đủ phí gửi)"
            })
            self.log(f"{char_tag} \033[90m[BỎ QUA]\033[0m Có {silver} Bạc (Không đủ phí gửi thư).")
            return

        # Kiểm tra quota số lượng thư tối đa
        if not self.acquire_mail_permit():
            self.log(f"{char_tag} \033[93m[DỪNG - ĐÃ ĐẠT GIỚI HẠN]\033[0m Đã gửi đủ số thư yêu cầu ({self.max_mails} thư). Bỏ qua gửi thêm.")
            account_result["characters_results"].append({
                "name": char.name,
                "id": char.id,
                "sent_silver": 0,
                "status": "LIMIT_REACHED",
                "message": f"Đã đạt giới hạn {self.max_mails} thư"
            })
            return

        send_amount = silver - 10 
        ok, msg_res = SilverSender.send_silver_mail(game_conn, target_str, send_amount, content="Bạc Tool")
        
        if ok:
            status_str = "SUCCESS"
            self.release_mail_permit(success=True, count=1, silver=send_amount)
            self.log(f"{char_tag} \033[92m[GỬI THÀNH CÔNG]\033[0m Đã gửi toàn bộ \033[93m{send_amount:,} Bạc\033[0m cho '{target_str}'")
        else:
            status_str = "FAILED"
            if "Không đủ tiền" in msg_res and silver > 110:
                send_amount = silver - 110
                ok, msg_res2 = SilverSender.send_silver_mail(game_conn, target_str, send_amount, content="Bạc Tool")
                if ok:
                    status_str = "SUCCESS"
                    msg_res = msg_res2
                    self.release_mail_permit(success=True, count=1, silver=send_amount)
                    self.log(f"{char_tag} \033[92m[GỬI THÀNH CÔNG]\033[0m Đã gửi \033[93m{send_amount:,} Bạc\033[0m cho '{target_str}'")
                else:
                    self.release_mail_permit(success=False)
                    self.log(f"{char_tag} \033[91m[GỬI THẤT BẠI]\033[0m {msg_res2}")
                    msg_res = msg_res2
            else:
                self.release_mail_permit(success=False)
                self.log(f"{char_tag} \033[91m[GỬI THẤT BẠI]\033[0m {msg_res}")
        
        account_result["characters_results"].append({
            "name": char.name,
            "id": char.id,
            "sent_silver": send_amount if status_str == "SUCCESS" else 0,
            "status": status_str,
            "message": msg_res
        })


def run_all_silver_sender(
    accounts: List[Tuple],
    target: str,
    max_workers: int = None,
    max_mails: Optional[int] = None,
    stop_event: Optional[threading.Event] = None
) -> Dict[str, Any]:
    receiver_target = target
    total_accs = len(accounts)
    
    # Điều chỉnh số luồng hợp lý nếu có max_mails
    effective_workers = max_workers
    if max_mails is not None and max_mails > 0:
        suggested_workers = min(total_accs, max(3, max_mails + 3))
        if effective_workers is None or effective_workers > suggested_workers:
            effective_workers = suggested_workers
    elif effective_workers is None:
        effective_workers = total_accs

    limit_info = f"{max_mails} thư" if max_mails is not None and max_mails > 0 else "Không giới hạn (Toàn bộ)"

    print("\n" + "=" * 90)
    print(f"  BẮT ĐẦU QUÉT & GỬI BẠC CHO {total_accs} TÀI KHOẢN")
    print(f"  NGƯỜI NHẬN: '{receiver_target}' | GIỚI HẠN: {limit_info} | ĐA LUỒNG: {effective_workers}")
    print("=" * 90 + "\n")

    start_time = time.time()

    account_tuples = []
    for idx, acc in enumerate(accounts, 1):
        username = acc[0]
        password = acc[1]
        proxy = acc[2] if len(acc) >= 3 else None
        account_tuples.append((idx, total_accs, username, password, proxy, receiver_target))

    runner = SilverSenderRunner(
        account_tuples,
        thread_name="Tài khoản",
        max_workers=effective_workers,
        max_mails=max_mails,
        stop_event=stop_event
    )
    all_results = runner.run_all()

    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}

    print("\n" + "=" * 90)
    print("                              BẢNG THỐNG KÊ GỬI BẠC")
    print("=" * 90)
    print(f"{'Tài khoản':<15} | {'Nhân vật':<15} | {'Bạc Đã Gửi':<15} | {'Trạng thái'}")
    print("-" * 90)

    total_silver = 0
    total_chars = 0
    total_success = 0

    for item in account_tuples:
        u = item[2]
        res = results_by_user.get(u)
        if not res:
            print(f"{u:<15} | {'N/A':<15} | {'0':<15} | \033[91mLỗi / Bỏ qua\033[0m")
            continue
        
        c_results = res.get("characters_results", [])
        if not c_results:
            print(f"{u:<15} | {'N/A':<15} | {'0':<15} | \033[93mKhông có NV\033[0m")
            continue
            
        for c_res in c_results:
            cname = c_res.get("name", "")
            sent_s = c_res.get("sent_silver", 0)
            st = c_res.get("status", "")
            msg = c_res.get("message", "")
            
            total_chars += 1
            if st == "SUCCESS":
                status_str = f"\033[92mThành công\033[0m"
                total_success += 1
                total_silver += sent_s
            elif st == "EMPTY":
                status_str = f"\033[90m{msg}\033[0m"
            elif st == "SKIP":
                status_str = f"\033[93m{msg}\033[0m"
            elif st == "LIMIT_REACHED":
                status_str = f"\033[93m{msg}\033[0m"
            else:
                status_str = f"\033[91mLỗi: {msg}\033[0m"
                
            print(f"{u:<15} | {cname:<15} | {sent_s:<15,} | {status_str}")

    final_mails = max(total_success, runner.sent_mails)
    final_silver = max(total_silver, runner.sent_silver)

    elapsed = round(time.time() - start_time, 1)
    print("-" * 90)
    print(f"Tổng tài khoản: \033[93m{total_accs}\033[0m")
    print(f"Tổng nhân vật đã quét: \033[96m{total_chars}\033[0m")
    print(f"Tổng số thư Bạc đã gửi: \033[92m{final_mails}\033[0m (Giới hạn: {limit_info})")
    print(f"Tổng số Bạc đã gửi: \033[93m{final_silver:,}\033[0m Bạc")
    print(f"Thời gian chạy: \033[96m{elapsed}\033[0m giây")
    print("=" * 90)

    quota_reached = getattr(runner, "quota_reached", False) or (max_mails is not None and final_mails >= max_mails)
    user_cancelled = runner.is_user_cancelled() and not quota_reached

    return {
        "total_silver": final_silver,
        "total_success": final_mails,
        "total_mails": final_mails,
        "total_chars": total_chars,
        "elapsed": elapsed,
        "quota_reached": quota_reached,
        "cancelled": user_cancelled
    }
