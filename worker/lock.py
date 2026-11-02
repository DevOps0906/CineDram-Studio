"""단일 인스턴스 잠금 (D2): OS 파일 잠금. 프로세스가 죽으면 OS 가 풀어 준다."""
import os
import sys


class AlreadyRunning(RuntimeError):
    pass


class SingleInstance:
    def __init__(self, path):
        self.path = str(path)
        self._f = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        f = open(self.path, "a+b")
        try:
            f.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            f.close()
            raise AlreadyRunning("다른 워커가 이미 실행 중이다") from e
        self._f = f
        return self

    def release(self):
        f, self._f = self._f, None
        if f is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
