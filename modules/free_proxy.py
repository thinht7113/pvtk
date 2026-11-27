import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import urllib.request
import threading
import random
import time
import socket
import select
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.network import _connect_socks5
from core.config import settings

class FreeProxyManager:
    _proxies = []
    _last_fetch = 0.0
    _last_web_fetch = 0.0
    _lock = threading.Lock()
    _file_lock = threading.Lock()
    _background_thread = None
    _stop_event = threading.Event()
    _is_fetching = False
    _proxy_usage = {}  # proxy_str -> active threads count
    _proxy_fail_counts = {}  # proxy_str -> consecutive fail count

    MAX_POOL_PROXIES = 120
    MIN_WEB_FETCH_INTERVAL = 300.0
    EMPTY_POOL_WEB_COOLDOWN = 60.0
    MIN_FILE_RECHECK_INTERVAL = 60.0
    MAX_RESERVOIR_CANDIDATES = 1500
    CHECK_WORKERS = 20
    DEAD_FAIL_THRESHOLD = 3
    MAX_SAVED_PROXIES = 500
    
    @classmethod
    def start_background_worker(cls):
        """Starts a background thread to refresh proxies only when pool runs low."""
        with cls._lock:
            if cls._stop_event.is_set():
                return
            if cls._background_thread is None or not cls._background_thread.is_alive():
                cls._background_thread = threading.Thread(
                    target=cls._background_worker_loop, daemon=True, name="FreeProxyBgWorker"
                )
                cls._background_thread.start()

    @classmethod
    def stop_background_worker(cls):
        cls._stop_event.set()

    @classmethod
    def _background_worker_loop(cls):
        while not cls._stop_event.is_set():
            try:
                with cls._lock:
                    pool_count = len(cls._proxies)
                if pool_count < 20:
                    cls.fetch_proxies(force=False)
            except Exception:
                pass
            if cls._stop_event.wait(timeout=60):
                break

    @classmethod
    def _merge_proxies(cls, new_valid):
        """Merge newly validated proxies with existing healthy pool without wiping good ones."""
        if not new_valid:
            return
        with cls._lock:
            merged = []
            seen = set()
            for p in new_valid:
                if p and p not in seen:
                    seen.add(p)
                    merged.append(p)
                cls._proxy_fail_counts.pop(p, None)
            for p in cls._proxies:
                if p and p not in seen:
                    seen.add(p)
                    merged.append(p)
            if len(merged) > cls.MAX_POOL_PROXIES:
                merged = merged[:cls.MAX_POOL_PROXIES]
            random.shuffle(merged)
            cls._proxies = merged
            cls._last_fetch = time.time()
            active_set = set(cls._proxies)
            for k in list(cls._proxy_usage.keys()):
                if k not in active_set:
                    cls._proxy_usage.pop(k, None)
            for k in list(cls._proxy_fail_counts.keys()):
                if k not in active_set:
                    cls._proxy_fail_counts.pop(k, None)

    @classmethod
    def _check_socks5_candidate(cls, proxy_str: str):
        if cls._stop_event.is_set():
            return None
        sock = None
        try:
            parts = proxy_str.split(":")
            if len(parts) != 2 or not parts[1].isdigit():
                return None
            host, port = parts[0], int(parts[1])
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((host, port))
            if cls._stop_event.is_set():
                return None
            if _connect_socks5(sock, settings.master_host, settings.master_port):
                return f"socks5://{proxy_str}"
        except Exception:
            pass
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        return None

    @classmethod
    def _check_candidates_pool(cls, candidates, target_valid: int = 50):
        valid_proxies = []
        if not candidates or cls._stop_event.is_set():
            return valid_proxies
        executor = ThreadPoolExecutor(max_workers=cls.CHECK_WORKERS)
        try:
            futures = [executor.submit(cls._check_socks5_candidate, p) for p in candidates]
            for fut in as_completed(futures):
                if cls._stop_event.is_set():
                    for rem in futures:
                        rem.cancel()
                    break
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res:
                    valid_proxies.append(res)
                if len(valid_proxies) >= target_valid:
                    for rem in futures:
                        rem.cancel()
                    break
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return valid_proxies

    @classmethod
    def fetch_proxies(cls, force=False):
        if cls._stop_event.is_set():
            return
        with cls._lock:
            if cls._stop_event.is_set():
                return
            if not force and len(cls._proxies) >= 20 and time.time() - cls._last_fetch < 600:
                return
            if cls._is_fetching:
                return  # Tránh đụng độ luồng ngầm và luồng chính
            cls._is_fetching = True
            existing_set = set(cls._proxies)
            pool_size = len(cls._proxies)
            last_fetch = cls._last_fetch

        reservoir = []
        test_candidates = []
        try:
            if cls._stop_event.is_set():
                return

            # Phase 1 — Fast Lane: Always check proxies_good.txt first when pool < 20
            import os
            should_check_file = (
                pool_size == 0
                or force
                or (time.time() - last_fetch >= cls.MIN_FILE_RECHECK_INTERVAL)
            )
            if pool_size < 20 and should_check_file and os.path.exists("proxies_good.txt"):
                file_candidates = []
                try:
                    with cls._file_lock:
                        with open("proxies_good.txt", "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                p = line.strip()
                                if p.startswith("socks5://"):
                                    p = p[9:]
                                if p and ":" in p and f"socks5://{p}" not in existing_set:
                                    file_candidates.append(p)
                except Exception:
                    file_candidates = []

                if file_candidates:
                    random.shuffle(file_candidates)
                    sample_list = file_candidates[:200]
                    valid_from_file = cls._check_candidates_pool(sample_list, target_valid=50)
                    if valid_from_file:
                        cls._merge_proxies(valid_from_file)
                    with cls._lock:
                        pool_size = len(cls._proxies)
                    if pool_size >= 20:
                        print(f"\033[92m[AUTO PROXY]\033[0m Đã nạp siêu tốc {len(valid_from_file)} Proxy hoạt động tốt từ file lưu trữ (Pool: {pool_size})!\n")
                        cls.start_background_worker()
                        return

            if cls._stop_event.is_set():
                return

            # Phase 2 — Rate-Limited Streaming Web Scrape (VN priority + Reservoir Sampling)
            now = time.time()
            with cls._lock:
                pool_size = len(cls._proxies)
                existing_set = set(cls._proxies)
            cooldown = cls.EMPTY_POOL_WEB_COOLDOWN if pool_size < 5 else cls.MIN_WEB_FETCH_INTERVAL
            if now - cls._last_web_fetch < cooldown:
                return
            cls._last_web_fetch = now

            print("\n\033[93m[AUTO PROXY]\033[0m Đang tìm kiếm Proxy SOCKS5 miễn phí (Ưu tiên Việt Nam)...")

            vn_url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=VN&ssl=all&anonymity=all"
            urls = [
                # Vietnamese proxies first (priority)
                vn_url,
                # Global SOCKS5 from multiple providers
                "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all&ssl=all&anonymity=all",
                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
                "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
                "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
                "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt",
                "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt",
                "https://raw.githubusercontent.com/ErcinDedeworker/proxy-list/main/proxy-list/data/socks5.txt",
                "https://raw.githubusercontent.com/STARTER-STARTER/socks5-list/main/proxy.txt",
                "https://api.openproxylist.xyz/socks5.txt",
                "https://www.proxy-list.download/api/v1/get?type=socks5",
                "https://raw.githubusercontent.com/manuGMG/proxy-365/main/SOCKS5.txt",
                "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks5.txt",
                "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/socks5.txt",
                "https://raw.githubusercontent.com/B4RC0DE-TM/proxy-list/main/SOCKS5.txt",
                "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt",
                "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/socks5_proxies.txt",
                "https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/socks5.txt",
                "https://proxyspace.pro/socks5.txt",
                "https://spys.me/socks.txt",
            ]

            cap = cls.MAX_RESERVOIR_CANDIDATES
            total_seen = 0
            max_bytes_per_url = 2 * 1024 * 1024
            max_lines_per_url = 30000
            vn_candidates = []
            max_vn_candidates = 300

            for url in urls:
                if cls._stop_event.is_set():
                    break
                is_vn_url = (url == vn_url)
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        bytes_read = 0
                        lines_read = 0
                        while bytes_read < max_bytes_per_url and lines_read < max_lines_per_url:
                            if cls._stop_event.is_set():
                                break
                            raw_line = resp.readline(512)
                            if not raw_line:
                                break
                            bytes_read += len(raw_line)
                            lines_read += 1
                            line = raw_line.decode("utf-8", errors="ignore").strip()
                            if not line or ":" not in line or len(line) > 64:
                                continue
                            total_seen += 1
                            if is_vn_url:
                                if len(vn_candidates) < max_vn_candidates:
                                    vn_candidates.append(line)
                            else:
                                if len(reservoir) < cap:
                                    reservoir.append(line)
                                else:
                                    j = random.randrange(total_seen)
                                    if j < cap:
                                        reservoir[j] = line
                except Exception:
                    pass

            if cls._stop_event.is_set():
                return

            if vn_candidates or reservoir:
                unique_vn = []
                unique_global = []
                seen = set()
                for p in vn_candidates:
                    if p not in seen and f"socks5://{p}" not in existing_set:
                        unique_vn.append(p)
                        seen.add(p)
                for p in reservoir:
                    if p not in seen and f"socks5://{p}" not in existing_set:
                        unique_global.append(p)
                        seen.add(p)

                random.shuffle(unique_global)
                unique_list = unique_vn + unique_global
                test_candidates = unique_list[:600]

                print(f"\033[93m[AUTO PROXY]\033[0m Đã lấy mẫu {len(unique_list)} proxies (VN: {len(unique_vn)}, tổng dòng web: {total_seen}). Đang kiểm tra tối đa {len(test_candidates)} ứng viên...")
                valid_proxies = []
                target_total = 30
                for start_idx in range(0, len(test_candidates), 200):
                    if cls._stop_event.is_set() or len(valid_proxies) >= target_total:
                        break
                    wave = test_candidates[start_idx:start_idx + 200]
                    remaining = target_total - len(valid_proxies)
                    wave_valid = cls._check_candidates_pool(wave, target_valid=remaining)
                    if wave_valid:
                        valid_proxies.extend(wave_valid)

                if valid_proxies:
                    cls._merge_proxies(valid_proxies)
                    for vp in valid_proxies:
                        cls.mark_proxy_good(vp)
                    with cls._lock:
                        new_pool_len = len(cls._proxies)
                    print(f"\033[92m[AUTO PROXY]\033[0m Lọc thành công {len(valid_proxies)} Proxy mới (Tổng pool: {new_pool_len})!\n")
                else:
                    with cls._lock:
                        cls._last_fetch = time.time()
                    print("\033[91m[AUTO PROXY]\033[0m Đợt quét web không có proxy mới sống, giữ nguyên pool hiện tại.\n")
            else:
                with cls._lock:
                    cls._last_fetch = time.time()
                print("\033[91m[AUTO PROXY]\033[0m Không lấy được Proxy SOCKS5 nào từ Web.\n")
        finally:
            reservoir.clear()
            test_candidates.clear()
            del reservoir, test_candidates
            with cls._lock:
                cls._is_fetching = False

    @classmethod
    def mark_proxy_good(cls, proxy_str):
        """Save a good proxy to file (capped at MAX_SAVED_PROXIES) and reset fail count."""
        if not proxy_str:
            return
        with cls._lock:
            cls._proxy_fail_counts.pop(proxy_str, None)
        if not proxy_str.startswith("socks5://"):
            return
        import os
        try:
            p = proxy_str[9:].strip() # remove socks5://
            if not p:
                return
            with cls._file_lock:
                ordered = []
                seen = set()
                if os.path.exists("proxies_good.txt"):
                    with open("proxies_good.txt", "r", encoding="utf-8") as f:
                        for line in f:
                            val = line.strip()
                            if val and val not in seen:
                                seen.add(val)
                                ordered.append(val)
                if p in seen and len(ordered) <= cls.MAX_SAVED_PROXIES:
                    return
                if p not in seen:
                    ordered.append(p)
                if len(ordered) > cls.MAX_SAVED_PROXIES:
                    ordered = ordered[-cls.MAX_SAVED_PROXIES:]
                tmp_p = f"proxies_good.txt.{os.getpid()}.tmp"
                with open(tmp_p, "w", encoding="utf-8") as f:
                    f.writelines(f"{item}\n" for item in ordered)
                os.replace(tmp_p, "proxies_good.txt")
        except Exception:
            pass

    @classmethod
    def test_proxy_alive(cls, proxy_str: str, timeout: float = 2.5) -> bool:
        """Kiểm tra nhanh xem Proxy có kết nối được tới Game Server / Master Server không."""
        if not proxy_str:
            return False
        p = proxy_str[9:] if proxy_str.startswith("socks5://") else proxy_str
        if ":" not in p:
            return False
        sock = None
        try:
            host, port = p.split(":")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, int(port)))
            # Kiểm tra bắt tay SOCKS5 tới Game Server
            return _connect_socks5(sock, settings.game_host, settings.game_port)
        except Exception:
            return False
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    @classmethod
    def acquire_proxy(cls, exclude_proxy=None):
        """Trả về 1 proxy có ít luồng sử dụng nhất từ pool đã được kiểm tra còn sống."""
        if cls._stop_event.is_set():
            return None
        with cls._lock:
            pool_len = len(cls._proxies)
            is_fetching = cls._is_fetching
        if pool_len <= 3 and not is_fetching and not cls._stop_event.is_set():
            cls.fetch_proxies()
            if not cls._stop_event.is_set():
                cls.start_background_worker()
            
        with cls._lock:
            if not cls._proxies:
                return None
            
            available = [p for p in cls._proxies if p != exclude_proxy]
            if not available:
                available = list(cls._proxies)
                
            # Sắp xếp proxy theo số lượng luồng đang dùng để cân bằng tải
            available.sort(key=lambda p: cls._proxy_usage.get(p, 0))
            min_usage = cls._proxy_usage.get(available[0], 0)
            best_candidates = [p for p in available if cls._proxy_usage.get(p, 0) == min_usage]
            best_proxy = random.choice(best_candidates)
            cls._proxy_usage[best_proxy] = cls._proxy_usage.get(best_proxy, 0) + 1
            return best_proxy

    @classmethod
    def release_proxy(cls, proxy_str):
        """Giải phóng 1 luồng khỏi proxy này để nhường cho luồng khác."""
        if not proxy_str:
            return
        with cls._lock:
            if proxy_str in cls._proxy_usage:
                cls._proxy_usage[proxy_str] = max(0, cls._proxy_usage[proxy_str] - 1)

    @classmethod
    def mark_proxy_dead(cls, proxy_str, force=False):
        if not proxy_str:
            return
        with cls._lock:
            cnt = cls._proxy_fail_counts.get(proxy_str, 0) + 1
            cls._proxy_fail_counts[proxy_str] = cnt
            if not force and cnt < cls.DEAD_FAIL_THRESHOLD:
                return
            if proxy_str in cls._proxies:
                cls._proxies.remove(proxy_str)
            cls._proxy_usage.pop(proxy_str, None)
            cls._proxy_fail_counts.pop(proxy_str, None)

    @classmethod
    def periodic_refresh(cls):
        """Được gọi bởi health_check mỗi 5 phút - chỉ refresh nếu pool proxy quá ít."""
        if cls._stop_event.is_set():
            return
        with cls._lock:
            current_count = len(cls._proxies)
        if current_count < 15:
            cls.fetch_proxies(force=False)

    _accounts_file = None
    @classmethod
    def set_accounts_file(cls, filepath):
        cls._accounts_file = filepath

    @classmethod
    def save_proxy_for_account(cls, username, password, proxy_str):
        if not cls._accounts_file or not proxy_str:
            return
        import os
        try:
            with cls._file_lock:
                if not os.path.exists(cls._accounts_file):
                    return
                with open(cls._accounts_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                new_lines = []
                changed = False
                for line in lines:
                    if not line.strip():
                        new_lines.append(line)
                        continue
                    parts = line.strip().split("|")
                    if len(parts) >= 2 and parts[0] == username and parts[1] == password:
                        if len(parts) < 3 or parts[2] != proxy_str:
                            new_lines.append(f"{username}|{password}|{proxy_str}\n")
                            changed = True
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                
                if changed:
                    tmp_acc = f"{cls._accounts_file}.{os.getpid()}.tmp"
                    with open(tmp_acc, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
                    os.replace(tmp_acc, cls._accounts_file)
        except Exception:
            pass

    @classmethod
    def get_random_proxy(cls, exclude_proxy=None):
        """(Legacy) Trả về proxy ngẫu nhiên, nên dùng acquire_proxy để thay thế."""
        return cls.acquire_proxy(exclude_proxy)

    @classmethod
    def assign_proxies_to_accounts(cls, accounts):
        """
        Duyệt qua list accounts (username, password, [proxy]),
        nếu account chưa có proxy hoặc proxy đã chết thì lấy 1 proxy sống từ pool gắn vào.
        Trả về list accounts mới (username, password, proxy).
        Đồng thời ghi lại file accounts_file nếu có thay đổi để duy trì proxy sống.
        """
        import os
        cls._stop_event.clear()
        cls.fetch_proxies()
        cls.start_background_worker()
        with cls._lock:
            has_proxies = bool(cls._proxies)
        if not has_proxies or cls._stop_event.is_set():
            return accounts

        # Kiểm tra song song tình trạng proxy của toàn bộ tài khoản
        dead_indices = set()
        dead_lock = threading.Lock()
        def _check_acc_proxy(item):
            if cls._stop_event.is_set():
                return
            idx, acc = item
            p = acc[2] if len(acc) >= 3 else None
            if not p or not p.strip() or not cls.test_proxy_alive(p, timeout=1.5):
                with dead_lock:
                    dead_indices.add(idx)

        executor = ThreadPoolExecutor(max_workers=cls.CHECK_WORKERS)
        try:
            list(executor.map(_check_acc_proxy, enumerate(accounts)))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if cls._stop_event.is_set():
            return accounts

        new_accounts = []
        has_changes = False
        for i, acc in enumerate(accounts):
            user = acc[0]
            pwd = acc[1]
            proxy = acc[2] if len(acc) >= 3 else None
            
            # Nếu chưa có proxy hoặc proxy đã chết -> cấp proxy sống mới từ pool
            if i in dead_indices:
                live_p = cls.acquire_proxy(exclude_proxy=proxy)
                if live_p:
                    proxy = live_p
                    has_changes = True
                
            new_accounts.append((user, pwd, proxy))

        # Tự động ghi lại file accounts_file để các lần chạy sau luôn có proxy sống
        if has_changes and cls._accounts_file and os.path.exists(cls._accounts_file):
            try:
                with cls._file_lock:
                    lines = []
                    for acc in new_accounts:
                        u, p, pr = acc[0], acc[1], acc[2]
                        pr_str = f"|{pr}" if pr else ""
                        lines.append(f"{u}|{p}{pr_str}\n")
                    tmp_acc = f"{cls._accounts_file}.{os.getpid()}.tmp"
                    with open(tmp_acc, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    os.replace(tmp_acc, cls._accounts_file)
            except Exception:
                pass
            
        return new_accounts

    @classmethod
    def run_with_proxy_rotation(cls, process_func, account_tuple, max_retries=4):
        import time
        current_tuple = list(account_tuple)
        
        for attempt in range(max_retries):
            if cls._stop_event.is_set():
                break
            err = ""
            result = {"error": "Lỗi không xác định"}
            try:
                result = process_func(tuple(current_tuple))
                err = result.get("error", "")
            except Exception as e:
                err = f"Ngoại lệ: {e}"
                result["error"] = err
            
            if err and any(kw in err.lower() for kw in ["kết nối", "phản hồi", "master", "game server", "mạng", "timeout", "ngoại lệ", "socket"]):
                current_proxy = current_tuple[-1] if len(current_tuple) >= 5 else None
                new_proxy = cls.get_random_proxy(exclude_proxy=current_proxy)
                if new_proxy and new_proxy != current_proxy:
                    idx = current_tuple[0]
                    total = current_tuple[1]
                    username = current_tuple[2]
                    print(f"\n[Tài khoản {idx}/{total} | {username}] \033[93m[ĐỔI PROXY TỰ ĐỘNG]\033[0m Lỗi ({err}).\n-> Thử lại lần {attempt+2}/{max_retries} với Proxy mới: {new_proxy}")
                    current_tuple[-1] = new_proxy
                    if cls._stop_event.wait(timeout=1.0):
                        return result
                    continue
            return result
            
        return result
