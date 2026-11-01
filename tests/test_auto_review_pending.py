# -*- coding: utf-8 -*-
"""docs/13 §10 자동 검수 6가지 중 엔진에 아직 없는 검사 — 구현되면 skip 을 풀고 채운다."""
import unittest

PENDING = "docs/13 §10 — 엔진 미구현"


class AutoReviewPending(unittest.TestCase):
    @unittest.skip(f"{PENDING}: 실제 음성과 자막 시작을 비교하는 싱크 검사 (C5-9 · #13)")
    def test_subtitle_not_before_voice(self):
        pass

    @unittest.skip(f"{PENDING}: 대사 컷 끝 여유 자동 적용·끝음절 잘림 검사 (P8-3, C6-1, S11-5)")
    def test_dialog_tail_preserved(self):
        pass

    @unittest.skip(f"{PENDING}: 음량 측정·정규화 (규칙 ID 없음, LUFS 미확인)")
    def test_loudness_normalized(self):
        pass

    @unittest.skip(f"{PENDING}: 오디오 무음 구간 검출·첫 소리 0초 (C3-3, A10-9)")
    def test_no_silence_gaps(self):
        pass

    @unittest.skip(f"{PENDING}: 검은 화면 프레임 검출 (규칙 ID 없음)")
    def test_no_black_frames(self):
        pass

    @unittest.skip(f"{PENDING}: 자동 받아쓰기 SRT 경계 확인 문구를 지시서 머리에 기록 (P8-2)")
    def test_auto_srt_warning_header(self):
        pass


if __name__ == "__main__":
    unittest.main()
