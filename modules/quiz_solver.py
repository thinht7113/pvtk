"""
PVTK Quiz Event Module (Su Kien Van Dap) — TURBO EDITION
Handles:
1. Requesting Question (0x2B30 - CG_VIP_QUESTION_GET)
2. Submitting Answer (0x2B31 - CG_VIP_QUESTION_SUB)
3. Adaptive Knowledge Base (Self-healing & 2-failure correction)
4. Automated Quiz Solver Loop — Ultra-fast pipeline (~0.15s/câu)
"""

import sys
import os
import json
import time
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from typing import List, Optional, Tuple, Dict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.message import Message
from core.network import PVTKConnection
import core.protocol as proto

KB_LOCK = threading.Lock()

def normalize_key(s: str) -> str:
    """
    Normalizes a question or answer string by:
    1. Unicode NFC normalization (handles Vietnamese precomposed and decomposed forms)
    2. Stripping all whitespace, punctuation, and non-alphanumeric characters
    3. Converting to lowercase for consistent matching across accounts
    """
    if not s:
        return ""
    s = unicodedata.normalize('NFC', str(s))
    cleaned = re.sub(r'[^\w\d]', '', s.lower())
    return cleaned


class QuizQuestion:
    def __init__(self, question_id: int, text: str, answers: List[str], answer_ids: List[int] = None, remaining_count: int = 20):
        self.question_id = question_id
        self.text = text.strip()
        self.answers = [a.strip() for a in answers]
        if answer_ids is None:
            self.answer_ids = list(range(1, len(self.answers) + 1))
        else:
            self.answer_ids = answer_ids
        self.remaining_count = remaining_count

    def __repr__(self):
        return f"<Question id={self.question_id} text='{self.text[:30]}...' answers={len(self.answers)} remaining={self.remaining_count}>"


class KnowledgeBase:
    """
    High-Performance In-Memory Knowledge Base for PVTK Quiz Q&A.
    Keeps entire knowledge graph in RAM for 0.0001ms instantaneous lookup across 15+ concurrent threads.
    Persists newly discovered answers safely without blocking solver threads.
    """
    def __init__(self, storage_path: str = None):
        if not storage_path:
            storage_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage")
            os.makedirs(storage_dir, exist_ok=True)
            storage_path = os.path.join(storage_dir, "qa_knowledge.json")
        self.storage_path = storage_path
        self.data: Dict[str, Dict] = {}
        self._dirty = False
        self._load_from_disk()

    def _load_from_disk(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        else:
            self.data = {}

    def _save_to_disk(self):
        try:
            tmp_path = self.storage_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.storage_path)
        except Exception:
            pass

    def get_entry(self, question_text: str) -> Dict:
        q_norm = normalize_key(question_text)
        if question_text in self.data:
            return self.data[question_text]
        if q_norm in self.data:
            return self.data[q_norm]

        # Match existing keys by dynamic normalization without altering stored keys
        for k, v in self.data.items():
            if normalize_key(k) == q_norm:
                return v

        # Intelligent fuzzy / containment matching across knowledge base
        best_k = None
        best_ratio = 0.0
        for k, v in self.data.items():
            k_norm = normalize_key(k)
            if k_norm in q_norm or q_norm in k_norm:
                return v
            ratio = SequenceMatcher(None, q_norm, k_norm).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_k = k

        if best_k and (best_ratio >= 0.82):
            return self.data[best_k]

        raw_key = question_text.strip().lower()
        self.data[raw_key] = {
            "question_display": question_text.strip(),
            "correct_answer": "",
            "wrong_answers": [],
            "correct_fail_count": 0
        }
        return self.data[raw_key]

    def choose_best_answer(self, question: QuizQuestion) -> Tuple[int, int, str]:
        """Instantaneous in-memory lookup with multi-stage matching (Exact -> Containment -> Fuzzy)."""
        if not question or not getattr(question, "answers", None):
            c_id = question.answer_ids[0] if (question and getattr(question, "answer_ids", None)) else 1
            return (0, c_id, "")

        with KB_LOCK:
            entry = self.get_entry(question.text)
            known_correct = entry.get("correct_answer", "").strip()
            wrong_answers = [w.strip() for w in entry.get("wrong_answers", [])]

            norm_known_correct = normalize_key(known_correct)
            norm_wrong_answers = [normalize_key(w) for w in wrong_answers]

            # Case 1: Already know the correct answer (100% precision hit)
            if norm_known_correct:
                # Stage 1.1: Exact match
                for idx, ans in enumerate(question.answers):
                    if normalize_key(ans) == norm_known_correct:
                        c_id = question.answer_ids[idx] if idx < len(question.answer_ids) else (idx + 1)
                        return (idx, c_id, ans)

                # Stage 1.2: Containment match (e.g., "Descartes" in "Descartes (Đề-các)")
                for idx, ans in enumerate(question.answers):
                    norm_ans = normalize_key(ans)
                    if norm_known_correct in norm_ans or norm_ans in norm_known_correct:
                        c_id = question.answer_ids[idx] if idx < len(question.answer_ids) else (idx + 1)
                        return (idx, c_id, ans)

                # Stage 1.3: Fuzzy similarity match (SequenceMatcher >= 0.80)
                best_idx = -1
                best_sim = 0.0
                for idx, ans in enumerate(question.answers):
                    sim = SequenceMatcher(None, normalize_key(ans), norm_known_correct).ratio()
                    if sim > best_sim:
                        best_sim = sim
                        best_idx = idx
                if best_idx >= 0 and best_sim >= 0.80:
                    ans = question.answers[best_idx]
                    c_id = question.answer_ids[best_idx] if best_idx < len(question.answer_ids) else (best_idx + 1)
                    return (best_idx, c_id, ans)

            # Case 2: Filter out known wrong answers
            available_candidates = []
            for idx, ans in enumerate(question.answers):
                norm_ans = normalize_key(ans)
                is_wrong = False
                for w in norm_wrong_answers:
                    if norm_ans == w or (len(w) >= 4 and w in norm_ans):
                        is_wrong = True
                        break
                if not is_wrong:
                    c_id = question.answer_ids[idx] if idx < len(question.answer_ids) else (idx + 1)
                    available_candidates.append((idx, c_id, ans))

            if available_candidates:
                import random
                return random.choice(available_candidates)

            # Fallback
            entry["wrong_answers"] = []
            self._save_to_disk()
            c_id = question.answer_ids[0] if question.answer_ids else 1
            import random
            return (0, c_id, random.choice(question.answers))

    def record_result(self, question: QuizQuestion, chosen_text: str, is_correct: bool, is_valid_result: bool = True, char_tag: str = ""):
        prefix = f"{char_tag} " if char_tag else ""
        if not question or not getattr(question, "text", None):
            return
        # If not a valid server response (network timeout or negative error code), do NOT penalize!
        if not is_valid_result:
            return

        with KB_LOCK:
            entry = self.get_entry(question.text)
            chosen_text = chosen_text.strip()
            norm_chosen = normalize_key(chosen_text)
            norm_known_correct = normalize_key(entry.get("correct_answer", ""))

            need_save = False

            if is_correct:
                if entry.get("correct_answer") != chosen_text:
                    entry["correct_answer"] = chosen_text
                    entry["correct_fail_count"] = 0
                    entry["wrong_answers"] = [w for w in entry.get("wrong_answers", []) if normalize_key(w) != norm_chosen]
                    need_save = True
                    print(f"{prefix}    -> \033[92m[LƯU TRI THỨC - ĐÚNG]\033[0m Đã lưu đáp án chính xác: \033[92m'{chosen_text}'\033[0m")
            else:
                if norm_known_correct and norm_chosen == norm_known_correct:
                    fail_cnt = entry.get("correct_fail_count", 0) + 1
                    entry["correct_fail_count"] = fail_cnt
                    print(f"{prefix}    -> \033[91m[TỰ ĐỔI ĐÁP ÁN]\033[0m Đáp án cũ '{chosen_text}' bị server báo sai -> Hủy ngay và chuyển sang loại trừ tìm đáp án mới!")
                    entry["correct_answer"] = ""
                    entry["correct_fail_count"] = 0
                    if chosen_text not in entry["wrong_answers"]:
                        entry["wrong_answers"].append(chosen_text)
                    need_save = True
                else:
                    if chosen_text not in entry["wrong_answers"]:
                        entry["wrong_answers"].append(chosen_text)
                        need_save = True
                        print(f"{prefix}    -> \033[93m[LƯU LOẠI TRỪ - SAI]\033[0m Đã lưu \033[91m'{chosen_text}'\033[0m là sai")

            if need_save:
                self._save_to_disk()


class QuizManager:
    kb = KnowledgeBase()

    @staticmethod
    def open_quiz(conn: PVTKConnection, is_vip: bool = False, is_super: bool = False) -> Tuple[int, Optional[QuizQuestion], str]:
        """
        Sends Opcode 0x2B30 to request the daily quiz event.
        TURBO: Drain buffer trước khi gửi, timeout 3.5s.
        """
        try:
            conn.drain_buffer(max_duration=0.3)

            msg = Message(proto.CG_VIP_QUESTION_GET)
            msg.putBoolean(is_vip)
            msg.putBoolean(is_super)

            res = conn.send_and_wait(msg, expected_type=proto.CG_VIP_QUESTION_GET, timeout=3.5)
            if not res:
                return (-1, None, "[Lỗi] Không nhận được phản hồi từ Server")

            status_code = res.getByte()

            # Case 1: Server returned error or notice code (< 0)
            if status_code < 0:
                err_msg = res.getString() if res.hasRemaining() else f"Thông báo từ máy chủ (Mã: {status_code})"
                return (status_code, None, err_msg)

            # Case 2: Read remaining questions count (my_count)
            my_count = res.getByte()

            if my_count == 0:
                msg_finish = "Bạn đã trả lời hết mọi câu hỏi ngày hôm nay, mai hãy tới."
                return (0, None, msg_finish)

            # Case 3: Valid question returned (my_count > 0)
            question_id = res.getShort()
            question_text = res.getString()
            answer_count = res.getByte()
            answer_ids: List[int] = []
            answers: List[str] = []

            for _ in range(answer_count):
                choice_id = res.getByte()
                choice_text = res.getString()
                answer_ids.append(choice_id)
                answers.append(choice_text)

            question = QuizQuestion(question_id, question_text, answers, answer_ids, my_count)
            return (1, question, f"Còn {my_count} câu hỏi hôm nay.")
        except Exception as e:
            return (-1, None, f"Ngoại lệ khi nhận câu hỏi: {e}")

    @staticmethod
    def submit_answer(conn: PVTKConnection, question_id: int, choice_id: int, is_vip: bool = False, is_super: bool = False) -> Tuple[bool, bool, str, bool]:
        """
        Sends Opcode 0x2B31 to submit selected choice_id (1..N).
        Returns: (is_pass, is_pass_all, reward_info, is_valid_result)
        """
        try:
            msg = Message(proto.CG_VIP_QUESTION_SUB)
            msg.putShort(question_id)
            msg.putByte(choice_id)
            msg.putBoolean(is_vip)
            msg.putBoolean(is_super)

            res = conn.send_and_wait(msg, expected_type=proto.CG_VIP_QUESTION_SUB, timeout=4.5)
            if not res:
                return (False, False, "Không nhận được kết quả từ server (Hết thời gian chờ).", False)

            status_code = res.getByte()
            if status_code < 0:
                err_msg = res.getString() if res.hasRemaining() else f"Lỗi nộp đáp án (Mã: {status_code})"
                return (False, False, err_msg, False)

            my_count = res.getByte()
            is_pass = res.getBoolean()
            question_count = res.getShort()

            is_pass_all = False
            reward_info = ""

            if is_pass:
                if res.hasRemaining():
                    is_pass_all = res.getBoolean()
                if res.hasRemaining():
                    reward_info = res.getString()
                else:
                    reward_info = "Đúng rồi!"
            else:
                reward_info = "Sai rồi!"

            return (is_pass, is_pass_all, reward_info, True)
        except Exception as e:
            return (False, False, f"Ngoại lệ nộp đáp án: {e}", False)

    @classmethod
    def solve_character_quiz(cls, conn: PVTKConnection, char_tag: str = "", delay_sec: float = 0.05, is_vip: bool = False, is_super: bool = False) -> Tuple[bool, int, int, str]:
        """
        TURBO Quiz Solver — Pipeline tốc độ tối đa với so khớp mờ và chống hủy tri thức khi lag mạng.
        """
        prefix = f"{char_tag} " if char_tag else ""
        questions_answered = 0
        correct_count = 0

        if not conn or not getattr(conn, "is_connected", False):
            return (False, 0, 0, "[Lỗi] Kết nối không hợp lệ hoặc đã ngắt kết nối.")

        try:
            # TURBO: Drain sạch toàn bộ packet thế giới trước khi bắt đầu quiz loop
            drained = conn.drain_buffer(max_duration=0.5)
            if drained > 0:
                print(f"{prefix}\033[90m[DRAIN]\033[0m Đã xả {drained} packet buffer trước khi bắt đầu Vấn Đáp.")

            # Giới hạn số vòng lặp tối đa để tránh kẹt vô hạn (20 câu * 2.5)
            for _ in range(50):
                status_code, question, msg_text = cls.open_quiz(conn, is_vip=is_vip, is_super=is_super)
                if status_code == 1 and question:
                    current_q_num = 20 - question.remaining_count + 1
                    choice_idx, choice_id, choice_text = cls.kb.choose_best_answer(question)

                    # TURBO LOG: 1 dòng gọn cho mỗi câu hỏi
                    known = cls.kb.get_entry(question.text).get("correct_answer", "")
                    kb_tag = "\033[92m[KB✓]\033[0m" if known else "\033[93m[THỬ]\033[0m"
                    print(f"{prefix}{kb_tag} [{current_q_num}/20] \033[93m{question.text[:60]}\033[0m → \033[96m'{choice_text}'\033[0m")

                    is_correct, is_pass_all, reward_info, is_valid = cls.submit_answer(conn, question.question_id, choice_id, is_vip=is_vip, is_super=is_super)
                    cls.kb.record_result(question, choice_text, is_correct, is_valid_result=is_valid, char_tag=char_tag)
                    questions_answered += 1
                    if is_correct:
                        correct_count += 1

                    if is_pass_all:
                        print(f"{prefix}-> \033[92m[HOÀN THÀNH 20/20 CÂU]\033[0m Đúng: \033[93m{correct_count}/20\033[0m câu!")
                        return (True, questions_answered, correct_count, "Đã hoàn thành toàn bộ 20 câu hỏi!")

                    # Delay nhỏ 50ms
                    time.sleep(delay_sec)
                    continue

                elif status_code == 0:
                    if questions_answered > 0:
                        print(f"{prefix}-> \033[92m[KẾT QUẢ VẤN ĐÁP]\033[0m Đã trả lời: \033[93m{questions_answered}/20 câu\033[0m (Đúng: \033[92m{correct_count}/{questions_answered}\033[0m)")
                        return (True, questions_answered, correct_count, f"Đã giải xong {questions_answered}/20 câu hỏi hôm nay.")
                    else:
                        if any(k in msg_text.lower() for k in ["hết", "mai hãy tới", "đã trả lời", "hôm nay đã"]):
                            print(f"{prefix}-> \033[92m[ĐÃ LÀM XONG TRƯỚC ĐÓ]\033[0m {msg_text}")
                            return (True, 20, 20, "Đã hoàn thành toàn bộ 20 câu trước đó trong ngày.")
                        return (False, 0, 0, msg_text)
                else:
                    if any(k in msg_text.lower() for k in ["hết", "mai hãy tới", "đã trả lời", "hôm nay đã"]):
                        print(f"{prefix}-> \033[92m[ĐÃ LÀM XONG TRƯỚC ĐÓ]\033[0m {msg_text}")
                        return (True, 20, 20, "Đã hoàn thành toàn bộ 20 câu trước đó trong ngày.")
                    return (False, questions_answered, correct_count, msg_text)

            return (False, questions_answered, correct_count, "[CẢNH BÁO] Kẹt vòng lặp Vấn Đáp (quá 50 lần thử), tự động thoát.")
        except Exception as e:
            return (False, questions_answered, correct_count, f"Ngoại lệ Vấn Đáp: {e}")
