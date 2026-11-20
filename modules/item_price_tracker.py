import os
import json
import time
import threading
from typing import Dict, Any, List, Optional, Tuple

class ItemPriceTracker:
    """
    Hệ thống phân tích và lưu trữ giá thị trường Sở Giao Dịch (Price Discovery & Market Intelligence).
    - Lưu trữ lịch sử giá đơn vị (Unit Price = Total Price / Quantity) cho từng vật phẩm.
    - Chống nhiễu giá ảo bằng Moving Average và Median (Trung vị).
    - Tự động nhận diện: Kèo rẻ bất thường (Bargain), Phá đáy lịch sử (All-Time Low), và Vật phẩm hiếm (Rare Asset).
    """

    def __init__(self, db_path: str = "storage/item_price_history.json", max_history_samples: int = 30):
        self.db_path = db_path
        self.max_history_samples = max_history_samples
        self.lock = threading.Lock()
        self.price_db: Dict[str, Dict[str, Any]] = {}
        self._load_database()

    def _get_item_key(self, name: str, grade: int) -> str:
        """Định danh vật phẩm kết hợp Tên và Phẩm chất (ví dụ: 'Ngọc Bích Cấp 4|2')."""
        clean_name = name.strip()
        return f"{clean_name}|{grade}"

    def _load_database(self):
        """Nạp cơ sở dữ liệu giá từ file JSON."""
        if not os.path.exists(self.db_path):
            self.price_db = {}
            return

        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                self.price_db = json.load(f)
        except Exception as e:
            print(f"⚠️ [GIÁ SGD] Lỗi khi nạp '{self.db_path}': {e}. Khởi tạo bộ nhớ tạm.")
            self.price_db = {}

    def _save_database(self):
        """Lưu cơ sở dữ liệu giá nguyên tử (Atomic write via .tmp file)."""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            tmp_path = f"{self.db_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.price_db, f, ensure_ascii=False, indent=2)
            if os.path.exists(self.db_path):
                os.replace(tmp_path, self.db_path)
            else:
                os.rename(tmp_path, self.db_path)
        except Exception as e:
            print(f"⚠️ [GIÁ SGD] Lỗi khi lưu '{self.db_path}': {e}")

    @staticmethod
    def _calculate_median(values: List[float]) -> float:
        """Tính trung vị (Median) để loại trừ nhiễu giá ảo."""
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        mid = n // 2
        if n % 2 == 1:
            return round(sorted_vals[mid], 2)
        return round((sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0, 2)

    def process_item_listing(
        self,
        name: str,
        grade: int,
        quantity: int,
        gold: int,
        silver: int,
        is_new_listing: bool = False
    ) -> Dict[str, Any]:
        """
        Xử lý và cập nhật giá của một lô hàng trên Sở Giao Dịch:
        - Chuẩn hóa Đơn giá: Unit Price = Total Price / Quantity
        - So sánh với giá trị lịch sử để đánh giá độ rẻ/hiếm.
        - Trả về thông tin phân tích thị trường (Analysis Report).
        """
        if not name or '\x00' in name or len(name) > 60 or quantity <= 0 or gold < 0 or silver < 0:
            return {"is_first_time": False, "is_bargain": False, "is_rare": False, "reasons": []}

        qty = max(1, quantity)
        unit_gold = round(gold / qty, 2) if gold > 0 else 0.0
        unit_silver = round(silver / qty, 1) if silver > 0 else 0.0
        key = self._get_item_key(name, grade)

        with self.lock:
            record = self.price_db.get(key)
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")

            is_first_time = (record is None)
            is_bargain = False
            is_rare = False
            discount_percent = 0.0
            reasons = []

            if is_first_time:
                # Món đồ hoàn toàn mới, chưa từng thấy trên sàn
                record = {
                    "name": name,
                    "grade": grade,
                    "gold_stats": {
                        "min_unit_price": unit_gold if unit_gold > 0 else None,
                        "max_unit_price": unit_gold if unit_gold > 0 else None,
                        "avg_unit_price": unit_gold,
                        "median_unit_price": unit_gold,
                        "sample_count": 1 if unit_gold > 0 else 0,
                        "recent_prices": [unit_gold] if unit_gold > 0 else []
                    },
                    "silver_stats": {
                        "min_unit_price": unit_silver if unit_silver > 0 else None,
                        "max_unit_price": unit_silver if unit_silver > 0 else None,
                        "avg_unit_price": unit_silver,
                        "median_unit_price": unit_silver,
                        "sample_count": 1 if unit_silver > 0 else 0,
                        "recent_prices": [unit_silver] if unit_silver > 0 else []
                    },
                    "total_listings_seen": 1,
                    "first_seen": now_str,
                    "last_seen": now_str
                }
                self.price_db[key] = record

                if grade >= 3:
                    is_rare = True
                    reasons.append("Vật phẩm phẩm chất cao lần đầu xuất hiện trên sàn")

            else:
                # Đã có dữ liệu lịch sử
                total_seen = record.get("total_listings_seen", 0) + 1
                record["total_listings_seen"] = total_seen
                record["last_seen"] = now_str

                # Kiểm tra đánh giá giá Vàng
                g_stats = record.get("gold_stats", {})
                g_samples = g_stats.get("sample_count", 0)
                g_avg = g_stats.get("avg_unit_price", 0.0)
                g_min = g_stats.get("min_unit_price")

                if unit_gold > 0:
                    if g_samples >= 2 and g_avg > 0:
                        # Nếu rẻ hơn >= 30% so với giá trung bình thị trường
                        if unit_gold <= 0.70 * g_avg:
                            is_bargain = True
                            discount_percent = round((1.0 - (unit_gold / g_avg)) * 100, 1)
                            reasons.append(f"Rẻ hơn {discount_percent}% so với giá TB ({g_avg:,.1f} Vàng/món)")

                        # Nếu phá vỡ mức giá rẻ nhất từng ghi nhận
                        elif g_min is not None and unit_gold < g_min:
                            is_bargain = True
                            reasons.append(f"Phá đáy giá lịch sử: {unit_gold:,} Vàng (Trước đây thấp nhất {g_min:,} Vàng)")

                    # Cập nhật thống kê Vàng
                    recent_g = g_stats.get("recent_prices", [])
                    # Chỉ cập nhật giá vào database nếu không phải là giá bất thường quá lớn (tránh troll)
                    if g_avg == 0 or unit_gold <= 5 * g_avg:
                        recent_g.append(unit_gold)
                        if len(recent_g) > self.max_history_samples:
                            recent_g.pop(0)
                        g_stats["recent_prices"] = recent_g
                        g_stats["sample_count"] = g_samples + 1
                        g_stats["avg_unit_price"] = round(sum(recent_g) / len(recent_g), 2)
                        g_stats["median_unit_price"] = self._calculate_median(recent_g)
                        g_stats["min_unit_price"] = min(recent_g) if g_min is None else min(g_min, unit_gold)
                        g_stats["max_unit_price"] = max(recent_g) if g_stats.get("max_unit_price") is None else max(g_stats["max_unit_price"], unit_gold)

                # Kiểm tra đánh giá giá Bạc
                s_stats = record.get("silver_stats", {})
                s_samples = s_stats.get("sample_count", 0)
                s_avg = s_stats.get("avg_unit_price", 0.0)
                s_min = s_stats.get("min_unit_price")

                if unit_silver > 0:
                    if s_samples >= 2 and s_avg > 0:
                        if unit_silver <= 0.65 * s_avg:
                            is_bargain = True
                            s_disc = round((1.0 - (unit_silver / s_avg)) * 100, 1)
                            if s_disc > discount_percent:
                                discount_percent = s_disc
                            reasons.append(f"Rẻ hơn {s_disc}% Bạc so với giá TB ({s_avg:,.0f} Bạc/món)")
                        elif s_min is not None and unit_silver < s_min:
                            is_bargain = True
                            reasons.append(f"Phá đáy giá Bạc lịch sử: {unit_silver:,.0f} Bạc")

                    recent_s = s_stats.get("recent_prices", [])
                    if s_avg == 0 or unit_silver <= 5 * s_avg:
                        recent_s.append(unit_silver)
                        if len(recent_s) > self.max_history_samples:
                            recent_s.pop(0)
                        s_stats["recent_prices"] = recent_s
                        s_stats["sample_count"] = s_samples + 1
                        s_stats["avg_unit_price"] = round(sum(recent_s) / len(recent_s), 1)
                        s_stats["median_unit_price"] = self._calculate_median(recent_s)
                        s_stats["min_unit_price"] = min(recent_s) if s_min is None else min(s_min, unit_silver)
                        s_stats["max_unit_price"] = max(recent_s) if s_stats.get("max_unit_price") is None else max(s_stats["max_unit_price"], unit_silver)

                # Kiểm tra đồ hiếm (ít khi có người bán)
                if total_seen <= 3 and grade >= 3:
                    is_rare = True
                    reasons.append(f"Vật phẩm hiếm (Lịch sử chỉ mới xuất hiện {total_seen} lần)")

            # Quy tắc bắt đáy bán hớ (Misprice / Liquidation)
            if gold > 0 and gold <= 5 and grade >= 3:
                is_bargain = True
                if not any("siêu rẻ" in r for r in reasons):
                    reasons.append(f"Giá thanh lý cực thấp: {gold} Vàng")

            if silver > 0 and silver <= 10000 and grade >= 3 and gold == 0:
                is_bargain = True
                if not any("Bạc siêu rẻ" in r for r in reasons):
                    reasons.append(f"Giá Bạc thanh lý cực thấp: {silver:,} Bạc")

            return {
                "name": name,
                "grade": grade,
                "quantity": quantity,
                "gold": gold,
                "silver": silver,
                "unit_gold": unit_gold,
                "unit_silver": unit_silver,
                "is_bargain": is_bargain,
                "is_rare": is_rare,
                "discount_percent": discount_percent,
                "reasons": reasons,
                "market_gold_avg": record.get("gold_stats", {}).get("avg_unit_price", 0.0),
                "market_gold_min": record.get("gold_stats", {}).get("min_unit_price"),
                "market_silver_avg": record.get("silver_stats", {}).get("avg_unit_price", 0.0),
                "market_silver_min": record.get("silver_stats", {}).get("min_unit_price"),
                "total_seen": record.get("total_listings_seen", 1)
            }

    def save_checkpoint(self):
        """Lưu trạng thái cơ sở dữ liệu giá ra đĩa."""
        with self.lock:
            self._save_database()

    def query_item_price(self, search_keyword: str) -> List[Dict[str, Any]]:
        """
        Tra cứu thống kê giá của vật phẩm theo từ khóa (dùng cho lệnh /gia của Telegram Bot).
        """
        kw = search_keyword.strip().lower()
        if not kw:
            return []

        results = []
        with self.lock:
            for key, data in self.price_db.items():
                name = data.get("name", "")
                if kw in name.lower():
                    results.append(data)

        # Sắp xếp kết quả: số lần xuất hiện nhiều nhất lên trước
        results.sort(key=lambda x: -x.get("total_listings_seen", 0))
        return results[:10]
