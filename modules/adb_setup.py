import os
import json
import time
from typing import Any, Optional, List
from modules.adb_controller import ADBController

CONFIG_PATH = "config_adb.json"

def get_default_coordinates(adb: ADBController) -> dict:
    """Tự động tính toán tọa độ chuẩn theo độ phân giải màn hình giả lập (chuẩn 540x960)"""
    w, h = adb.get_screen_size()
    sx = w / 540.0
    sy = h / 960.0
    
    return {
        "btn_splash_enter": [int(270 * sx), int(690 * sy)],
        "input_username": [int(270 * sx), int(628 * sy)],
        "input_password": [int(270 * sx), int(680 * sy)],
        "dialog_username": [int(270 * sx), int(102 * sy)],
        "dialog_password": [int(270 * sx), int(220 * sy)],
        "dialog_btn_confirm": [int(135 * sx), int(330 * sy)],
        "dialog_btn_back": [int(400 * sx), int(330 * sy)],
        "btn_login": [int(270 * sx), int(798 * sy)],
        "btn_server_select": [int(270 * sx), int(735 * sy)],
        "char_slot_1": [int(270 * sx), int(430 * sy)],
        "char_slot_2": [int(160 * sx), int(340 * sy)],
        "char_slot_3": [int(380 * sx), int(340 * sy)],
        "char_slot_center": [int(270 * sx), int(430 * sy)],
        "char_slot_left": [int(160 * sx), int(340 * sy)],
        "char_slot_right": [int(380 * sx), int(340 * sy)],
        "btn_enter_game": [int(270 * sx), int(548 * sy)],
        "btn_back_to_login": [int(490 * sx), int(115 * sy)],
        "btn_settings": [int(310 * sx), int(835 * sy)],
        "btn_logout": [int(140 * sx), int(518 * sy)],
        "btn_exit_game": [int(140 * sx), int(592 * sy)],
        "btn_close_popup": [int(490 * sx), int(115 * sy)]
    }

def calibrate_interactive(adb: ADBController):
    w, h = adb.get_screen_size()
    print("===========================================================================")
    print("           ⚙️  CẤU HÌNH TỌA ĐỘ GIẢ LẬP LDPLAYER TỰ ĐỘNG")
    print("===========================================================================")
    print(f"-> Giả lập: \033[96m{adb.device}\033[0m | Độ phân giải: \033[92m{w}x{h}\033[0m")
    print("\n  [1] TỰ ĐỘNG NẠP BỘ TỌA ĐỘ CHUẨN 100% (Khuyên dùng - Đã chuẩn hóa cho PVTK)")
    print("  [2] Thử nghiệm click tự động vào màn hình giả lập (Test trực quan)")
    print("  [3] Nhập tọa độ thủ công bằng tay (Nếu muốn tùy chỉnh riêng)")
    print("---------------------------------------------------------------------------")
    
    choice = input("-> Chọn chế độ [1/2/3] (Nhấn Enter = 1): ").strip()
    defaults = get_default_coordinates(adb)
    
    if choice in ["", "1"]:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=4, ensure_ascii=False)
        print("\n\033[92m[THÀNH CÔNG] ĐÃ TỰ ĐỘNG THIẾT LẬP TOÀN BỘ TỌA ĐỘ CHUẨN!\033[0m")
        print("Các tọa độ đã được lưu vào config_adb.json:")
        labels = {
            "input_username": "Ô mở Dialog Tài khoản",
            "dialog_username": "Ô nhập Tên user (Dialog)",
            "dialog_password": "Ô nhập Mật mã (Dialog)",
            "dialog_btn_confirm": "Nút Đồng ý (Dialog)",
            "btn_login": "Nút Đăng Nhập",
            "char_slot_1": "Nhân vật 1 (Ở giữa)",
            "char_slot_2": "Nhân vật 2 (Bên trái)",
            "char_slot_3": "Nhân vật 3 (Bên phải)",
            "btn_enter_game": "Nút Vào Game",
            "btn_settings": "Menu H.Thống",
            "btn_logout": "Nút Đổi Nhân Vật",
            "btn_exit_game": "Nút Rời Game",
            "btn_back_to_login": "Nút Trở Về (Màn hình chọn NV)"
        }
        for k, name in labels.items():
            if k in defaults:
                print(f"  • {name:<30}: {defaults[k]}")
        print("\n👉 Bạn đã có thể vào thẳng chức năng 12-2 để chạy kịch bản tự động!")
        return True
        
    elif choice == "2":
        print("\n👉 Đang thử nghiệm click các nút trên giả lập...")
        for key in ["btn_login", "char_slot_1", "btn_enter_game"]:
            if key in defaults:
                x, y = defaults[key]
                print(f"  Tap thử nút {key} tại ({x}, {y})...")
                adb.tap(x, y, delay=1.0)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=4, ensure_ascii=False)
        print("\n✅ Đã lưu cấu hình tọa độ vào config_adb.json")
        return True
        
    else:
        # Nhập thủ công
        config = defaults.copy()
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config.update(json.load(f))
            except Exception:
                pass
                
        points = [
            ("input_username", "Ô Tài khoản trên game"),
            ("dialog_username", "Ô Tên user trong Dialog"),
            ("dialog_password", "Ô Mật mã trong Dialog"),
            ("dialog_btn_confirm", "Nút Đồng ý trong Dialog"),
            ("btn_login", "Nút Đăng Nhập"),
            ("char_slot_1", "Nhân vật 1"),
            ("char_slot_2", "Nhân vật 2"),
            ("char_slot_3", "Nhân vật 3"),
            ("btn_enter_game", "Nút Vào Game"),
            ("btn_settings", "Menu H.Thống (Góc dưới)"),
            ("btn_logout", "Nút Đổi Nhân Vật (Trong Menu)"),
            ("btn_exit_game", "Nút Rời Game (Trong Menu)"),
            ("btn_back_to_login", "Nút Trở Về Màn Hình Đăng Nhập")
        ]
        for key, desc in points:
            cur = config.get(key, defaults.get(key, [0, 0]))
            val = input(f"Tọa độ {desc} [Hiện tại: {cur}] (Nhấn Enter để giữ nguyên | Nhập X,Y): ").strip()
            if val and ',' in val:
                try:
                    parts = val.split(',')
                    config[key] = [int(parts[0].strip()), int(parts[1].strip())]
                except Exception:
                    pass
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        print("\n✅ Đã lưu cấu hình tọa độ vào config_adb.json")
        return True

def auto_login_account(adb: ADBController, account_info: Any) -> bool:
    """
    Kịch bản tự động nhập tài khoản và mật khẩu, xử lý cả Dialog Native Android trên PVTK J2ME,
    chờ tải vào màn hình Chọn Nhân Vật.
    Hỗ trợ account_info dạng tuple (user, pass), list [user, pass], hoặc dict {"username": ..., "password": ...}.
    """
    if isinstance(account_info, (tuple, list)):
        username = str(account_info[0]) if len(account_info) > 0 else ""
        password = str(account_info[1]) if len(account_info) > 1 else ""
    elif isinstance(account_info, dict):
        username = str(account_info.get("username", ""))
        password = str(account_info.get("password", ""))
    else:
        username = str(account_info)
        password = ""

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
            
    defaults = get_default_coordinates(adb)
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
            
    print(f"\n🤖 [ADB {adb.device}] Bắt đầu đăng nhập tài khoản: \033[92m{username}\033[0m")
    
    # 0. Mở Game nếu chưa mở (nếu đã mở sẵn ở màn hình Đăng Nhập thì giữ nguyên)
    if not (hasattr(adb, "is_app_foreground") and adb.is_app_foreground()):
        adb.open_app(delay=3.0)
        # Bấm nhẹ nút 'Vào Game' ở màn hình chào nếu có
        splash_pt = cfg.get("btn_splash_enter", [270, 690])
        adb.tap(splash_pt[0], splash_pt[1], delay=1.0)
    else:
        print(f"[ADB {adb.device}] 📱 Game đã đang ở màn hình Đăng Nhập, tiến hành nhập tài khoản trực tiếp...")
    
    # 1. Click vào ô Tài khoản trên Canvas game (hoặc nút cyan Đăng Nhập ở màn hình ban đầu) -> kích hoạt Native Dialog
    user_canvas = cfg.get("input_username", [270, 628])
    adb.tap(user_canvas[0], user_canvas[1], delay=0.8)
    first_login_btn = cfg.get("btn_first_login", [270, 660])
    adb.tap(first_login_btn[0], first_login_btn[1], delay=1.0)
    
    # 2. Nhập Username trong Dialog
    dlg_user = cfg.get("dialog_username", [270, 102])
    adb.tap(dlg_user[0], dlg_user[1], delay=0.5)
    adb.clear_text(backspace_count=20, delay=0.3)
    adb.input_text(username, delay=0.5)
    
    # 3. Nhập Password trong Dialog
    dlg_pwd = cfg.get("dialog_password", [270, 220])
    adb.tap(dlg_pwd[0], dlg_pwd[1], delay=0.5)
    adb.clear_text(backspace_count=20, delay=0.3)
    adb.input_text(password, delay=0.5)
    
    # 4. Bấm 'Đồng ý' trên Dialog để đóng dialog và điền vào Canvas
    dlg_ok = cfg.get("dialog_btn_confirm", [135, 330])
    adb.tap(dlg_ok[0], dlg_ok[1], delay=1.0)
    
    # 5. Bấm nút 'Đăng Nhập' trên Canvas game
    btn_login = cfg.get("btn_login", [270, 798])
    adb.tap(btn_login[0], btn_login[1], delay=1.5)
    
    # 6. Nếu là lần đầu đăng nhập thì game sẽ hiện bảng chọn Server -> chọn máy chủ mặc định (PHỤNG THIÊN)
    srv_pt = cfg.get("server_phung_thien", [140, 250])
    adb.tap(srv_pt[0], srv_pt[1], delay=1.0)
    
    print(f"[ADB {adb.device}] ⏳ Đợi 4 giây để game tải danh sách nhân vật...")
    time.sleep(4.0)
    return True
    
def auto_select_character(
    adb: ADBController,
    char_index: int = 1,
    target_char_id: Optional[int] = None,
    all_chars: Optional[List[dict]] = None,
    last_char_id: Optional[int] = None
) -> bool:
    """
    Chọn Nhân vật 1, 2 hoặc 3 trên giao diện chọn nhân vật và bấm Vào Game.
    Hỗ trợ tính toán vị trí động (Chính giữa, Bên trái, Bên phải) theo cơ chế sortCharListPlayer
    của game, tránh lỗi giao diện tự động hoán đổi nhân vật vừa chơi vào giữa.
    """
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
            
    defaults = get_default_coordinates(adb)
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v

    # Tọa độ 3 vị trí trên màn hình:
    # Child 0: Chính giữa (270, 430)
    # Child 1: Bên trái (160, 340)
    # Child 2: Bên phải (380, 340)
    pos_center = cfg.get("char_slot_center", defaults.get("char_slot_center", [270, 430]))
    pos_left = cfg.get("char_slot_left", defaults.get("char_slot_left", [160, 340]))
    pos_right = cfg.get("char_slot_right", defaults.get("char_slot_right", [380, 340]))

    tap_coord = None
    pos_desc = ""

    # Nếu có danh sách all_chars và target_char_id -> Tính toán vị trí hiển thị động
    if all_chars and target_char_id:
        screen_chars = list(all_chars)
        # Giả lập thuật toán sortCharListPlayer(lastPlayerID) của GameCanvas.smali
        if last_char_id:
            swap_idx = next((i for i, c in enumerate(screen_chars) if c.get("id") == last_char_id), 0)
            if swap_idx > 0:
                screen_chars[0], screen_chars[swap_idx] = screen_chars[swap_idx], screen_chars[0]

        # Tìm vị trí hiển thị hiện tại của target_char_id trên màn hình (0: Giữa, 1: Trái, 2: Phải)
        target_visual_pos = next((i for i, c in enumerate(screen_chars) if c.get("id") == target_char_id), -1)
        if target_visual_pos == 0:
            tap_coord = pos_center
            pos_desc = "Chính Giữa"
        elif target_visual_pos == 1:
            tap_coord = pos_left
            pos_desc = "Bên Trái"
        elif target_visual_pos == 2:
            tap_coord = pos_right
            pos_desc = "Bên Phải"

    if tap_coord:
        print(f"[ADB {adb.device}] 🤖 Chọn nhân vật slot #{char_index} (Vị trí trên màn hình: \033[96m{pos_desc}\033[0m tại {tap_coord})...")
        adb.tap(*tap_coord, delay=1.2)
    else:
        print(f"[ADB {adb.device}] 🤖 Chọn nhân vật slot #{char_index}...")
        char_key = f"char_slot_{char_index}"
        if char_key in cfg:
            adb.tap(*cfg[char_key], delay=1.2)
        
    # Vào Game
    enter_pt = cfg.get("btn_enter_game", [270, 548])
    adb.tap(*enter_pt, delay=2.0)
        
    print(f"[ADB {adb.device}] ✅ Đã bấm Vào Game cho nhân vật #{char_index}")
    return True

def auto_return_to_char_select(adb: ADBController) -> bool:
    """Đăng xuất từ trong Game ra ngoài màn hình Chọn Nhân Vật"""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
            
    defaults = get_default_coordinates(adb)
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
        
    print(f"[ADB {adb.device}] 🤖 Đang trở về màn hình chọn nhân vật...")
    
    # (Đã loại bỏ tính năng bấm nút đóng popup theo yêu cầu)
    # 1. Mở menu H.Thống
    hthong_pt = cfg.get("btn_settings", [295, 860])
    adb.tap(*hthong_pt, delay=1.5)
        
    # 2. Bấm nút 'Nhân vật' để quay lại màn hình chọn NV
    logout_char_pt = cfg.get("btn_logout", [135, 365])
    adb.tap(*logout_char_pt, delay=4.0)
        
    print(f"[ADB {adb.device}] ✅ Đã về màn hình Chọn Nhân Vật.")
    return True

def auto_exit_game_world(adb: ADBController) -> bool:
    """Rời game từ trong thế giới ra thẳng màn hình đăng nhập"""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
            
    defaults = get_default_coordinates(adb)
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
        
    print(f"[ADB {adb.device}] 🚪 Đang rời game ra màn hình Đăng nhập...")
    
    # (Đã loại bỏ tính năng bấm nút đóng popup theo yêu cầu)
    # 1. Mở Cài đặt
    hthong_pt = cfg.get("btn_settings", [295, 860])
    adb.tap(*hthong_pt, delay=1.5)
        
    # 2. Bấm nút 'Rời game'
    exit_game_pt = cfg.get("btn_exit_game", [140, 592])
    adb.tap(*exit_game_pt, delay=4.0)
        
    print(f"[ADB {adb.device}] ✅ Đã về màn hình Đăng Nhập.")
    return True

def auto_logout_account(adb: ADBController) -> bool:
    """Lui từ màn hình Chọn Nhân Vật ra ngoài màn hình Đăng Nhập Tài Khoản"""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
            
    defaults = get_default_coordinates(adb)
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
        
    print(f"[ADB {adb.device}] 🤖 Đang chuyển ra màn hình Đăng Nhập (không tắt game)...")
    # Bấm nút Back ở góc trên bên phải màn hình Chọn NV (490, 115) để ra màn hình Đăng Nhập
    back_pt = cfg.get("btn_back_to_login", [490, 115])
    adb.tap(*back_pt, delay=2.0)
    # adb.press_back(delay=0.5) # FIXED: Removed double back
        
    print(f"[ADB {adb.device}] ✅ Đã về màn hình Đăng Nhập.")
    return True

# Aliases for compatibility
auto_login_flow = auto_login_account
auto_logout_flow = auto_logout_account
