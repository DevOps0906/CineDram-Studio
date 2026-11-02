# -*- coding: utf-8 -*-
"""docs/13 §10 자동 검수 — 렌더 산출물 검사: C10-4 음량(SHOULD → warn) · C10-5 검은 화면(MUST → error).

엔진 `review_output` 에 합성 영상을 넣어 본다. ffmpeg 가 없으면 검사 자체가 건너뛰어짐(빈 목록)을 확인한다.
"""
import unittest
from unittest import mock

from _sf import HAS_FFMPEG, ProjectCase, ffmpeg, sf


def ids(iss, level=None):
    return [r for lv, r, _ in iss if level in (None, lv)]


def make_clip(path, video, volume=1.0, seconds=3):
    """video: lavfi 영상 소스 문자열. 소리는 1 kHz 사인파에 volume 배율."""
    ffmpeg("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", f"sine=f=1000:d={seconds}",
           "-af", f"volume={volume}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", path)


PATTERN = "testsrc2=s=320x180:r=15:d=3"


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg 없음")
class OutputReview(ProjectCase):
    def test_loudness_normalized(self):
        # 사인파 볼륨 0.01 ≈ 매우 작은 음량 → C10-4 warn (error 아님)
        clip = self.dir / "작은 소리.mp4"
        make_clip(clip, PATTERN, volume=0.01)
        iss = sf.review_output(clip)
        self.assertIn("C10-4", ids(iss, "warn"))
        self.assertEqual(ids(iss, "error"), [])
        self.assertTrue(any("LUFS" in m for _, r, m in iss if r == "C10-4"))

    def test_no_black_frames(self):
        # 앞 1초 검은 화면 + 2초 테스트 패턴 → C10-5 error
        clip = self.dir / "검은 앞.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x180:r=15:d=1", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=15:d=2",
               "-f", "lavfi", "-i", "sine=f=1000:d=3", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
               "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", clip)
        iss = sf.review_output(clip)
        self.assertEqual(ids(iss, "error"), ["C10-5"])
        self.assertEqual(sf.print_issues(iss), 1)

    def test_short_black_and_clean_pass(self):
        # 0.3초 검은 화면(< 0.5초)은 통과, 패턴만 있는 영상도 C10-5 없음
        clip = self.dir / "짧은 검정.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x180:r=15:d=0.3", "-f", "lavfi", "-i", PATTERN,
               "-f", "lavfi", "-i", "sine=f=1000:d=3.3", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
               "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", clip)
        self.assertNotIn("C10-5", ids(sf.review_output(clip)))

    def test_region_ignores_template_margin(self):
        # 위쪽 절반 검정(여백) + 아래쪽 패턴(영상 자리) → 영상 자리만 보면 통과, 여백 자리를 보면 C10-5
        clip = self.dir / "여백.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x360:r=15:d=3", "-f", "lavfi", "-i", PATTERN,
               "-f", "lavfi", "-i", "sine=f=1000:d=3", "-filter_complex", "[0:v][1:v]overlay=0:180[v]",
               "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", clip)
        self.assertNotIn("C10-5", ids(sf.review_output(clip, (320, 180, 0, 180))))
        self.assertIn("C10-5", ids(sf.review_output(clip, (320, 180, 0, 0))))


class OutputReviewSkip(ProjectCase):
    def test_skip_without_ffmpeg(self):
        with mock.patch.object(sf.shutil, "which", return_value=None):
            self.assertEqual(sf.review_output(self.dir / "없는 파일.mp4"), [])


if __name__ == "__main__":
    unittest.main()
