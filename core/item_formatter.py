"""
PVTK Item Colorizer & Quality Formatter Module
Formats item names in console output with ANSI colors matching their exact game grade/quality:
- Grade 0 / 1: Trắng (Phổ Thông / Thường) -> Trắng sáng (\033[97m)
- Grade 2:     Xanh Lá (Ưu Tú / Tinh Xảo)  -> Xanh lá (\033[92m)
- Grade 3:     Xanh Lam (Hiếm / Hoàn Mỹ)   -> Xanh lam Cyan (\033[96m)
- Grade 4:     Tím (Sử Thi / Truyền Thuyết)-> Tím Magenta (\033[95m)
- Grade 5:     Cam (Hoàng Kim / Thần Thoại)-> Vàng Cam (\033[93m)
- Grade 6:     Đỏ (Cực Phẩm / Thần Binh)   -> Đỏ Tươi (\033[91m)
"""

from typing import Dict, Tuple

GRADE_COLOR_MAP: Dict[int, Tuple[str, str]] = {
    0: ("\033[97m", "Trắng"),
    1: ("\033[97m", "Trắng"),
    2: ("\033[92m", "Xanh Lá"),
    3: ("\033[96m", "Xanh Lam"),
    4: ("\033[95m", "Tím"),
    5: ("\033[93m", "Hoàng Kim"),
    6: ("\033[91m", "Đỏ"),
}

def get_item_color(grade: int) -> str:
    """Returns the ANSI color code for the given item grade."""
    return GRADE_COLOR_MAP.get(grade, ("\033[97m", "Trắng"))[0]

def get_grade_name(grade: int) -> str:
    """Returns human-readable name of the item grade."""
    return GRADE_COLOR_MAP.get(grade, ("\033[97m", "Trắng"))[1]

def format_item(name: str, grade: int = 1, quantity: int = 1) -> str:
    """
    Returns beautifully formatted item name with ANSI color matching its grade/quality.
    Example: \033[95m[Mũ Hóa Thân X1] (Tím)\033[0m
    """
    color, grade_label = GRADE_COLOR_MAP.get(grade, ("\033[97m", "Trắng"))
    qty_str = f" x{quantity}" if quantity > 1 else ""
    return f"{color}[{name}{qty_str}]\033[0m {color}({grade_label})\033[0m"

def format_reward_badge(item_name: str, grade: int = 1, quantity: int = 1) -> str:
    """Returns a full reward log badge with icon and colored item string."""
    item_str = format_item(item_name, grade, quantity)
    return f"🎁 \033[92m[NHẬN ĐỒ]\033[0m {item_str}"
