# -*- coding: utf-8 -*-
"""engine/inspect_av.py — 합성 mp4(lavfi)로 항목별 fail/통과, 종료 코드, 스키마 키 검증."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sf  # noqa: E402  (engine 경로 등록)

import inspect_av  # noqa: E402

FFMPEG_BIN = r"C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin"
if os.path.isdir(FFMPEG_BIN) and FFMPEG_BIN not in os.environ["PATH"]:
    os.environ["PATH"] = FFMPEG_BIN + os.pathsep + os.environ["PATH"]

import shutil  # noqa: E402

HAS_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
W, H = 270, 480   # 캔버스 1080x1920 의 1/4 — 영상 영역은 y 120~272

PLAN = {
    "version": 1,
    "meta": {"template": "topbottom", "rule_set": ["common"]},
    "cuts": [
        {"no": 1, "kind": "dialog", "src_start": "00:00.0", "src_end": "00:03.0"},
        {"no": 2, "kind": "dialog", "src_start": "00:10.0", "src_end": "00:13.0",
         "sensitive": {"type": "x", "method": "mute", "audio": "mute"}},
        {"no": 3, "kind": "dialog", "src_start": "00:20.0", "src_end": "00:23.0"},
    ],
}


def make_mp4(path, black=None, top_black=None, silence=(), amp=0.5):
    """9초(30fps). black=(첫 프레임, 끝 프레임) 영상 영역 검은색, top_black 은 영역 밖(레터박스), silence=[(s,e)] 초."""
    vf = [f"testsrc2=s={W}x{H}:r=30:d=9"]
    if black:
        vf.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,{black[0]},{black[1]})'")
    if top_black:
        vf.append(f"drawbox=x=0:y=0:w=iw:h=100:color=black:t=fill:enable='between(n,{top_black[0]},{top_black[1]})'")
    af = f"sine=f=440:d=9,volume={amp}"
    for s, e in silence:
        af += f",volume=enable='between(t,{s},{e})':volume=0"
    _sf.ffmpeg("-f", "lavfi", "-i", ",".join(vf), "-f", "lavfi", "-i", af, "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-shortest", path)


@unittest.skipUnless(HAS_FF, "ffmpeg 없음")
class InspectCase(_sf.ProjectCase):
    def run_inspect(self, **kw):
        self.write_project(PLAN, srt=[])
        mp4 = self.dir / "숏폼_상하템플릿.mp4"
        make_mp4(mp4, **kw)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = inspect_av.inspect_output(self.load(), mp4)
        f = self.dir / "점검결과.json"
        return rc, (json.loads(f.read_text(encoding="utf-8")) if f.exists() else None)

    def level(self, res, id_):
        return next(it for it in res["items"] if it["id"] == id_)

    def test_clean_passes_and_schema(self):
        rc, res = self.run_inspect(silence=[(3.5, 5.5)])   # 무음은 mute 컷(3~6초) 안
        self.assertEqual(rc, 0, res["summary"])
        self.assertEqual(res["schema"], 1)
        self.assertEqual(res["plan_hash"], self.load()["_hash"])
        self.assertEqual(res["output"]["file"], "숏폼_상하템플릿.mp4")
        self.assertEqual(res["output"]["size"], (self.dir / "숏폼_상하템플릿.mp4").stat().st_size)
        self.assertEqual([it["id"] for it in res["items"]],
                         ["loudness", "true_peak", "silence", "first_sound", "black_frames"])
        for k in ("ok", "warn", "fail", "auto_fixes", "fail_reason"):
            self.assertIn(k, res["summary"])
        self.assertIsNone(res["summary"]["fail_reason"])
        self.assertEqual(self.level(res, "silence")["excluded"][0]["why"], "mute")

    def test_silence_half_second_fails(self):
        rc, res = self.run_inspect(silence=[(7.0, 7.5)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.level(res, "silence")["level"], "fail")
        self.assertIn("무음", res["summary"]["fail_reason"])

    def test_silence_short_passes(self):
        rc, res = self.run_inspect(silence=[(7.0, 7.2)])
        self.assertEqual(self.level(res, "silence")["level"], "ok")

    def test_first_sound_delay_fails(self):
        rc, res = self.run_inspect(silence=[(0, 0.2)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.level(res, "first_sound")["level"], "fail")

    def test_first_sound_within_limit_passes(self):
        rc, res = self.run_inspect(silence=[(0, 0.08)])
        self.assertEqual(self.level(res, "first_sound")["level"], "ok")

    def test_one_black_frame_fails(self):
        """plan §7: blackdetect d=1/fps 가 1프레임을 잡는지."""
        rc, res = self.run_inspect(black=(100, 100))
        self.assertEqual(rc, 1)
        it = self.level(res, "black_frames")
        self.assertEqual(it["level"], "fail")
        self.assertAlmostEqual(it["at"][0]["start"], 100 / 30, places=2)

    def test_black_frame_detected_at_24_and_60fps(self):
        for fps in (24, 60):
            f = self.dir / f"b{fps}.mp4"
            _sf.ffmpeg("-f", "lavfi", "-i",
                       f"testsrc2=s=64x64:r={fps}:d=2,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='eq(n,{fps})'",
                       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", f)
            self.assertEqual(len(inspect_av.black_frames(f, (0, 0, 64, 64), fps)), 1, fps)

    def test_dynamic_or_out_of_tolerance_fix_warns(self):
        for fix in ({"mode": "dynamic"}, {"mode": "linear", "out_of_tolerance": True}):
            self.write_project(PLAN, srt=[])
            (self.dir / "작업기록.json").write_text(json.dumps({"schema": 1, "auto_fixes": [fix]}), encoding="utf-8")
            rc, res = self.run_inspect(silence=[(3.5, 5.5)], amp=1.0)
            self.assertEqual(rc, 0)
            self.assertEqual(self.level(res, "loudness")["level"], "warn")
            self.assertIn("작업기록", self.level(res, "loudness")["msg"])
            self.assertEqual(res["summary"]["auto_fixes"], 1)

    def test_letterbox_black_is_ignored(self):
        rc, res = self.run_inspect(top_black=(30, 120))
        self.assertEqual(self.level(res, "black_frames")["level"], "ok")

    def test_true_peak_over_limit_fails(self):
        rc, res = self.run_inspect(amp=8.0, silence=[(3.5, 5.5)])
        self.assertEqual(self.level(res, "true_peak")["level"], "fail")
        rc, res = self.run_inspect(amp=4.0)
        self.assertEqual(self.level(res, "true_peak")["level"], "ok")

    def test_missing_mp4_is_rc2_and_no_file(self):
        self.write_project(PLAN, srt=[])
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            rc = inspect_av.inspect_output(self.load(), self.dir / "없음.mp4")
        self.assertEqual(rc, 2)
        self.assertFalse((self.dir / "점검결과.json").exists())

    def test_cli_standalone(self):
        import subprocess
        self.write_project(PLAN, srt=[])
        make_mp4(self.dir / "숏폼_상하템플릿.mp4", silence=[(7.0, 7.5)])
        r = subprocess.run([sys.executable, str(_sf.ENGINE / "inspect_av.py"), str(self.plan_path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
