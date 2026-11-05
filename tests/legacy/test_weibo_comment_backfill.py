"""评论回填失败语义测试。

覆盖：

- ``util.comment_fetch``：403 / 网络异常 / 非「无内容」的 ``ok=0`` → 判「失败」；
  ``ok=1`` 空列表、``ok=0``「这里还没有内容」→ 判「无评论」而不是失败。
- ``scripts/weibo_comment_backfill.py``：失败项记「失败」、不写断点、计入连续
  失败熔断；二次运行会重试上次失败项；无评论项正常写断点。"""

import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


def _install_requests_stub() -> None:
    """让 scripts/weibo_comment_backfill.py 能在未安装 requests 的环境被导入。"""
    if "requests" in sys.modules:
        return
    module = types.ModuleType("requests")
    module.Session = object
    module.get = lambda *args, **kwargs: None
    sys.modules["requests"] = module


_install_requests_stub()

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import weibo_comment_backfill as backfill  # noqa: E402
from util.comment import comment_fetch  # noqa: E402
from util.comment import comment_markdown  # noqa: E402
from util.core import file_lock  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    """按 handler 生成响应，并记录每次请求；不联网。"""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append({"url": url, "params": params})
        return self.handler(url, params)


def hotflow_page(items, max_id=0, total_number=0):
    return {
        "ok": 1,
        "data": {
            "data": items,
            "max_id": max_id,
            "max_id_type": 0,
            "total_number": total_number,
            "max": 10,
        },
    }


def raw_comment(cid, text="评论正文", total_number=0):
    return {
        "id": cid,
        "rootid": cid,
        "created_at": "Fri May 03 00:22:41 +0800 2024",
        "text": text,
        "user": {"id": "u" + str(cid), "screen_name": "用户A"},
        "like_count": 1,
        "total_number": total_number,
        "floor_number": 1,
        "pic": {},
    }


def fetch_options(max_retries=0, **kwargs):
    defaults = dict(
        max_root=0,
        max_reply=0,
        max_requests=100,
        delay_min=0,
        delay_max=0,
        include_latest=False,
        timeout=1,
        max_retries=max_retries,
        emoji_replace=True,
        sleep=lambda *_: None,
        rand=lambda: 0.0,
    )
    defaults.update(kwargs)
    return comment_fetch.FetchOptions(**defaults)


def forbidden_handler(url, params):
    return FakeResponse({"ok": 0, "msg": "Forbidden"}, status_code=403)


def empty_page_handler(url, params):
    return FakeResponse(hotflow_page([], max_id=0))


def comment_page_handler(url, params):
    return FakeResponse(hotflow_page([raw_comment("9001")], max_id=0))


def business_no_content_handler(url, params):
    return FakeResponse({"ok": 0, "msg": "这里还没有内容"})


class CommentFetchFailureSemanticsTest(unittest.TestCase):
    """``fetch_comments`` 必须能区分「抓取失败」与「确实没有评论」。"""

    def test_http_403_is_failure(self):
        session = FakeSession(forbidden_handler)
        result = comment_fetch.fetch_comments(session, {}, "101", fetch_options())
        self.assertTrue(result.failed)
        self.assertEqual(result.roots, [])
        self.assertEqual(result.stats.stopped_reason, "error")
        self.assertEqual(result.stats.requests, 1)
        self.assertEqual(result.stats.errors, 1)
        self.assertTrue(any("HTTP 403" in msg for msg in result.stats.error_messages))

    def test_network_exception_is_failure(self):
        def handler(url, params):
            raise RuntimeError("connection reset")

        result = comment_fetch.fetch_comments(
            FakeSession(handler), {}, "101", fetch_options()
        )
        self.assertTrue(result.failed)
        self.assertEqual(result.stats.stopped_reason, "error")

    def test_ok_zero_non_content_message_is_failure(self):
        def handler(url, params):
            return FakeResponse({"ok": 0, "msg": "请求过于频繁"})

        session = FakeSession(handler)
        result = comment_fetch.fetch_comments(
            session, {}, "101", fetch_options(max_retries=1)
        )
        self.assertTrue(result.failed)
        self.assertEqual(result.stats.requests, 2)  # 失败会重试
        self.assertEqual(result.stats.stopped_reason, "error")

    def test_ok_empty_list_is_not_failure(self):
        result = comment_fetch.fetch_comments(
            FakeSession(empty_page_handler), {}, "101", fetch_options()
        )
        self.assertFalse(result.failed)
        self.assertEqual(result.roots, [])
        self.assertEqual(result.stats.stopped_reason, "empty")

    def test_business_no_content_is_not_failure(self):
        session = FakeSession(business_no_content_handler)
        result = comment_fetch.fetch_comments(
            session, {}, "101", fetch_options(max_retries=2)
        )
        self.assertFalse(result.failed)
        self.assertEqual(result.roots, [])
        self.assertEqual(result.stats.stopped_reason, "empty")
        # 明确「没有内容」是业务结论，不重试、不计错误
        self.assertEqual(result.stats.requests, 1)
        self.assertEqual(result.stats.errors, 0)

    def test_reply_error_marks_failure_even_with_partial_roots(self):
        def handler(url, params):
            if url == comment_fetch.REPLY_URL:
                return FakeResponse({"ok": 0, "msg": "Forbidden"}, status_code=403)
            return FakeResponse(
                hotflow_page([raw_comment("100", total_number=2)], max_id=0)
            )

        result = comment_fetch.fetch_comments(
            FakeSession(handler), {}, "101", fetch_options()
        )
        self.assertTrue(result.failed)
        self.assertEqual([root.id for root in result.roots], ["100"])
        self.assertEqual(result.stats.stopped_reason, "error")

    def test_filter_comments_keeps_failed_flag(self):
        result = comment_fetch.fetch_comments(
            FakeSession(forbidden_handler), {}, "101", fetch_options()
        )
        filtered = comment_fetch.filter_comments(result, ["用户A"])
        self.assertTrue(filtered.failed)


class ProcessOneStatusTest(unittest.TestCase):
    """``process_one`` 必须把失败与无评论分成两种状态。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.md_path = self.root / "2024-05-03.md"
        self.md_path.write_text(
            "## 2024-05-03 [用户A] 微博存档\n\n"
            "### 10:00:00\n<!-- weibo_id: 101 -->\n正文\n\n---\n",
            encoding="utf-8",
        )
        self.item = {
            "user": "用户A",
            "weibo_id": "101",
            "created_at": None,
            "md_path": str(self.md_path),
        }
        self.render_options = comment_markdown.RenderOptions()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, session, options=None):
        return backfill.process_one(
            session,
            {},
            self.item,
            options or fetch_options(),
            self.render_options,
            backup=False,
            dry_run=False,
            pic_download=False,
        )

    def test_403_recorded_as_failed_not_empty(self):
        record = self._run(FakeSession(forbidden_handler))
        self.assertEqual(record["status"], "失败")
        self.assertIn("抓取失败", record["note"])
        self.assertNotIn("#### 评论", self.md_path.read_text(encoding="utf-8"))

    def test_empty_result_recorded_as_no_comment(self):
        record = self._run(FakeSession(empty_page_handler))
        self.assertEqual(record["status"], "无评论")

    def test_success_still_writes_comment_block(self):
        record = self._run(FakeSession(comment_page_handler))
        self.assertEqual(record["status"], "已写入")
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("#### 评论", text)
        self.assertIn("<!-- comments: 101", text)


MD_TEMPLATE = (
    "## 2024-05-03 [用户A] 微博存档\n\n"
    "### 10:00:00\n<!-- weibo_id: 101 -->\n正文一\n\n---\n\n"
    "### 10:05:00\n<!-- weibo_id: 102 -->\n正文二\n\n---\n\n"
    "### 10:10:00\n<!-- weibo_id: 103 -->\n正文三\n\n---\n"
)


class BackfillMainFailureTest(unittest.TestCase):
    """回填主循环的端到端语义（stub 会话，临时存档树）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output_dir = self.root / "weibo_data"
        self.user_dir = self.output_dir / "用户A"
        month_dir = self.user_dir / "2024-05"
        month_dir.mkdir(parents=True)
        self.md_path = month_dir / "2024-05-03.md"
        self.md_path.write_text(MD_TEMPLATE, encoding="utf-8")
        self.report_dir = self.output_dir / "report"
        self.config_path = self.root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "weibo_comment_backfill": 1,
                    "output_directory": str(self.output_dir),
                    "comment_request_delay_min": 0,
                    "comment_request_delay_max": 0,
                    "comment_markdown_backup": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 辅助 ----

    @classmethod
    def setUpClass(cls):
        cls._old_log_level = backfill.logger.level
        backfill.logger.setLevel(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        backfill.logger.setLevel(cls._old_log_level)

    def _run(self, session, *extra_args):
        argv = ["--config", str(self.config_path)] + list(extra_args)
        with mock.patch.object(backfill, "build_session", return_value=session):
            return backfill.main(argv)

    def _progress(self):
        # 进度文件按用户分目录：report/<用户>/comment_backfill_progress.json
        path = self.report_dir / "用户A" / "comment_backfill_progress.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _report_text(self):
        reports = sorted(self.report_dir.glob("weibo_comment_backfill_*.txt"))
        self.assertTrue(reports, "回填报告未生成")
        return "\n".join(path.read_text(encoding="utf-8") for path in reports)

    # ---- 用例 ----

    def test_failure_not_marked_done_and_retried_after_recovery(self):
        def failing_handler(url, params):
            if params.get("id") == "101":
                return FakeResponse(hotflow_page([], max_id=0))
            return FakeResponse({"ok": 0, "msg": "Forbidden"}, status_code=403)

        first_session = FakeSession(failing_handler)
        first_code = self._run(first_session)

        # 403 场景：退出码非 0、报告记失败、断点只有「无评论」的 101
        self.assertNotEqual(first_code, 0)
        progress = self._progress()
        self.assertEqual(progress["done"], ["101"])
        first_report = self._report_text()
        self.assertIn("失败: 2 条", first_report)
        self.assertIn("无评论: 1 条", first_report)
        self.assertIn("微博id: 102", first_report)
        self.assertIn("微博id: 103", first_report)
        self.assertNotIn("#### 评论", self.md_path.read_text(encoding="utf-8"))

        # 接口恢复后重跑：只重试上次失败项 102/103，并成功写入
        second_session = FakeSession(comment_page_handler)
        second_code = self._run(second_session)
        self.assertEqual(second_code, 0)
        self.assertTrue(second_session.calls)
        self.assertTrue(
            all(
                call["params"].get("id") in {"102", "103"}
                for call in second_session.calls
            )
        )
        self.assertEqual(self._progress()["done"], ["101", "102", "103"])
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("<!-- comments: 102", text)
        self.assertIn("<!-- comments: 103", text)
        self.assertNotIn("<!-- comments: 101", text)

    def test_ok_empty_marks_done_distinct_from_failure(self):
        session = FakeSession(empty_page_handler)
        code = self._run(session, "--max-weibo", "2")
        self.assertEqual(code, 0)
        self.assertEqual(self._progress()["done"], ["101", "102"])
        report = self._report_text()
        self.assertIn("无评论: 2 条", report)
        self.assertIn("失败: 0 条", report)
        self.assertNotIn("#### 评论", self.md_path.read_text(encoding="utf-8"))

    def test_consecutive_failures_abort_and_keep_report(self):
        session = FakeSession(forbidden_handler)
        code = self._run(session, "--max-consecutive-errors", "2")

        self.assertNotEqual(code, 0)
        # 熔断后第 3 条不再请求：2 条 × (1 次 + 2 次重试) = 6 次
        self.assertEqual(len(session.calls), 6)
        self.assertEqual(self._progress()["done"], [])
        report = self._report_text()
        self.assertIn("本次处理: 2 条", report)
        self.assertIn("失败: 2 条", report)
        self.assertNotIn("#### 评论", self.md_path.read_text(encoding="utf-8"))


class ResolveAllowedUsersTest(unittest.TestCase):
    """白名单解析：txt 文件解析结果是 set，内联配置是 list/tuple，两种都必须认。"""

    def test_set_from_filter_file_is_supported(self) -> None:
        allowed = backfill.resolve_allowed_users(
            {"测试用户": {"评论者甲", "评论者乙"}}, "测试用户", {}
        )
        self.assertEqual(allowed, {"评论者甲", "评论者乙"})

    def test_list_from_inline_config_is_supported(self) -> None:
        allowed = backfill.resolve_allowed_users(
            {"123": ["评论者甲", "评论者乙"]}, "测试用户", {"测试用户": "123"}
        )
        self.assertEqual(allowed, {"评论者甲", "评论者乙"})

    def test_unknown_account_returns_none(self) -> None:
        self.assertIsNone(
            backfill.resolve_allowed_users({"其他用户": ["评论者甲"]}, "测试用户", {})
        )


class FileLockTest(unittest.TestCase):
    """跨进程文件锁的基本语义。"""

    def test_busy_then_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sub" / ".weibo_crawler.lock")
            first = file_lock.FileLock(path)
            first.acquire()
            try:
                second = file_lock.FileLock(path)
                with self.assertRaises(file_lock.LockBusy):
                    second.acquire()
            finally:
                first.release()
            # 释放后可再次获取（进程不会因为锁文件残留而永久死锁）
            again = file_lock.FileLock(path)
            again.acquire()
            again.release()

    def test_lock_path_in_output_dir(self) -> None:
        path = file_lock.lock_path_for_output("/tmp/weibo_data")
        self.assertEqual(path, "/tmp/weibo_data/.weibo_crawler.lock")


class BackfillSafetyTest(unittest.TestCase):
    """备份状态共享 / Ctrl-C 落盘 / 并发锁 / dry-run 不建目录。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output_dir = self.root / "weibo_data"
        self.user_dir = self.output_dir / "用户A"
        month_dir = self.user_dir / "2024-05"
        month_dir.mkdir(parents=True)
        self.md_path = month_dir / "2024-05-03.md"
        self.md_path.write_text(MD_TEMPLATE, encoding="utf-8")
        self.report_dir = self.output_dir / "report"
        self.config_path = self.root / "config.json"
        self._write_config(backup=0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @classmethod
    def setUpClass(cls) -> None:
        cls._old_log_level = backfill.logger.level
        backfill.logger.setLevel(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls) -> None:
        backfill.logger.setLevel(cls._old_log_level)

    def _write_config(self, backup: int) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    "weibo_comment_backfill": 1,
                    "output_directory": str(self.output_dir),
                    "comment_request_delay_min": 0,
                    "comment_request_delay_max": 0,
                    "comment_markdown_backup": backup,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _run(self, session, *extra_args):
        argv = ["--config", str(self.config_path)] + list(extra_args)
        with mock.patch.object(backfill, "build_session", return_value=session):
            return backfill.main(argv)

    def _report_text(self) -> str:
        reports = sorted(self.report_dir.glob("weibo_comment_backfill_*.txt"))
        self.assertTrue(reports, "回填报告未生成")
        return "\n".join(path.read_text(encoding="utf-8") for path in reports)

    def test_backup_once_per_md_file(self) -> None:
        """开启备份时，同一 md 文件里的多条微博只生成一份 .bak。"""
        self._write_config(backup=1)
        session = FakeSession(comment_page_handler)
        code = self._run(session, "--max-weibo", "2")
        self.assertEqual(code, 0)
        backups = list(self.md_path.parent.glob("2024-05-03.md.bak.*"))
        self.assertEqual(len(backups), 1, backups)

    def test_keyboard_interrupt_saves_progress_and_report(self) -> None:
        """Ctrl-C 后必须保存进度并写部分报告，退出码 2。"""

        class InterruptSession(FakeSession):
            def get(self, *args, **kwargs):
                raise KeyboardInterrupt()

        session = InterruptSession(comment_page_handler)
        code = self._run(session)
        self.assertEqual(code, 2)
        self.assertTrue(
            (
                self.report_dir / "用户A" / "comment_backfill_progress.json"
            ).is_file()
        )
        report = self._report_text()
        self.assertIn("中断: 是", report)

    def test_busy_lock_refuses_to_run(self) -> None:
        """主爬/另一个回填持锁时，回填应拒绝执行并返回 2。"""
        lock_path = file_lock.lock_path_for_output(str(self.output_dir))
        holder = file_lock.FileLock(lock_path)
        holder.acquire()
        try:
            code = self._run(FakeSession(comment_page_handler))
        finally:
            holder.release()
        self.assertEqual(code, 2)
        self.assertFalse(self.md_path.read_text(encoding="utf-8").count("#### 评论"))

    def test_dry_run_does_not_create_report_dir(self) -> None:
        """dry-run 只读：不应创建报告目录。"""
        self.assertFalse(self.report_dir.exists())
        code = self._run(FakeSession(comment_page_handler), "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(self.report_dir.exists())


class BackupPatternTest(unittest.TestCase):
    def test_backup_regex_covers_both_naming_styles(self):
        """清理命令要同时识别主爬的 .bak.YYYYMMDD 与评论的 .bak.YYYYMMDD_HHMMSS。"""
        matched = [
            "2024-05-03.md.bak.20261102",
            "2024-05-03.md.bak.20261102_2",
            "2024-05-03.md.bak.20261102_120000",
            "2024-05-03.md.bak.20261102_120000_1",
        ]
        for name in matched:
            self.assertTrue(backfill.BACKUP_RE.search(name), name)
        unmatched = [
            "2024-05-03.md.bak",
            "2024-05-03.md.bak.x",
            "2024-05-03.md",
            "2024-05-03.md.bak.2026110",
        ]
        for name in unmatched:
            self.assertFalse(backfill.BACKUP_RE.search(name), name)


if __name__ == "__main__":
    unittest.main()
