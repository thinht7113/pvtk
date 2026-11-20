"""
PVTK AI-Powered Auto Captcha Solver Module
Uses ddddocr (deep learning OCR engine based on ONNX/CNN) to automatically 
solve PVTK 4-6 character distorted text captchas in 0.01 - 0.02 seconds.
Provides fallback mechanisms if OCR is unavailable or multiple retries fail.
"""

import os
import time
from typing import Optional

# Global cached OCR instance (lazy loaded)
_OCR_INSTANCE = None
_OCR_LOAD_ATTEMPTED = False


class CaptchaSolver:
    @classmethod
    def get_ocr(cls):
        global _OCR_INSTANCE, _OCR_LOAD_ATTEMPTED
        if _OCR_INSTANCE is not None:
            return _OCR_INSTANCE
        if _OCR_LOAD_ATTEMPTED:
            return None

        _OCR_LOAD_ATTEMPTED = True
        try:
            import ddddocr
            # Initialize with show_ad=False and beta=False for standard character classification
            _OCR_INSTANCE = ddddocr.DdddOcr(show_ad=False, beta=False)
            return _OCR_INSTANCE
        except Exception as e:
            # ddddocr not installed or failed to load
            return None

    @classmethod
    def solve_bytes(cls, image_bytes: bytes) -> Optional[str]:
        """
        Takes raw image bytes (JPEG / PNG / BMP) directly from the PVTK game packet,
        passes them through AI OCR, and returns the sanitized text string (e.g. '2LQDk').
        """
        if not image_bytes or len(image_bytes) < 10:
            return None

        ocr = cls.get_ocr()
        if ocr:
            try:
                raw_text = ocr.classification(image_bytes)
                if raw_text:
                    # Sanitize: strip whitespace and keep only alphanumeric chars
                    cleaned = "".join([c for c in raw_text.strip() if c.isalnum()])
                    if cleaned:
                        return cleaned
            except Exception:
                pass

        # Fallback: Save to storage for debugging or manual review
        try:
            storage_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")
            os.makedirs(storage_dir, exist_ok=True)
            save_path = os.path.join(storage_dir, "last_captcha.png")
            with open(save_path, "wb") as f:
                f.write(image_bytes)
        except Exception:
            pass

        return None

    @classmethod
    def solve_file(cls, image_path: str) -> Optional[str]:
        """Solves a captcha from a local image file."""
        if not os.path.exists(image_path):
            return None
        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            return cls.solve_bytes(img_bytes)
        except Exception:
            return None
