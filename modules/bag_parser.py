# modules/bag_parser.py
from typing import Dict, Tuple, Optional
from core.message import Message
from modules.exp_ticket_sender import Item

def parse_all_items_in_bag(bag_bytes: bytes) -> Dict[int, dict]:
    """
    Phân tích gói tin 0x2EE3 (bag_bytes) từ Server.
    Trả về dictionary: { slot_pos: {"item_id": int, "item_name": str, "type": int, "reqLv": int, "grade": int} }
    Hỗ trợ 2 tầng:
      1. Structured Deserialization chuẩn PVTK protocol (Item.from_bytes).
      2. Heuristic Scanner dự phòng nếu gói tin bị khuyết header hoặc phân mảnh.
    """
    items = {}
    if not bag_bytes or len(bag_bytes) < 10:
        return items

    # --- TẦNG 1: Structured Deserialization chuẩn Game PVTK ---
    try:
        msg = Message(0x2EE3, bytearray(bag_bytes))
        code = msg.getByte()
        if code == 0:
            new_map = msg.getInt()
            if new_map >= 20000:
                msg.getShort()
            gx = msg.getByte()
            gy = msg.getByte()
            block_flag = msg.getInt()

            if (block_flag & 0x10) != 0:
                for i in range(32):
                    bid = block_flag & (1 << i)
                    if bid == 0:
                        continue
                    if bid == 0x02: # Block Tiền tệ / Thông tin nhân vật
                        _cid = msg.getInt()
                        _name = msg.getString()
                        msg.offset += 76
                        _gold = msg.getInt()
                        _leaf = msg.getInt()
                        _silver = msg.getInt()
                    elif bid == 0x10: # Block Túi đồ
                        count = msg.getShort()
                        for _ in range(count):
                            it = Item.from_bytes(msg)
                            items[it.slotPos] = {
                                "item_id": it.id,
                                "item_name": it.name,
                                "type": it.type,
                                "reqLv": it.reqLv,
                                "grade": it.grade
                            }
                        if items:
                            return items
                    else:
                        break
    except Exception:
        pass

    # --- TẦNG 2: Fallback Scanner (Quét mẫu chuỗi UTF-16BE) ---
    idx = 0
    while idx < len(bag_bytes) - 6:
        # PVTK Message.putString(): 3 bytes length [0, 0, char_len], sau đó char_len * 2 bytes UTF-16BE
        if bag_bytes[idx] == 0 and bag_bytes[idx+1] == 0 and 1 <= bag_bytes[idx+2] <= 40:
            char_len = bag_bytes[idx+2]
            byte_len = char_len * 2
            start = idx + 3
            end = start + byte_len
            if end <= len(bag_bytes):
                try:
                    name_bytes = bag_bytes[start:end]
                    item_name = name_bytes.decode('utf-16-be')
                    if all(c >= ' ' and c.isprintable() for c in item_name) and len(item_name) >= 2:
                        if idx >= 6:
                            item_id = int.from_bytes(bag_bytes[idx-2:idx], 'big')
                            item_type = bag_bytes[idx-3]
                            status = bag_bytes[idx-4]
                            if (status & 1) == 0:
                                slot_pos = int.from_bytes(bag_bytes[idx-6:idx-4], 'big')
                            else:
                                slot_pos = int.from_bytes(bag_bytes[idx-8:idx-6], 'big')
                            
                            if 0 <= slot_pos < 150 and 0 < item_id < 65535 and 0 < item_type < 40:
                                if slot_pos not in items:
                                    items[slot_pos] = {
                                        "item_id": item_id,
                                        "item_name": item_name,
                                        "type": item_type,
                                        "reqLv": 0,
                                        "grade": 0
                                    }
                except UnicodeDecodeError:
                    pass
        idx += 1

    return items

def get_item_by_slot(bag_bytes: bytes, target_slot: int) -> Tuple[int, str]:
    """Trả về (item_id, item_name) của một slot cụ thể, hoặc (-1, '') nếu không tìm thấy."""
    items = parse_all_items_in_bag(bag_bytes)
    if target_slot in items:
        return items[target_slot]["item_id"], items[target_slot]["item_name"]
    return -1, ""

def get_slot_by_item_id(bag_bytes: bytes, target_item_id: int) -> int:
    """Tìm slot_pos hiện tại đang chứa target_item_id. Trả về -1 nếu không tìm thấy."""
    items = parse_all_items_in_bag(bag_bytes)
    for slot_pos, data in items.items():
        if data["item_id"] == target_item_id:
            return slot_pos
    return -1

def get_slot_by_item_name(bag_bytes: bytes, target_name: str) -> int:
    """Tìm slot_pos hiện tại đang chứa vật phẩm có tên khớp target_name. Trả về -1 nếu không tìm thấy."""
    if not target_name:
        return -1
    items = parse_all_items_in_bag(bag_bytes)
    target_lower = target_name.lower().strip()
    
    # 1. Khớp chính xác 100%
    for slot_pos, data in items.items():
        if data["item_name"].lower().strip() == target_lower:
            return slot_pos
            
    # 2. Khớp chuỗi con
    for slot_pos, data in items.items():
        if target_lower in data["item_name"].lower():
            return slot_pos
            
    return -1
