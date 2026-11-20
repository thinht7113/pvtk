"""
Special Watcher Manager for the top 5 accounts in PVTK 24/7 Automation.
- Acc 1 (index 0): Watch Item Market (SGD) + Snatch Luck Money + Claim Online Rewards
- Acc 2 (index 1): Watch Character Market + Snatch Luck Money + Claim Online Rewards
- Acc 3, 4, 5 (index 2, 3, 4): Snatch Luck Money + Claim Online Rewards
- Seamless Telegram notification forwarding for rare items, characters, and luck money.
"""

import threading
from typing import List, Tuple, Dict, Any, Optional

from modules.character_market_watcher import MultiAccountMarketWatcher

class SpecialWatcherManager:
    def __init__(self, accounts: List[Tuple], telegram_bot=None):
        self.accounts = list(accounts)
        self.telegram_bot = telegram_bot
        self.watcher: Optional[MultiAccountMarketWatcher] = None
        self.thread: Optional[threading.Thread] = None
        self.is_running = False
        self.total_won_gold = 0

    def start(self):
        if self.is_running:
            return
        if not self.accounts:
            print("[SPECIAL WATCHER] Không có tài khoản nào cho các acc đặc biệt.")
            return

        self.is_running = True
        self.thread = threading.Thread(target=self._run_watcher, daemon=True, name="SpecialWatcherThread")
        self.thread.start()
        print(f"[SPECIAL WATCHER] Đã khởi động {len(self.accounts)} Acc đặc biệt theo dõi & đoạt Lì Xì.")

    def _run_watcher(self):
        try:
            self.watcher = MultiAccountMarketWatcher(
                accounts_file="accountsNV.txt",
                storage_char_file="storage/market_characters.json",
                storage_item_file="storage/market_items.json",
                telegram_bot=self.telegram_bot
            )

            # Override load_accounts to return our top 3 accounts with direct IP (proxy = None)
            self.watcher.load_accounts = lambda: [(acc[0], acc[1], None) for acc in self.accounts]

            # Hook into on_luck_money_won to track total won gold and notify Telegram
            orig_on_luck = self.watcher.on_luck_money_won
            def hooked_on_luck_money_won(worker, gold_won, new_total_gold, sender_name, note):
                self.total_won_gold += gold_won
                orig_on_luck(worker, gold_won, new_total_gold, sender_name, note)

            self.watcher.on_luck_money_won = hooked_on_luck_money_won

            # Start watcher main loop
            self.watcher.run()
        except Exception as e:
            import logging
            logging.getLogger("PVTK_247").error(f"[SPECIAL WATCHER LỖI KHỞI ĐỘNG] {e}", exc_info=True)
            print(f"[SPECIAL WATCHER ERR] {e}")
        finally:
            self.is_running = False

    def stop(self, timeout: float = 5.0):
        self.is_running = False
        if self.watcher:
            self.watcher.is_running = False
            for w in self.watcher.workers:
                w.is_running = False
                try:
                    if w.conn:
                        w.conn.close()
                except Exception:
                    pass
        if self.thread and self.thread.is_alive():
            try:
                self.thread.join(timeout=timeout)
            except Exception:
                pass
        self.thread = None
        self.watcher = None

    def get_status(self) -> Dict[str, Any]:
        if not self.watcher:
            return {
                "active": self.is_running,
                "online_count": 0,
                "total_workers": len(self.accounts),
                "active_threads": 0,
                "total_won_gold": self.total_won_gold
            }
        online_cnt = sum(1 for w in self.watcher.workers if w.is_connected or (w.conn and getattr(w.conn, "is_connected", False)))
        active_th = sum(1 for w in self.watcher.workers if getattr(w, "thread", None) and w.thread.is_alive())
        return {
            "active": self.is_running,
            "online_count": online_cnt,
            "total_workers": len(self.watcher.workers) if self.watcher.workers else len(self.accounts),
            "active_threads": active_th,
            "total_won_gold": self.total_won_gold
        }

    def get_connected_usernames(self) -> List[str]:
        """Danh sách tên đăng nhập của các acc watcher đang thực sự online."""
        if not self.watcher or not self.is_running:
            return []
        return [w.user for w in self.watcher.workers if w.is_connected or (w.conn and getattr(w.conn, "is_connected", False))]

    def get_gold_balances(self) -> Dict[str, int]:
        """Truy vấn số dư KNB (vàng) thực tế của các tài khoản đoạt lì xì (Chạy song song toàn bộ luồng)."""
        import concurrent.futures
        balances: Dict[str, int] = {}
        if self.watcher and self.watcher.workers:
            workers = list(self.watcher.workers)
            def _get_worker_gold(w):
                try:
                    return w.user, w.query_current_gold()
                except Exception:
                    return w.user, getattr(w, "current_gold", 0)

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(workers))) as executor:
                futures = {executor.submit(_get_worker_gold, w): w for w in workers}
                try:
                    for fut in concurrent.futures.as_completed(futures, timeout=2.5):
                        user, g = fut.result()
                        balances[user] = g
                except concurrent.futures.TimeoutError:
                    pass

            for w in workers:
                if w.user not in balances:
                    balances[w.user] = getattr(w, "current_gold", 0)
        else:
            for acc in self.accounts:
                balances[acc[0]] = 0
        return balances

    def send_all_knb(self, target_id: str, amount_per_account: Optional[int] = None) -> Dict[str, Any]:
        """
        Chuyển KNB hiện có của các tài khoản đoạt lì xì tới target_id qua thư in-game.
        Nếu amount_per_account được chỉ định (ví dụ = 1): chỉ gửi số KNB này từ mỗi tài khoản (Test Mode).
        Chạy gửi song song (ThreadPoolExecutor) để tối ưu tốc độ và không bị nghẽn nếu một acc chờ timeout.
        """
        import concurrent.futures
        results: Dict[str, Dict[str, Any]] = {}
        total_sent = 0
        if self.watcher and self.watcher.workers:
            workers = list(self.watcher.workers)
            def _send_worker(w):
                try:
                    ok, sent_amount, msg = w.send_gold_to_target(target_id, amount=amount_per_account)
                    return w.user, ok, sent_amount, msg
                except Exception as e:
                    return w.user, False, 0, str(e)

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(workers))) as executor:
                futures = [executor.submit(_send_worker, w) for w in workers]
                for fut in concurrent.futures.as_completed(futures):
                    user, ok, sent_amount, msg = fut.result()
                    results[user] = {
                        "success": ok,
                        "amount": sent_amount,
                        "message": msg
                    }
                    if ok:
                        total_sent += sent_amount
        return {
            "target": target_id,
            "total_sent": total_sent,
            "amount_per_account": amount_per_account,
            "details": results
        }

    def trigger_in_session_lottery(self):
        """Tài khoản accountsNV.txt được cấu hình cố định treo online 24/24, không ngắt phiên để rút thưởng."""
        return []

    def trigger_in_session_quiz(self):
        """Tài khoản accountsNV.txt được cấu hình cố định treo online 24/24, không ngắt phiên để vấn đáp."""
        return []

    def query_item_price(self, keyword: str) -> List[Dict[str, Any]]:
        """Tra cứu thống kê giá lịch sử của vật phẩm từ price_tracker."""
        if self.watcher and hasattr(self.watcher, "price_tracker") and self.watcher.price_tracker:
            return self.watcher.price_tracker.query_item_price(keyword)
        from modules.item_price_tracker import ItemPriceTracker
        tracker = ItemPriceTracker(db_path="storage/item_price_history.json")
        return tracker.query_item_price(keyword)


