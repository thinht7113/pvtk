import os
import sys
import time
import socket
from typing import Tuple, Dict, Any, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed, wait as futures_wait, FIRST_COMPLETED
from core.config import settings
from modules.auth import AuthManager
from core.device_faker import DeviceFaker
from modules.free_proxy import FreeProxyManager

import threading
print_lock = threading.Lock()

def safe_print(msg: str):
    with print_lock:
        print(msg, flush=True)

class BaseAccountRunner:
    """
    Core engine for handling concurrent account execution, proxy rotation,
    Master Login, and Game Server handshake.
    Subclasses should override `process_account()` to implement feature logic.
    """
    def __init__(self, accounts: List[Tuple], thread_name: str = "Tài khoản", max_retries: int = 10, proxy_rotation: bool = True, max_workers: int = None, stop_event: Optional[threading.Event] = None):
        self.thread_name = thread_name
        self.max_retries = max_retries
        self.proxy_rotation = proxy_rotation
        self.max_workers = max_workers
        self.user_stop_event = stop_event
        self.stop_event = threading.Event()
        self.active_workers_count = 0
        self.active_workers_lock = threading.Lock()

        self.accounts = []
        total = len(accounts)
        for i, acc in enumerate(accounts):
            if len(acc) > 0 and isinstance(acc[0], int):
                self.accounts.append(acc)
            else:
                self.accounts.append((i + 1, total) + tuple(acc))

    def is_stopped(self) -> bool:
        if self.user_stop_event and self.user_stop_event.is_set():
            if not self.stop_event.is_set():
                self.stop_event.set()
            return True
        return self.stop_event.is_set()

    def is_user_cancelled(self) -> bool:
        return bool(self.user_stop_event and self.user_stop_event.is_set())

    def _wait_interruptible(self, timeout: float) -> bool:
        if self.is_stopped():
            return True
        if not self.user_stop_event or self.user_stop_event is self.stop_event:
            return self.stop_event.wait(timeout=timeout)
        deadline = time.time() + timeout
        while True:
            if self.is_stopped():
                return True
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            if self.stop_event.wait(timeout=min(0.25, remaining)):
                return True

    def log(self, message: str):
        safe_print(message)


    def process_account(self, game_conn, task: Tuple, thread_tag: str, login_setting: int, dev: Dict[str, str], account_result: Dict[str, Any]):
        """
        Abstract method. Subclasses MUST override this to provide specific logic.
        (e.g., fetch characters, answer quiz, claim mail)
        """
        raise NotImplementedError("Subclasses must implement process_account")

    def run_all(self) -> List[Dict[str, Any]]:
        total = len(self.accounts)
        all_results = []
        seen_users = set()

        def _add_result(r):
            if not r or not isinstance(r, dict):
                return
            u = r.get("username")
            if u and u in seen_users:
                return
            if u:
                seen_users.add(u)
            all_results.append(r)

        workers = self.max_workers if self.max_workers else (total if total > 0 else 1)
        executor = ThreadPoolExecutor(max_workers=workers)
        
        futures = {}
        for task in self.accounts:
            if self.is_stopped():
                break
            futures[executor.submit(self._account_retry_wrapper, task, total)] = task
            proxy = task[2] if len(task) > 2 else None
            stagger = 0.35 if not proxy else 0.1
            if self._wait_interruptible(stagger):
                break

        interrupted = False
        pending = set(futures.keys())
        try:
            while pending:
                if self.is_stopped():
                    break
                done, pending = futures_wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        res = future.result()
                        if res:
                            _add_result(res)
                    except Exception as e:
                        import traceback
                        self.log(f"Lỗi không xác định ở vòng lặp ngoài: {e}")
                        traceback.print_exc()

            if self.is_stopped():
                interrupted = True
                if self.is_user_cancelled():
                    self.log("\n\033[93m[ĐÃ HỦY TIẾN TRÌNH]\033[0m Nhận lệnh dừng khẩn cấp (/huy). Đang đóng các tác vụ dở dang...")
                else:
                    self.log("\n\033[92m[HOÀN TẤT MỤC TIÊU]\033[0m Đã đạt giới hạn yêu cầu. Đang đóng các tác vụ dở dang...")
                for f in pending:
                    f.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                wait_deadline = time.time() + 2.5
                while self.get_active_workers_count() > 0 and time.time() < wait_deadline:
                    time.sleep(0.1)
                for f in futures:
                    if f.done() and not f.cancelled():
                        try:
                            r = f.result()
                            if r:
                                _add_result(r)
                        except Exception:
                            pass
        except KeyboardInterrupt:
            interrupted = True
            self.log("\n\033[93m[ĐÃ HỦY]\033[0m Nhận lệnh dừng (Ctrl+C). Đang đóng kết nối an toàn để tránh kẹt acc...")
            self.stop_event.set()
            for f in pending:
                f.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return all_results
        finally:
            if not interrupted and not self.is_stopped():
                try:
                    executor.shutdown(wait=True)
                except Exception:
                    pass
            else:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass

        return all_results

    def get_active_workers_count(self) -> int:
        with self.active_workers_lock:
            return self.active_workers_count

    def _account_retry_wrapper(self, task: Tuple, total: int) -> Dict[str, Any]:
        with self.active_workers_lock:
            self.active_workers_count += 1
        try:
            retries = 0
            last_res = None
            
            accumulated_chars: Dict[str, Dict[str, Any]] = {}
            while retries < self.max_retries and not self.is_stopped():
                done_names = set(accumulated_chars.keys())
                res = self._process_single_account_internal(task, total, completed_char_names=done_names)
                last_res = res
                
                # Gom gộp kết quả nhân vật qua các lần thử (tránh mất kết quả từng phần khi đứt mạng giữa chừng)
                if res and "characters_results" in res:
                    for cr in res.get("characters_results", []):
                        cname = cr.get("name") or cr.get("char_name") or ""
                        if cname:
                            if cname not in accumulated_chars:
                                accumulated_chars[cname] = dict(cr)
                            else:
                                existing = accumulated_chars[cname]
                                existing["mails_claimed"] = existing.get("mails_claimed", 0) + cr.get("mails_claimed", 0)
                                existing["total_silver_gained"] = existing.get("total_silver_gained", 0) + cr.get("total_silver_gained", 0)
                                if cr.get("latest_silver"):
                                    existing["latest_silver"] = cr["latest_silver"]
                                for iname, idata in cr.get("items_gained", {}).items():
                                    if iname not in existing.setdefault("items_gained", {}):
                                        existing["items_gained"][iname] = dict(idata)
                                    else:
                                        existing["items_gained"][iname]["qty"] = existing["items_gained"][iname].get("qty", 0) + (idata.get("qty", 1) if isinstance(idata, dict) else idata)
                                if "rewards_summary" in cr:
                                    existing.setdefault("rewards_summary", []).extend(cr["rewards_summary"])

                # If account is fully completed, return immediately
                if res and res.get("completed", False):
                    if accumulated_chars:
                        res["characters_results"] = list(accumulated_chars.values())
                    return res

                if self.is_stopped():
                    break
                
                # Otherwise, we retry
                retries += 1
                if retries < self.max_retries and not self.is_stopped():
                    # Rotate Proxy on retry if enabled
                    if (self.proxy_rotation or True) and len(task) >= 5:
                        try:
                            old_proxy = task[4]
                            # Nếu đã thử 2 lần proxy mà vẫn lỗi, thử fallback IP trực tiếp (None)
                            if retries >= 3 and old_proxy is not None:
                                new_proxy = None
                                self.log(f"\n\033[93m[FALLBACK IP CHÍNH]\033[0m {task[2]} đã thử {retries} lần với Proxy thất bại. Chuyển sang kết nối IP Trực Tiếp...")
                            else:
                                new_proxy = FreeProxyManager.get_random_proxy(exclude_proxy=old_proxy)
                            
                            if new_proxy != old_proxy:
                                task_list = list(task)
                                task_list[4] = new_proxy
                                task = tuple(task_list)
                                if new_proxy:
                                    self.log(f"\n\033[93m[ĐỔI PROXY THÀNH CÔNG]\033[0m {task[2]} chuyển từ {old_proxy} sang {new_proxy}")
                        except Exception as e:
                            self.log(f"\n\033[91m[LỖI ĐỔI PROXY]\033[0m {e}")
                    
                    self.log(f"\n\033[93m[CHẠY LẠI {self.thread_name.upper()}]\033[0m {task[2]} gặp lỗi hoặc chưa xong. Thử lại lần {retries}/{self.max_retries}...")
                    if self._wait_interruptible(0.1):
                        break

            if last_res and accumulated_chars:
                last_res["characters_results"] = list(accumulated_chars.values())

            # Đã hết số lần thử lại tối đa (retries >= self.max_retries)
            # Chỉ thông báo khi tài khoản thực sự không thể vào được game nữa!
            if not self.is_stopped() and (not last_res or not last_res.get("completed", False)):
                err_msg = (last_res.get("error") if last_res else "") or "Không thể kết nối hoặc vào game sau nhiều lần thử lại"
                username = task[2]
                self.log(f"\n\033[91m[KHÔNG THỂ VÀO GAME]\033[0m {username} đã thử lại tối đa {self.max_retries}/{self.max_retries} lần nhưng không thể vào game: {err_msg}")
                try:
                    from core.tele_notifier import TeleNotifier
                    TeleNotifier.get_instance().send_error_alert(
                        title="Tài Khoản Không Thể Vào Game (Hết Lượt Thử)",
                        detail=f"Tài khoản: <b>{username}</b>\nĐã thử lại: <b>{self.max_retries}/{self.max_retries} lần</b> thất bại.\nChi tiết lỗi: <code>{err_msg}</code>\nTrạng thái: Đã dừng thử, không thể vào được nữa!",
                        level="ERROR",
                        source=f"[{self.thread_name} | {username}]",
                        cooldown=300.0
                    )
                except Exception:
                    pass

            return last_res
        finally:
            with self.active_workers_lock:
                self.active_workers_count = max(0, self.active_workers_count - 1)

    def _process_single_account_internal(self, task: Tuple, total: int, completed_char_names: Optional[Set[str]] = None) -> Dict[str, Any]:
        idx = task[0]
        username = task[2]
        password = task[3]
        base_proxy = task[4] if len(task) >= 5 else None

        current_proxy = base_proxy
        if self.proxy_rotation and not current_proxy:
            new_p = FreeProxyManager.acquire_proxy()
            if new_p:
                current_proxy = new_p

        # Extract specific host/port if provided (for multi-server functionality)
        g_host = task[5] if len(task) >= 10 else settings.game_host
        g_port = task[6] if len(task) >= 10 else settings.game_port
        m_host = task[7] if len(task) >= 10 else settings.master_host
        m_port = task[8] if len(task) >= 10 else settings.master_port
        s_name = task[9] if len(task) >= 10 else settings.game_server_name

        proxy_tag = f" [Proxy: {current_proxy}]" if current_proxy else ""
        thread_tag = f"[{self.thread_name} {idx}/{total} | {s_name} | {username}{proxy_tag}]"
        
        account_result = {
            "username": username,
            "completed": False,
            "error": "",
            "characters_results": []
        }

        # 1. Master Login (Internal Retry)
        login_data = None
        err_reason = ""
        
        for proxy_attempt in range(3):
            if self.is_stopped():
                break
            try:
                login_data, err_reason = AuthManager.master_login(m_host, m_port, username, password, proxy=current_proxy)
                if login_data:
                    break
            except Exception as e:
                err_reason = str(e)

            if self.is_stopped():
                break

            if login_data is None:
                if "Sai tài khoản/mật khẩu" in err_reason or "bị khóa" in err_reason or "không tồn tại" in err_reason:
                    account_result["error"] = err_reason
                    account_result["completed"] = True  # Fatal error, no retry
                    self.log(f"{thread_tag} [LỖI ĐĂNG NHẬP] {err_reason}")
                    if self.proxy_rotation:
                        FreeProxyManager.release_proxy(current_proxy)
                    try:
                        from core.tele_notifier import TeleNotifier
                        TeleNotifier.get_instance().send_error_alert(
                            title="Lỗi Tài Khoản (Không Thể Vào Game)",
                            detail=f"Tài khoản: <b>{username}</b>\nLý do: <code>{err_reason}</code>\nTrạng thái: Không thể vào game nữa (Bị khóa hoặc sai mật khẩu)!",
                            level="CRITICAL",
                            source=thread_tag,
                            cooldown=300.0
                        )
                    except Exception:
                        pass
                    return account_result
                else:
                    if self.proxy_rotation:
                        FreeProxyManager.release_proxy(current_proxy)
                        FreeProxyManager.mark_proxy_dead(current_proxy)
                        if self.is_stopped():
                            break
                        new_proxy = FreeProxyManager.acquire_proxy(exclude_proxy=current_proxy)
                        if new_proxy and new_proxy != current_proxy:
                            current_proxy = new_proxy
                            proxy_tag = f" [Proxy: {current_proxy}]"
                            thread_tag = f"[{self.thread_name} {idx}/{total} | {s_name} | {username}{proxy_tag}]"
                            self.log(f"{thread_tag} \033[93m[ĐỔI PROXY]\033[0m Lỗi mạng, thử lại với proxy mới: {current_proxy}")
                            if self._wait_interruptible(0.1):
                                break
                            continue
                    break

        if self.is_stopped():
            account_result["error"] = "Dừng bởi người dùng"
            if self.proxy_rotation:
                FreeProxyManager.release_proxy(current_proxy)
            return account_result

        if not login_data:
            account_result["error"] = err_reason
            self.log(f"{thread_tag} [LỖI ĐĂNG NHẬP] {err_reason}")
            if self.proxy_rotation:
                FreeProxyManager.release_proxy(current_proxy)
            return account_result

        ukey, session_id, login_setting = login_data
        account_result["session_id"] = session_id
        account_result["ukey"] = ukey
        dev = DeviceFaker.get_device_fingerprint(username)

        # 2. Game Server Connection
        game_conn = AuthManager.connect_game_server(g_host, g_port, ukey, session_id, proxy=current_proxy)
        if not game_conn:
            account_result["error"] = "Không kết nối được Game Server"
            self.log(f"{thread_tag} [LỖI KẾT NỐI] Không kết nối được Game Server")
            if self.proxy_rotation:
                FreeProxyManager.release_proxy(current_proxy)
                FreeProxyManager.mark_proxy_dead(current_proxy)
            return account_result

        # Update task with current proxy so child runner uses it
        task_list = list(task)
        if len(task_list) >= 5:
            task_list[4] = current_proxy
        task = tuple(task_list)

        # Save proxy since it successfully connected and logged in
        if current_proxy:
            FreeProxyManager.save_proxy_for_account(username, password, current_proxy)

        # Let subclass process characters
        try:
            try:
                self.process_account(game_conn, task, thread_tag, login_setting, dev, account_result, completed_char_names=completed_char_names)
            except TypeError:
                self.process_account(game_conn, task, thread_tag, login_setting, dev, account_result)
        except Exception as e:
            account_result["error"] = str(e)
            account_result["completed"] = False
            self.log(f"{thread_tag} [LỖI NGOẠI LỆ] {e}")
        finally:
            if game_conn:
                try:
                    AuthManager.leave_character(game_conn)
                except Exception:
                    pass
                try:
                    game_conn.close()
                except Exception:
                    pass
            if self.proxy_rotation:
                FreeProxyManager.release_proxy(current_proxy)
        return account_result


class CharacterLoopRunner(BaseAccountRunner):
    """
    Subclass of BaseAccountRunner that automatically fetches characters,
    iterates through them, handles enter_character and Kẹt Acc detection,
    and manages the `failed_chars` logic for "CHƯA XONG HẾT NV" retries.
    Subclasses MUST override `process_character()`.
    """
    def process_account(self, game_conn, task: Tuple, thread_tag: str, login_setting: int, dev: Dict[str, str], account_result: Dict[str, Any], completed_char_names: Optional[Set[str]] = None):
        characters = AuthManager.fetch_character_list(game_conn)
        if not characters:
            account_result["error"] = "Chưa có nhân vật hoặc tải lỗi"
            self.log(f"{thread_tag} [CẢNH BÁO] Không có nhân vật. Có thể do lỗi mạng, sẽ thử lại.")
            account_result["completed"] = False
            return

        failed_chars = []
        for char_idx, char in enumerate(characters):
            if self.is_stopped():
                account_result["completed"] = False
                return

            char_tag = f"{thread_tag} [Nhân vật {char_idx + 1}/{len(characters)} - {char.name}]"
            
            # If character already completed in previous retry attempt, skip it!
            if completed_char_names and char.name in completed_char_names:
                self.log(f"{char_tag} \033[92m[ĐÃ XONG Ở LƯỢT TRƯỚC]\033[0m Bỏ qua, tiếp tục nhân vật tiếp theo.")
                continue

            # If subclass wants to skip this character
            if not self.should_process_character(char, task):
                continue
                
            enter_ok = False
            try:
                enter_ok = AuthManager.enter_character(game_conn, char.id, login_setting, device_id=dev.get("android_id", ""))
            except Exception:
                pass

            if not enter_ok:
                self.log(f"{char_tag} \033[93m[CHƯA VÀO ĐƯỢC]\033[0m Thêm nhân vật vào lượt chạy vét tiếp theo.")
                failed_chars.append(char)
                continue

            try:
                # Actual subclass logic
                self.process_character(game_conn, char, task, char_tag, account_result)
            except Exception as e:
                self.log(f"{char_tag} [LỖI NGOẠI LỆ NV] {e}")
                failed_chars.append(char)
                # Socket bị ngắt kết nối! Đánh dấu các nhân vật còn lại chưa xong và break để đổi proxy kết nối lại
                if isinstance(e, (ConnectionError, TimeoutError, socket.error, OSError)) or any(kw in str(e).lower() for kw in ["socket", "kết nối", "phản hồi", "broken pipe", "connection", "network", "timed out", "reset", "refused", "eof"]):
                    for rem_char in characters[char_idx + 1:]:
                        if (not completed_char_names or rem_char.name not in completed_char_names) and self.should_process_character(rem_char, task):
                            failed_chars.append(rem_char)
                    break
            finally:
                try:
                    AuthManager.leave_character(game_conn)
                except Exception:
                    pass

        # Check completion:
        current_done_names = set(completed_char_names or set())
        for cr in account_result.get("characters_results", []):
            cn = cr.get("name") or cr.get("char_name")
            if cn:
                current_done_names.add(cn)

        remaining_uncompleted = [c for c in characters if c.name not in current_done_names and self.should_process_character(c, task)]

        if not remaining_uncompleted and not account_result.get("error"):
            account_result["completed"] = True
            account_result["error"] = ""
        else:
            account_result["completed"] = False
            missing_chars = len(remaining_uncompleted)
            if not account_result.get("error"):
                account_result["error"] = f"Còn {missing_chars}/{len(characters)} nhân vật chưa xong"
            self.log(f"{thread_tag} \033[93m[CHƯA XONG HẾT NV]\033[0m Sẽ đưa vào hàng đợi chạy vét tiếp: {account_result['error']}")

    def should_process_character(self, char, task: Tuple) -> bool:
        """Override this to skip certain characters. Return True by default."""
        return True

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        """
        Abstract method. Subclasses MUST override this.
        """
        raise NotImplementedError("Subclasses must implement process_character")
