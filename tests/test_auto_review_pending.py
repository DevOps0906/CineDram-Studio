# -*- coding: utf-8 -*-
"""docs/13 §10 자동 검수 6가지 중 엔진에 아직 없는 검사 — 규칙 ID가 생기고 구현되면 skip 을 풀고 채운다.

규칙 ID가 있는 4가지(C5-9 · P8-3/C6-1 · C3-3 · P8-2)는 test_auto_review.py 로 옮겼다.
"""
import unittest

PENDING = "docs/13 §10 — 엔진 미구현"


class AutoReviewPending(unittest.TestCase):
    @unittest.skip(f"{PENDING}: 음량 측정·정규화 (규칙 ID 없음, LUFS 미확인)")
    def test_loudness_normalized(self):
        pass

    @unittest.skip(f"{PENDING}: 검은 화면 프레임 검출 (규칙 ID 없음)")
    def test_no_black_frames(self):
        pass


if __name__ == "__main__":
    unittest.main()
