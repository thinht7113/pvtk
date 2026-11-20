"""
PVTK Knowledge Base Management Module
Allows viewing, searching, and manually managing stored Q&A pairs in qa_knowledge.json.
"""

import os
import json
from core.config import settings

def load_kb():
    kb_path = os.path.join(settings.storage_dir, "qa_knowledge.json")
    if not os.path.exists(kb_path):
        return {}, kb_path
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            return json.load(f), kb_path
    except Exception:
        return {}, kb_path

def save_kb(data, kb_path):
    with open(kb_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def run_kb_manager():
    while True:
        kb_data, kb_path = load_kb()
        total_q = len(kb_data)

        print("\n" + "=" * 65)
        print(f"       QUẢN LÝ KHO TRI THỨC VẤN ĐÁP (ĐÃ LƯU {total_q} CÂU HỎI)")
        print("=" * 65)
        print("  [1] Xem danh sách toàn bộ câu hỏi & đáp án")
        print("  [2] Tìm kiếm câu hỏi theo từ khóa")
        print("  [3] Thêm / Chỉnh sửa câu hỏi & đáp án thủ công")
        print("  [0] Quay lại Menu chính")
        print("-" * 65)

        choice = input("-> Nhập lựa chọn [0-3]: ").strip()

        if choice == "0":
            break
        elif choice == "1":
            print("\n" + "-" * 65)
            print(f"DANH SÁCH {total_q} CÂU HỎI HIỆN CÓ TRONG KHO TRI THỨC:")
            print("-" * 65)
            for i, (k, item) in enumerate(kb_data.items(), start=1):
                q_text = item.get("question_display", k)
                ans = item.get("correct_answer", "(Chưa có)")
                wrongs = ", ".join(item.get("wrong_answers", []))
                print(f"[{i}] Câu hỏi: {q_text}")
                print(f"    -> ĐÁP ÁN ĐÚNG : \033[92m{ans}\033[0m")
                if wrongs:
                    print(f"    -> Các câu sai : {wrongs}")
                print()
            input("Nhấn Enter để tiếp tục...")

        elif choice == "2":
            kw = input("\n-> Nhập từ khóa cần tìm: ").strip().lower()
            if not kw:
                continue
            found = 0
            print("\n" + "-" * 65)
            for i, (k, item) in enumerate(kb_data.items(), start=1):
                q_text = item.get("question_display", k)
                ans = item.get("correct_answer", "")
                if kw in q_text.lower() or kw in ans.lower() or kw in k:
                    found += 1
                    print(f"[{found}] Câu hỏi: {q_text}")
                    print(f"    -> Đáp án đúng: \033[92m{ans}\033[0m")
                    print()
            if found == 0:
                print(f"Không tìm thấy câu hỏi nào chứa từ khóa '{kw}'.")
            print("-" * 65)
            input("Nhấn Enter để tiếp tục...")

        elif choice == "3":
            print("\n" + "-" * 65)
            print("THÊM / CẬP NHẬT CÂU HỎI THỦ CÔNG")
            print("-" * 65)
            q_input = input("-> Nhập nội dung câu hỏi: ").strip()
            if not q_input:
                continue
            a_input = input("-> Nhập đáp án đúng: ").strip()
            if not a_input:
                continue

            normalized_key = "".join(q_input.lower().split())
            if normalized_key in kb_data:
                kb_data[normalized_key]["correct_answer"] = a_input
                kb_data[normalized_key]["question_display"] = q_input
            else:
                kb_data[normalized_key] = {
                    "question_display": q_input,
                    "correct_answer": a_input,
                    "wrong_answers": [],
                    "correct_fail_count": 0
                }
            save_kb(kb_data, kb_path)
            print(f"\033[92m[THÀNH CÔNG]\033[0m Đã lưu câu hỏi vào kho tri thức.")
            input("Nhấn Enter để tiếp tục...")
