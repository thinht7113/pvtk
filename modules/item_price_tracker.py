"""
Market Price Discovery & Deal Evaluation Engine for PVTK 24/7.
- Normalizes item names and separates enhanced equipment (+CH) from base equipment.
- Deduplicates listings by unique good_id via a bounded sidecar store so periodic
  15s scans never pollute historical samples or inflate seen counts.
- Strictly separates Gold listings (where `silver == 300 * gold` is the 3% auction tax)
  from true Silver-only listings (`gold == 0, silver > 0`).
- Uses Median + Rolling Floor + Live Market Floor comparison instead of fragile Mean
  or hardcoded liquidation thresholds to eliminate false positives.
"""

import os
import re
import json
import time
import threading
import collections
from typing import Dict, Any, List, Optional, Tuple, Set

WHITESPACE_RE = re.compile(r"\s+")


def normalize_item_name(name: str) -> str:
    """Normalizes non-breaking spaces and repeated whitespace in PVTK item names."""
    if not name:
        return ""
    return WHITESPACE_RE.sub(" ", str(name).replace("\xa0", " ")).strip()


def infer_enhance_tier(item_dict: Dict[str, Any]) -> int:
    """
    Infers whether an equipment listing is enhanced/socketed (+CH) from item metadata.
    Non-equipment items always return 0.
    """
    if item_dict is None:
        return 0
    if "enhance_tier" in item_dict:
        return 1 if int(item_dict.get("enhance_tier") or 0) > 0 else 0

    equip_cats = {
        "Kiếm", "Đao", "Súng", "Trượng", "Cung",
        "Nặng", "Trường Thương", "PhápKhí", "Hộ giáp", "SứcPhẩm"
    }
    cat = item_dict.get("category", "")
    if cat not in equip_cats:
        return 0

    star = max(0, int(item_dict.get("star", 0) or 0))
    up_star = max(0, int(item_dict.get("upgrade_star", 0) or 0))
    asc_star = max(0, int(item_dict.get("ascension_star", 0) or 0))
    attach_done = max(0, int(item_dict.get("attach_done", 0) or 0))
    return 1 if (star > 0 or up_star > 0 or asc_star > 0 or attach_done > 0) else 0


class ItemPriceTracker:
    """
    Hệ thống phân tích và lưu trữ giá thị trường Sở Giao Dịch (Price Discovery & Market Intelligence).
    - Lưu trữ lịch sử giá đơn vị (Unit Price = Total Price / Quantity) theo từng unique good_id.
    - Chống nhiễu giá ảo bằng Median (Trung vị), P25, và giới hạn outlier > 3x trung vị.
    - Tách bạch hoàn toàn giao dịch Vàng (bỏ qua 3% thuế Bạc đi kèm) và giao dịch thuần Bạc.
    - Xác định Kèo Rẻ (Bargain) dựa trên: giá trung vị lịch sử/hiện tại, giá sàn thấp nhất đang bán,
      và mức lãi tuyệt đối tối thiểu.
    """

    def __init__(self, db_path: str = "storage/item_price_history.json", max_history_samples: int = 50):
        self.db_path = db_path
        self.max_history_samples = max(10, int(max_history_samples))
        db_dir = os.path.dirname(self.db_path) or "."
        self.seen_ids_path = os.path.join(db_dir, "item_seen_good_ids.json")

        self.lock = threading.Lock()
        self.price_db: Dict[str, Dict[str, Any]] = {}
        self._seen_good_ids_dq: collections.deque = collections.deque(maxlen=30000)
        self._seen_good_ids_set: Set[int] = set()
        self._load_database()

    def _get_item_key(self, name: str, grade: int, enhance_tier: int = 0) -> str:
        """Định danh vật phẩm kết hợp Tên chuẩn hóa, Phẩm chất, và cấp cường hóa nếu có."""
        clean_name = normalize_item_name(name)
        if int(enhance_tier or 0) > 0:
            return f"{clean_name}|{int(grade)}|+CH"
        return f"{clean_name}|{int(grade)}"

    @staticmethod
    def _empty_currency_stats() -> Dict[str, Any]:
        return {
            "min_unit_price": None,
            "max_unit_price": None,
            "avg_unit_price": 0.0,
            "median_unit_price": 0.0,
            "p25_unit_price": 0.0,
            "sample_count": 0,
            "recent_prices": []
        }

    def _new_record(self, clean_name: str, grade: int, enhance_tier: int, now_str: str) -> Dict[str, Any]:
        return {
            "name": clean_name,
            "grade": int(grade),
            "enhance_tier": int(enhance_tier),
            "gold_stats": self._empty_currency_stats(),
            "silver_stats": self._empty_currency_stats(),
            "total_listings_seen": 0,
            "first_seen": now_str,
            "last_seen": now_str
        }

    def _atomic_write_json(self, path: str, payload: Any):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def _load_database(self):
        """Nạp cơ sở dữ liệu giá và danh sách good_id đã ghi nhận từ đĩa."""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.price_db = loaded
                else:
                    self.price_db = {}
            except Exception as e:
                print(f"⚠️ [GIÁ SGD] Lỗi khi nạp '{self.db_path}': {e}. Khởi tạo bộ nhớ tạm.")
                self.price_db = {}
        else:
            self.price_db = {}

        if os.path.exists(self.seen_ids_path):
            try:
                with open(self.seen_ids_path, "r", encoding="utf-8") as f:
                    ids_data = json.load(f)
                if isinstance(ids_data, list):
                    for gid in ids_data[-self._seen_good_ids_dq.maxlen:]:
                        try:
                            igid = int(gid)
                            if igid not in self._seen_good_ids_set:
                                self._seen_good_ids_dq.append(igid)
                                self._seen_good_ids_set.add(igid)
                        except (TypeError, ValueError):
                            pass
            except Exception as e:
                print(f"⚠️ [GIÁ SGD] Lỗi khi nạp '{self.seen_ids_path}': {e}. Bắt đầu danh sách mới.")

    def _save_database(self):
        """Lưu cơ sở dữ liệu giá và seen_good_ids nguyên tử."""
        try:
            self._atomic_write_json(self.db_path, self.price_db)
        except Exception as e:
            print(f"⚠️ [GIÁ SGD] Lỗi khi lưu '{self.db_path}': {e}")

        try:
            self._atomic_write_json(self.seen_ids_path, list(self._seen_good_ids_dq))
        except Exception as e:
            print(f"⚠️ [GIÁ SGD] Lỗi khi lưu '{self.seen_ids_path}': {e}")

    @staticmethod
    def _calculate_percentile(values: List[float], p: float) -> float:
        """Tính phân vị thứ p (0.0 .. 1.0) bằng nội suy tuyến tính."""
        if not values:
            return 0.0
        sorted_vals = sorted(float(v) for v in values)
        n = len(sorted_vals)
        if n == 1:
            return round(sorted_vals[0], 2)
        p_clamped = max(0.0, min(1.0, float(p)))
        pos = p_clamped * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        val = sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac
        return round(val, 2)

    @classmethod
    def _calculate_median(cls, values: List[float]) -> float:
        """Tính trung vị (Median) để loại trừ nhiễu giá ảo."""
        return cls._calculate_percentile(values, 0.50)

    def _mark_seen_unlocked(self, good_id: Optional[int]):
        if good_id is None:
            return
        try:
            igid = int(good_id)
        except (TypeError, ValueError):
            return
        if igid <= 0 or igid in self._seen_good_ids_set:
            return
        if len(self._seen_good_ids_dq) == self._seen_good_ids_dq.maxlen:
            evicted = self._seen_good_ids_dq.popleft()
            self._seen_good_ids_set.discard(evicted)
        self._seen_good_ids_dq.append(igid)
        self._seen_good_ids_set.add(igid)

    def is_good_id_seen(self, good_id: int) -> bool:
        try:
            igid = int(good_id)
        except (TypeError, ValueError):
            return False
        with self.lock:
            return igid in self._seen_good_ids_set

    def mark_good_id_seen(self, good_id: int):
        with self.lock:
            self._mark_seen_unlocked(good_id)

    def _update_currency_stats(
        self,
        stats: Dict[str, Any],
        unit_price: float,
        is_gold: bool,
        weight: int = 1
    ):
        """Cập nhật cửa sổ trượt giá trị gần nhất và các chỉ số thống kê cho 1 loại tiền tệ."""
        if unit_price <= 0:
            return
        recent = list(stats.get("recent_prices") or [])
        cur_med = self._calculate_median(recent) if recent else 0.0
        # Chống nhiễu giá troll quá cao (> 3x trung vị) khi đã có ít nhất 3 mẫu
        if len(recent) >= 3 and cur_med > 0 and unit_price > 3.0 * cur_med:
            return

        repeats = max(1, min(int(weight), 3))
        for _ in range(repeats):
            recent.append(unit_price)
            if len(recent) > self.max_history_samples:
                recent.pop(0)

        decimals = 2 if is_gold else 1
        stats["recent_prices"] = recent
        stats["sample_count"] = int(stats.get("sample_count", 0) or 0) + repeats
        stats["avg_unit_price"] = round(sum(recent) / len(recent), decimals)
        stats["median_unit_price"] = round(self._calculate_median(recent), decimals)
        stats["p25_unit_price"] = round(self._calculate_percentile(recent, 0.25), decimals)
        stats["min_unit_price"] = round(min(recent), decimals)
        stats["max_unit_price"] = round(max(recent), decimals)

    def _evaluate_same_currency_bargain(
        self,
        unit_price: float,
        qty: int,
        c_stats: Dict[str, Any],
        live_prices_raw: Optional[List[float]],
        min_discount_pct: float,
        min_floor_discount_pct: float,
        min_profit: float,
        min_samples: int,
        unit_label: str
    ) -> Tuple[bool, float, float, float, float, float, Optional[float], int, List[str]]:
        """
        Đánh giá kèo rẻ trong cùng 1 hệ tiền tệ (Vàng hoặc Bạc).
        Returns: (is_bargain, disc_pct, floor_disc_pct, est_profit, ref_price,
                  hist_med, live_floor, ref_samples, reasons)
        """
        c_samples = int(c_stats.get("sample_count", 0) or 0)
        hist_med = float(c_stats.get("median_unit_price", 0.0) or 0.0) if c_samples >= min_samples else 0.0
        recent = list(c_stats.get("recent_prices") or [])
        rolling_min = min(recent) if len(recent) >= min_samples else None

        live_prices = [float(p) for p in (live_prices_raw or []) if p and p > 0]
        live_floor = min(live_prices) if live_prices else None
        live_med = self._calculate_median(live_prices) if len(live_prices) >= 2 else 0.0

        if hist_med > 0 and live_med > 0:
            ref_price = min(hist_med, live_med)
        else:
            ref_price = hist_med or live_med

        ref_samples = max(c_samples, len(live_prices))
        reasons: List[str] = []

        if ref_price <= 0 or ref_samples < min_samples:
            return False, 0.0, 0.0, 0.0, ref_price, hist_med, live_floor, ref_samples, reasons

        disc_vs_ref = round((1.0 - unit_price / ref_price) * 100.0, 1)
        saving_vs_ref = round((ref_price - unit_price) * qty, 1)

        disc_vs_floor = 0.0
        beats_floor = True
        if live_floor is not None and live_floor > 0:
            disc_vs_floor = round((1.0 - unit_price / live_floor) * 100.0, 1)
            saving_vs_floor = round((live_floor - unit_price) * qty, 1)
            beats_floor = (disc_vs_floor >= min_floor_discount_pct) and (saving_vs_floor >= min_profit * 0.5)

        is_med_bargain = (disc_vs_ref >= min_discount_pct and saving_vs_ref >= min_profit and beats_floor)
        is_rolling_break = (
            rolling_min is not None
            and rolling_min > 0
            and unit_price <= rolling_min * 0.80
            and saving_vs_ref >= min_profit
            and beats_floor
        )

        if not (is_med_bargain or is_rolling_break):
            return False, 0.0, 0.0, 0.0, ref_price, hist_med, live_floor, ref_samples, reasons

        fmt_spec = ",.1f" if unit_label == "V/món" else ",.0f"
        if is_med_bargain:
            reasons.append(f"Rẻ hơn {disc_vs_ref:.0f}% so với giá Trung vị ({ref_price:{fmt_spec}} {unit_label})")
        if live_floor is not None and live_floor > 0:
            reasons.append(f"Thấp hơn giá rẻ nhất đang bán {disc_vs_floor:.0f}% (Sàn: {live_floor:{fmt_spec}} {unit_label})")
        if rolling_min is not None and rolling_min > 0 and unit_price <= rolling_min * 0.85:
            reasons.append(f"Phá đáy gần đây (đáy cũ {rolling_min:{fmt_spec}} {unit_label})")

        return (
            True,
            max(0.0, disc_vs_ref),
            max(0.0, disc_vs_floor),
            max(0.0, saving_vs_ref),
            ref_price,
            hist_med,
            live_floor,
            ref_samples,
            reasons
        )

    def record_baseline_snapshot(self, items_dict: Dict[int, Dict[str, Any]]) -> int:
        """
        Nạp baseline mặt bằng giá từ các món đang treo trên sàn.
        Mỗi good_id chưa từng thấy được ghi nhận ĐÚNG 1 LẦN.
        """
        if not items_dict:
            return 0
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        recorded_count = 0

        with self.lock:
            for gid_raw, it in items_dict.items():
                try:
                    gid = int(gid_raw)
                except (TypeError, ValueError):
                    gid = 0
                if gid <= 0 or gid in self._seen_good_ids_set:
                    continue

                clean_name = normalize_item_name(it.get("name", ""))
                if not clean_name or "\x00" in clean_name or len(clean_name) > 80:
                    continue

                qty = max(1, int(it.get("quantity", 1) or 1))
                gold = max(0, int(it.get("gold", 0) or 0))
                silver = max(0, int(it.get("silver", 0) or 0))
                if gold <= 0 and silver <= 0:
                    continue

                grade = int(it.get("grade", 0) or 0)
                enhance_tier = infer_enhance_tier(it)
                key = self._get_item_key(clean_name, grade, enhance_tier)

                record = self.price_db.get(key)
                if record is None:
                    record = self._new_record(clean_name, grade, enhance_tier, now_str)
                    self.price_db[key] = record

                record["total_listings_seen"] = int(record.get("total_listings_seen", 0) or 0) + 1
                record["last_seen"] = now_str

                if gold > 0:
                    unit_gold = round(gold / qty, 2)
                    self._update_currency_stats(record["gold_stats"], unit_gold, is_gold=True)
                else:
                    unit_silver = round(silver / qty, 1)
                    self._update_currency_stats(record["silver_stats"], unit_silver, is_gold=False)

                self._mark_seen_unlocked(gid)
                recorded_count += 1

        return recorded_count

    def process_item_listing(
        self,
        name: str,
        grade: int,
        quantity: int,
        gold: int,
        silver: int,
        is_new_listing: bool = False,
        good_id: Optional[int] = None,
        enhance_tier: int = 0,
        live_gold_prices: Optional[List[float]] = None,
        live_silver_prices: Optional[List[float]] = None,
        config: Optional[Dict[str, Any]] = None,
        record_sample: bool = True,
        record_weight: int = 1
    ) -> Dict[str, Any]:
        """
        Đánh giá lô hàng dựa trên baseline trước khi cập nhật + mặt bằng giá đang bán trực tiếp,
        sau đó mới cập nhật lịch sử giá nếu good_id chưa từng được ghi nhận.
        """
        clean_name = normalize_item_name(name)
        if (
            not clean_name
            or "\x00" in clean_name
            or len(clean_name) > 80
            or quantity <= 0
            or gold < 0
            or silver < 0
            or (gold == 0 and silver == 0)
        ):
            return {
                "name": clean_name,
                "grade": int(grade or 0),
                "enhance_tier": int(enhance_tier or 0),
                "quantity": int(quantity or 0),
                "gold": int(gold or 0),
                "silver": int(silver or 0),
                "unit_gold": 0.0,
                "unit_silver": 0.0,
                "currency": "gold" if (gold or 0) > 0 else "silver",
                "is_first_time": False,
                "is_bargain": False,
                "is_rare": False,
                "discount_percent": 0.0,
                "floor_discount_percent": 0.0,
                "estimated_profit_gold": 0.0,
                "estimated_profit_silver": 0.0,
                "reasons": [],
                "market_gold_avg": 0.0,
                "market_gold_median": 0.0,
                "market_gold_min": None,
                "live_gold_floor": None,
                "market_silver_avg": 0.0,
                "market_silver_median": 0.0,
                "market_silver_min": None,
                "live_silver_floor": None,
                "ref_samples": 0,
                "total_seen": 0
            }

        cfg = config or {}
        min_discount_pct = float(cfg.get("min_discount_pct", 30.0))
        min_floor_discount_pct = float(cfg.get("min_floor_discount_pct", 15.0))
        min_profit_gold = float(cfg.get("min_profit_gold", 10.0))
        min_samples = int(cfg.get("min_samples", 3))
        silver_per_gold = float(cfg.get("silver_per_gold", 50000.0))
        min_profit_silver = min_profit_gold * silver_per_gold

        qty = max(1, int(quantity))
        is_gold_listing = gold > 0
        unit_gold = round(gold / qty, 2) if is_gold_listing else 0.0
        unit_silver = round(silver / qty, 1) if (not is_gold_listing and silver > 0) else 0.0

        norm_tier = 1 if int(enhance_tier or 0) > 0 else 0
        key = self._get_item_key(clean_name, grade, norm_tier)

        with self.lock:
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            record = self.price_db.get(key)
            is_first_time = record is None

            is_bargain = False
            discount_percent = 0.0
            floor_discount_percent = 0.0
            estimated_profit_gold = 0.0
            estimated_profit_silver = 0.0
            reasons: List[str] = []
            ref_samples = 0

            live_g_list = [float(p) for p in (live_gold_prices or []) if p and p > 0]
            live_s_list = [float(p) for p in (live_silver_prices or []) if p and p > 0]
            live_gold_floor: Optional[float] = min(live_g_list) if live_g_list else None
            live_silver_floor: Optional[float] = min(live_s_list) if live_s_list else None

            g_stats_snap = (record or {}).get("gold_stats", self._empty_currency_stats())
            s_stats_snap = (record or {}).get("silver_stats", self._empty_currency_stats())

            # 1. ĐÁNH GIÁ TRÊN BASELINE TRƯỚC KHI CẬP NHẬT
            if is_gold_listing:
                (
                    is_bargain,
                    discount_percent,
                    floor_discount_percent,
                    estimated_profit_gold,
                    _ref_p,
                    _h_med,
                    _l_fl,
                    ref_samples,
                    reasons
                ) = self._evaluate_same_currency_bargain(
                    unit_price=unit_gold,
                    qty=qty,
                    c_stats=g_stats_snap,
                    live_prices_raw=live_g_list,
                    min_discount_pct=min_discount_pct,
                    min_floor_discount_pct=min_floor_discount_pct,
                    min_profit=min_profit_gold,
                    min_samples=min_samples,
                    unit_label="V/món"
                )
            else:
                # Giao dịch thuần Bạc (gold == 0 and silver > 0)
                # 2A: Phát hiện bán hớ bằng Bạc cho vật phẩm thường bán bằng Vàng (>= 20V)
                equiv_gold = unit_silver / silver_per_gold if silver_per_gold > 0 else 0.0
                g_samples = int(g_stats_snap.get("sample_count", 0) or 0)
                g_med = float(g_stats_snap.get("median_unit_price", 0.0) or 0.0) if g_samples >= min_samples else 0.0
                live_g_med = self._calculate_median(live_g_list) if len(live_g_list) >= 2 else 0.0

                if g_med >= 20.0 or live_g_med >= 20.0:
                    if g_med >= 20.0 and live_g_med >= 20.0:
                        ref_gold = min(g_med, live_g_med)
                    elif g_med >= 20.0:
                        ref_gold = g_med
                    else:
                        ref_gold = live_g_med

                    ref_g_samples = max(g_samples, len(live_g_list))
                    profit_g = round((ref_gold - equiv_gold) * qty, 1)
                    if (
                        equiv_gold > 0
                        and ref_gold >= 20.0
                        and equiv_gold <= ref_gold * 0.50
                        and profit_g >= min_profit_gold
                        and (live_gold_floor is None or equiv_gold <= live_gold_floor * 0.70)
                    ):
                        is_bargain = True
                        discount_percent = round((1.0 - equiv_gold / ref_gold) * 100.0, 1)
                        if live_gold_floor and live_gold_floor > 0:
                            floor_discount_percent = max(0.0, round((1.0 - equiv_gold / live_gold_floor) * 100.0, 1))
                        estimated_profit_gold = max(0.0, profit_g)
                        ref_samples = ref_g_samples
                        reasons.append(
                            f"Bán hớ bằng Bạc (~{equiv_gold:,.1f} V/món so với giá Vàng trung vị {ref_gold:,.1f} V/món)"
                        )

                # 2B: Đánh giá cùng hệ Bạc nếu chưa kích hoạt bán hớ chéo tiền tệ
                if not is_bargain:
                    (
                        is_bargain,
                        discount_percent,
                        floor_discount_percent,
                        estimated_profit_silver,
                        _ref_ps,
                        _hs_med,
                        _ls_fl,
                        ref_samples,
                        reasons
                    ) = self._evaluate_same_currency_bargain(
                        unit_price=unit_silver,
                        qty=qty,
                        c_stats=s_stats_snap,
                        live_prices_raw=live_s_list,
                        min_discount_pct=min_discount_pct,
                        min_floor_discount_pct=min_floor_discount_pct,
                        min_profit=min_profit_silver,
                        min_samples=min_samples,
                        unit_label="Bạc/món"
                    )

            # 2. CẬP NHẬT LỊCH SỬ SAU KHI ĐÁNH GIÁ (nếu chưa ghi nhận good_id này)
            already_seen = good_id is not None and int(good_id) in self._seen_good_ids_set
            if record_sample and not already_seen:
                if record is None:
                    record = self._new_record(clean_name, grade, norm_tier, now_str)
                    self.price_db[key] = record

                record["total_listings_seen"] = int(record.get("total_listings_seen", 0) or 0) + 1
                record["last_seen"] = now_str

                if is_gold_listing:
                    self._update_currency_stats(record["gold_stats"], unit_gold, is_gold=True, weight=record_weight)
                else:
                    self._update_currency_stats(record["silver_stats"], unit_silver, is_gold=False, weight=record_weight)

                if good_id is not None:
                    self._mark_seen_unlocked(good_id)
            elif good_id is not None and not already_seen:
                # Đánh dấu đã thấy nếu caller không muốn ghi vào thống kê giá
                self._mark_seen_unlocked(good_id)

            final_rec = record or self._new_record(clean_name, grade, norm_tier, now_str)
            g_out = final_rec.get("gold_stats", {})
            s_out = final_rec.get("silver_stats", {})

            return {
                "name": clean_name,
                "grade": int(grade),
                "enhance_tier": norm_tier,
                "quantity": qty,
                "gold": int(gold),
                "silver": int(silver),
                "unit_gold": unit_gold,
                "unit_silver": unit_silver,
                "currency": "gold" if is_gold_listing else "silver",
                "is_first_time": is_first_time,
                "is_bargain": is_bargain,
                "is_rare": False,  # Đã loại bỏ theo thiết kế mới (dùng watchlist_keywords thay thế)
                "discount_percent": discount_percent,
                "floor_discount_percent": floor_discount_percent,
                "estimated_profit_gold": estimated_profit_gold,
                "estimated_profit_silver": estimated_profit_silver,
                "reasons": reasons,
                "market_gold_avg": g_out.get("avg_unit_price", 0.0),
                "market_gold_median": g_out.get("median_unit_price", 0.0),
                "market_gold_min": g_out.get("min_unit_price"),
                "live_gold_floor": live_gold_floor,
                "market_silver_avg": s_out.get("avg_unit_price", 0.0),
                "market_silver_median": s_out.get("median_unit_price", 0.0),
                "market_silver_min": s_out.get("min_unit_price"),
                "live_silver_floor": live_silver_floor,
                "ref_samples": ref_samples,
                "total_seen": final_rec.get("total_listings_seen", 0)
            }

    def save_checkpoint(self):
        """Lưu trạng thái cơ sở dữ liệu giá và good_id đã thấy ra đĩa."""
        with self.lock:
            self._save_database()

    def query_item_price(self, search_keyword: str) -> List[Dict[str, Any]]:
        """
        Tra cứu thống kê giá của vật phẩm theo từ khóa (dùng cho lệnh /gia của Telegram Bot).
        """
        kw = normalize_item_name(search_keyword).lower()
        if not kw:
            return []

        results = []
        with self.lock:
            for key, data in self.price_db.items():
                name = normalize_item_name(data.get("name", ""))
                if kw in name.lower():
                    results.append(data)

        results.sort(key=lambda x: -int(x.get("total_listings_seen", 0) or 0))
        return results[:10]
