"""테스트 공통 진입부: 사용자 셸의 CINEDRAM_ROOTS·CINEDRAM_DB 가 테스트 결과를 바꾸지 않게 지운다.

unittest discover 는 모든 테스트 모듈을 먼저 import 한 뒤 실행하므로(그리고 tests/ 는 패키지가 아니라
__init__.py 가 실행되지 않으므로) 여기서 import 시점에 지우면 전체 실행에 적용된다.
환경변수가 필요한 테스트는 각자 설정하고 되돌린다.
"""
import os
import unittest

ENV_KEYS = ("CINEDRAM_ROOTS", "CINEDRAM_DB")
for _k in ENV_KEYS:
    os.environ.pop(_k, None)


class EnvIsolationTest(unittest.TestCase):
    def test_env_cleared(self):
        for k in ENV_KEYS:
            self.assertNotIn(k, os.environ)
