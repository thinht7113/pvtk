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
    _last_fetch = 0
    _lock = threading.Lock()
    _file_lock = threading.Lock()
    _background_thread = None
    _stop_event = threading.Event()
    _is_fetching = False
    
    @classmethod
    def start_background_worker(cls):
        """Starts a background thread to continually scrape proxies if needed."""
        with cls._lock:
            cls._stop_event.clear()
            if cls._background_thread is None or not cls._background_thread.is_alive():
                cls._background_thread = threading.Thread(target=cls._background_worker_loop, daemon=True)
                cls._background_thread.start()

    @classmethod
    def stop_background_worker(cls):
        cls._stop_event.set()
        cls._is_fetching = False

    @classmethod
    def _background_worker_loop(cls):
        while not cls._stop_event.is_set():
            try:
                # If we have less than 10 proxies or 5 minutes passed, fetch more
                if len(cls._proxies) < 25 or time.time() - cls._last_fetch > 300:
                    cls.fetch_proxies(force=True)
            except Exception:
                pass
            if cls._stop_event.wait(timeout=30):
                break

    @classmethod
    def fetch_proxies(cls, force=False):
        if cls._stop_event.is_set():
            return
        with cls._lock:
            if cls._stop_event.is_set():
                return
            # Chỉ bỏ qua nếu còn trên 5 proxy hoạt động tốt VÀ chưa quá 10 phút
            if not force and len(cls._proxies) > 20 and time.time() - cls._last_fetch < 600:
                return
            if cls._is_fetching:
                return # Tránh đụng độ luồng ngầm và luồng chính
            cls._is_fetching = True

        try:
            if cls._stop_event.is_set():
                return
            print("\n\033[93m[AUTO PROXY]\033[0m Đang tìm kiếm Proxy SOCKS5 miễn phí (Ưu tiên Việt Nam)...")
            
            fetched_list = []
            
            # Load proxies_good.txt if exists
            import os
            try:
                if os.path.exists("proxies_good.txt"):
                    with open("proxies_good.txt", "r", encoding="utf-8") as f:
                        for line in f:
                            p = line.strip()
                            if p and ":" in p:
                                if p.startswith("socks5://"): p = p[9:]
                                fetched_list.append(p)
            except Exception:
                pass
                
            valid_proxies = []
            def check_proxy(proxy_str):
                if cls._stop_event.is_set() or len(valid_proxies) >= 80:
                    return
                sock = None
                try:
                    host, port = proxy_str.split(":")
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2.0) # Timeout ngắn để loại proxy lag
                    sock.connect((host, int(port)))
                    if cls._stop_event.is_set() or len(valid_proxies) >= 80:
                        return
                    if _connect_socks5(sock, settings.master_host, settings.master_port):
                        valid_proxies.append(f"socks5://{proxy_str}")
                except Exception:
                    pass
                finally:
                    if sock is not None:
                        try:
                            sock.close()
                        except Exception:
                            pass

            # Fast Lane: Nếu không bị bắt buộc ép tải từ web, thử kiểm tra proxy đã lưu trước
            if not force and fetched_list:
                # Chỉ kiểm tra tối đa 200 proxy gần nhất trong file để khởi động trong 2-3 giây
                sample_list = fetched_list[-200:]
                random.shuffle(sample_list)
                with ThreadPoolExecutor(max_workers=50) as executor:
                    futures = [executor.submit(check_proxy, p) for p in sample_list]
                    for fut in as_completed(futures):
                        if len(valid_proxies) >= 50 or cls._stop_event.is_set():
                            for rem in futures:
                                rem.cancel()
                            break
                    
                if len(valid_proxies) >= 15:
                    with cls._lock:
                        cls._proxies = valid_proxies
                        random.shuffle(cls._proxies)
                        cls._last_fetch = time.time()
                    print(f"\033[92m[AUTO PROXY]\033[0m Đã nạp siêu tốc {len(valid_proxies)} Proxy hoạt động tốt từ file lưu trữ!\n")
                    # Bật luồng ngầm đi cào web thêm
                    cls.start_background_worker()
                    return
                else:
                    print("\033[93m[AUTO PROXY]\033[0m Proxy cũ đã chết nhiều, bắt đầu cào proxy mới từ Internet...")
                    valid_proxies = [] # Reset lại để quét chung
                    
            urls = [
                # Vietnamese proxies first (priority)
                "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=VN&ssl=all&anonymity=all",
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
            
            for url in urls:
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = resp.read().decode('utf-8').splitlines()
                    for line in data:
                        line = line.strip()
                        if line and ":" in line:
                            fetched_list.append(line)
                except Exception as e:
                    pass
            
            if fetched_list:
                unique_list = []
                seen = set()
                for p in fetched_list:
                    if p not in seen:
                        unique_list.append(p)
                        seen.add(p)
                fetched_list = unique_list
                
                # Trộn ngẫu nhiên và chỉ kiểm tra tối đa 250 proxy để hoàn thành trong 2-3s
                random.shuffle(fetched_list)
                test_candidates = fetched_list[:250]
                
                print(f"\033[93m[AUTO PROXY]\033[0m Đã tải {len(fetched_list)} proxies từ Web. Đang kiểm tra {len(test_candidates)} ứng viên (khoảng 3s)...")
                
                with ThreadPoolExecutor(max_workers=50) as executor:
                    futures = [executor.submit(check_proxy, p) for p in test_candidates]
                    for fut in as_completed(futures):
                        if len(valid_proxies) >= 60 or cls._stop_event.is_set():
                            for rem in futures:
                                rem.cancel()
                            break

                if valid_proxies:
                    with cls._lock:
                        cls._proxies = valid_proxies
                        random.shuffle(cls._proxies) # Shuffle để phân phối tải
                        cls._last_fetch = time.time()
                    for vp in valid_proxies:
                        cls.mark_proxy_good(vp)
                    print(f"\033[92m[AUTO PROXY]\033[0m Lọc thành công {len(valid_proxies)} Proxy SIÊU NHANH & HOẠT ĐỘNG TỐT!\n")
                else:
                    with cls._lock:
                        cls._last_fetch = time.time()
                    print("\033[91m[AUTO PROXY]\033[0m Tất cả Proxy miễn phí đều chết hoặc bị chặn bởi Game Server. Sẽ sử dụng IP thật.\n")
            else:
                with cls._lock:
                    cls._last_fetch = time.time()
                print("\033[91m[AUTO PROXY]\033[0m Không lấy được Proxy SOCKS5 nào, sẽ sử dụng IP thật.\n")
        finally:
            with cls._lock:
                cls._is_fetching = False

    MAX_SAVED_PROXIES = 500

    @classmethod
    def mark_proxy_good(cls, proxy_str):
        """Save a good proxy to file (capped at MAX_SAVED_PROXIES) so we can reuse it next time."""
        if not proxy_str or not proxy_str.startswith("socks5://"):
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

    _proxy_usage = {} # proxy_str -> active threads count

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
        import random
        cls._stop_event.clear()
        if not cls._proxies or len(cls._proxies) <= 3:
            cls.fetch_proxies()
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
    def mark_proxy_dead(cls, proxy_str):
        if not proxy_str:
            return
        with cls._lock:
            if proxy_str in cls._proxies:
                cls._proxies.remove(proxy_str)
            if proxy_str in cls._proxy_usage:
                del cls._proxy_usage[proxy_str]

    @classmethod
    def periodic_refresh(cls):
        """Được gọi bởi health_check mỗi 5 phút - chỉ refresh nếu pool proxy quá ít."""
        if cls._stop_event.is_set():
            return
        with cls._lock:
            current_count = len(cls._proxies)
        if current_count < 15:
            cls.fetch_proxies(force=True)

    _accounts_file = None
    @classmethod
    def set_accounts_file(cls, filepath):
        cls._accounts_file = filepath

    @classmethod
    def save_proxy_for_account(cls, username, password, proxy_str):
        if not cls._accounts_file or not proxy_str: return
        import os
        try:
            with cls._lock:
                if not os.path.exists(cls._accounts_file): return
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
                    with open(cls._accounts_file, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
        except Exception:
            pass

    @classmethod
    def get_random_proxy(cls, exclude_proxy=None):
        """(Legacy) Trả về proxy ngẫu nhiên, nên dùng acquire_proxy để thay thế."""
        cls._stop_event.clear()
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
        if not cls._proxies:
            return accounts

        # Kiểm tra song song tình trạng proxy của toàn bộ tài khoản (50 luồng) để hoàn thành trong 1-2 giây
        dead_indices = set()
        def _check_acc_proxy(item):
            idx, acc = item
            p = acc[2] if len(acc) >= 3 else None
            if not p or not p.strip() or not cls.test_proxy_alive(p, timeout=1.5):
                dead_indices.add(idx)

        with ThreadPoolExecutor(max_workers=50) as executor:
            executor.map(_check_acc_proxy, enumerate(accounts))

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
                with cls._lock:
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
                    time.sleep(1.0)
                    continue
            return result
            
        return result
