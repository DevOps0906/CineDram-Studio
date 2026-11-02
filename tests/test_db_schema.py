"""db/migrations 스키마·db/migrate.py 적용기 테스트 (임시 DB, 표준 라이브러리만)."""
import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

SECRET_WORDS = ("password", "token", "secret", "api_key")


class DbTestBase(unittest.TestCase):
    def setUp(self):
        # 한국어 경로에서도 동작하는지 함께 확인한다.
        self.tmp = Path(tempfile.mkdtemp(prefix="디비_테스트_"))
        self.db = self.tmp / "작업 데이터.sqlite3"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conn(self):
        c = migrate_mod.connect(self.db)
        self.addCleanup(c.close)
        return c

    def seed_job(self, c):
        c.execute("INSERT INTO works(title) VALUES ('테스트 작품')")
        c.execute("INSERT INTO sources(work_id, file_path, origin) VALUES (1, 'a.mp4', 'local_file')")
        c.execute("INSERT INTO jobs(source_id) VALUES (1)")
        return 1


class MigrateTest(DbTestBase):
    def test_idempotent(self):
        self.assertEqual(migrate_mod.migrate(self.db), ["0001", "0002", "0003"])
        self.assertEqual(migrate_mod.migrate(self.db), [])
        rows = self.conn().execute("SELECT version, checksum FROM schema_migrations").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], "0001")

    def test_checksum_tamper_aborts(self):
        mig = self.tmp / "migrations"
        mig.mkdir()
        f = mig / "0001_t.sql"
        f.write_text("CREATE TABLE t (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
        self.assertEqual(migrate_mod.migrate(self.db, mig), ["0001"])
        f.write_text("CREATE TABLE t (id INTEGER PRIMARY KEY, x TEXT);\n", encoding="utf-8")
        (mig / "0002_u.sql").write_text("CREATE TABLE u (id INTEGER);\n", encoding="utf-8")
        with self.assertRaises(migrate_mod.MigrationError):
            migrate_mod.migrate(self.db, mig)
        tables = {r[0] for r in self.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("u", tables)  # 중단 시 뒤 파일도 적용하지 않는다

    def test_failed_migration_rolls_back(self):
        mig = self.tmp / "migrations"
        mig.mkdir()
        (mig / "0001_bad.sql").write_text(
            "CREATE TABLE ok_t (id INTEGER);\nCREATE TABLE broken (;\n", encoding="utf-8")
        with self.assertRaises(migrate_mod.MigrationError):
            migrate_mod.migrate(self.db, mig)
        c = self.conn()
        self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='ok_t'").fetchone())
        self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 0)

    def test_cli(self):
        self.assertEqual(migrate_mod.main([str(self.db)]), 0)
        self.assertEqual(migrate_mod.main([]), 2)


class SchemaTest(DbTestBase):
    def setUp(self):
        super().setUp()
        migrate_mod.migrate(self.db)
        self.c = self.conn()

    def test_check_constraints(self):
        jid = self.seed_job(self.c)
        bad = [
            "UPDATE jobs SET state='unknown' WHERE id=1",
            "UPDATE jobs SET queue_state='done' WHERE id=1",
            "UPDATE jobs SET exec_mode='robot' WHERE id=1",
            "UPDATE jobs SET upload_mode='always' WHERE id=1",
            "UPDATE sources SET rights_status='unknown' WHERE id=1",
            "UPDATE sources SET origin='tiktok' WHERE id=1",
            "UPDATE topics SET used=2",
        ]
        self.c.execute("INSERT INTO topics(job_id) VALUES (?)", (jid,))
        for sql in bad:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.c.execute(sql)

    def test_defaults(self):
        self.seed_job(self.c)
        row = self.c.execute(
            "SELECT state, queue_state, exec_mode, upload_mode, priority FROM jobs WHERE id=1").fetchone()
        self.assertEqual(row, ("queued", "waiting", "auto_render", "review_first", 0))
        self.assertEqual(self.c.execute("SELECT rights_status FROM sources").fetchone()[0], "unverified")
        # auto_schedule 은 CHECK 로 막지 않는다(허용 조건은 앱 게이트, docs/13 §13 안 A)
        self.c.execute("UPDATE jobs SET upload_mode='auto_schedule' WHERE id=1")

    def test_foreign_keys(self):
        self.assertEqual(self.c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO job_events(job_id, to_state) VALUES (999, 'queued')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO sources(work_id, file_path, origin) VALUES (999, 'x', 'local_file')")
        self.seed_job(self.c)
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("DELETE FROM sources WHERE id=1")  # 작업이 참조 중
        self.assertEqual(self.c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_conditional_claim_only_one_wins(self):
        self.seed_job(self.c)
        a, b = self.conn(), self.conn()
        claim = "UPDATE jobs SET queue_state='running', slot_id=? WHERE id=1 AND queue_state='waiting'"
        a.execute("BEGIN IMMEDIATE")
        # a 가 쓰기 잠금을 쥔 동안 b 는 IMMEDIATE 를 못 연다
        b.execute("PRAGMA busy_timeout = 0")
        with self.assertRaises(sqlite3.OperationalError):
            b.execute("BEGIN IMMEDIATE")
        ra = a.execute(claim, ("gpu-1",)).rowcount
        a.execute("COMMIT")
        b.execute("BEGIN IMMEDIATE")
        rb = b.execute(claim, ("gpu-2",)).rowcount
        b.execute("COMMIT")
        self.assertEqual((ra, rb), (1, 0))
        self.assertEqual(self.c.execute("SELECT slot_id FROM jobs WHERE id=1").fetchone()[0], "gpu-1")

    def test_indexes(self):
        idx = {r[0]: r[1] for r in self.c.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")}
        cols = [r[2] for r in self.c.execute("PRAGMA index_info(idx_jobs_queue)")]
        self.assertEqual(cols, ["queue_state", "priority"])
        for t in ("job_events", "topics", "approvals", "outputs"):
            self.assertIn(t, idx.values())

    def test_no_secret_columns(self):
        tables = [r[0] for r in self.c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        self.assertTrue({"works", "sources", "jobs", "job_events", "topics", "approvals",
                         "outputs", "schema_migrations"} <= set(tables))
        for t in tables:
            for col in self.c.execute(f"PRAGMA table_info({t})"):
                name = col[1].lower()
                for w in SECRET_WORDS:
                    with self.subTest(table=t, column=name):
                        self.assertNotIn(w, name)


if __name__ == "__main__":
    unittest.main()
