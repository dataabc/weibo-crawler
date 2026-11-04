"""覆盖项：
- get_filepath 支持绝对路径 output_directory；
- CSV / JSON 去重（重试/补抓不再产生重复行、重复对象）；
- resolve_cookie（环境变量优先、多行 cookie 文件、文件缺失不崩）；
- get_user_info 重试耗尽返回 1，不再 sys.exit；
- 用户列表解析（四字段行、多空格、第三列非法日期回退全局）；
- get_long_weibo 失败返回 None，不再抛异常；
- initialize_info 不重置 new_written_weibo（多关键词 query 共享）；
- 主流程评论失败写「重试清单」，下一轮自动重试；
- Markdown 渲染随媒体下载开关联动（关掉下载不写死链）；
- markdown 模式媒体目录与 md 链接目录一致（不再多一层 video/）；
- 转发图落到 retweet_image_dir，而不是原创图目录。

测试环境无第三方依赖：按 tests/test_p0_regressions.py 的做法「只在缺失时补
stub、运行期临时替换、用完恢复」；全部用 tempfile，不联网、不落地真实数据。"""

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class _RequestException(Exception):
    pass


class _Adapter:
    def __init__(self, *args, **kwargs):
        pass


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402

# json5 只是为了让 weibo.py 可导入；移除以免影响 util.config_migrate 的
# 「无 json5 降级解析」测试（与其它测试文件保持一致）。
sys.modules.pop("json5", None)

from util.comment import comment_fetch  # noqa: E402


def new_wb() -> "weibo.Weibo":
    """构造一个不跑 __init__ 的 Weibo 实例，测试只注入被测方法需要的属性。"""
    return weibo.Weibo.__new__(weibo.Weibo)


class GetFilepathAbsoluteTest(unittest.TestCase):
    """output_directory 为绝对路径时，不再把绝对路径拼到项目目录后面。"""

    def test_absolute_output_directory_returns_absolute_user_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wb = new_wb()
            wb.user = {"screen_name": "测试用户"}
            wb.user_config = {"user_id": "123"}
            wb.user_id_as_folder_name = 0
            wb.output_directory = tmp  # 绝对路径（config 模板承诺支持）

            markdown_dir = wb.get_filepath("markdown")

            self.assertEqual(markdown_dir, os.path.join(tmp, "测试用户"))
            self.assertTrue(os.path.isdir(markdown_dir))
            # 既不在临时目录下，还会在项目里建出错误目录。
            project_root = os.path.dirname(os.path.realpath(weibo.__file__))
            self.assertFalse(markdown_dir.startswith(project_root + os.sep))


class CsvDedupTest(unittest.TestCase):
    """同一批微博重复写 CSV 不应出现重复行（重试/补抓场景）。

    第二次写入时文件已存在，必然走 ``_read_csv_ids`` 去重分支——这里同时覆盖
    「已存在 ID 被过滤」与「新 ID 仍正常追加」两侧，避免误写成「有文件就不写」。"""

    def test_write_csv_twice_keeps_single_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "123.csv")
            wb = new_wb()
            wb.only_crawl_original = 1
            wb.got_count = 1
            wb.weibo = [
                {"id": "1001", "text": "正文", "created_at": "2024-06-01T10:00:00"}
            ]
            wb.get_filepath = lambda _type: csv_path

            wb.write_csv(0)
            # 对 odict_values 取下标会 TypeError，导致 CSV 静默不写）
            wb.write_csv(0)  # 模拟重抓同一窗口

            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = [row for row in csv.reader(f) if row]
            data_rows = rows[1:]
            self.assertEqual(len(data_rows), 1)
            self.assertEqual(str(data_rows[0][0]).strip(), "1001")

            # 去重不能误伤新微博：追加一条新 ID 后应正常写入第 2 行
            wb.weibo.append(
                {"id": "1002", "text": "新正文", "created_at": "2024-06-02T10:00:00"}
            )
            wb.write_csv(1)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = [row for row in csv.reader(f) if row]
            ids = [str(row[0]).strip() for row in rows[1:]]
            self.assertEqual(ids, ["1001", "1002"])


class JsonMergeTest(unittest.TestCase):
    ""

    def test_existing_id_updated_new_id_appended(self) -> None:
        wb = new_wb()
        wb.user = {"id": "123", "screen_name": "测试用户"}
        data = {
            "user": {"id": "123"},
            "weibo": [{"id": "1", "text": "旧内容"}],
        }

        merged = wb.update_json_data(
            data,
            [
                {"id": "1", "text": "更新内容"},  # 已存在：覆盖
                {"id": "2", "text": "新增"},      # 不存在：追加
            ],
        )

        ids = [str(w["id"]) for w in merged["weibo"]]
        self.assertEqual(ids, ["1", "2"])
        self.assertEqual(merged["weibo"][0]["text"], "更新内容")
        self.assertEqual(merged["weibo"][1]["text"], "新增")


class ResolveCookieTest(unittest.TestCase):
    """Cookie 解析优先级与文件路径解析（模块级 resolve_cookie）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cookie_file = self.root / "cookie.txt"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_env_wins_over_cookie_file_and_path_is_absolute(self) -> None:
        self.cookie_file.write_text("SUB=file; SUBP=file2", encoding="utf-8")

        cookie, path = weibo.resolve_cookie(
            "SUB=env", "cookie.txt", str(self.root)
        )

        self.assertEqual(cookie, "SUB=env")
        self.assertEqual(path, str(self.cookie_file))

    def test_multiline_cookie_file_joined(self) -> None:
        self.cookie_file.write_text("SUB=a;\nSUBP=b\n", encoding="utf-8")

        cookie, path = weibo.resolve_cookie("", "cookie.txt", str(self.root))

        self.assertEqual(cookie, "SUB=a; SUBP=b")
        self.assertEqual(path, str(self.cookie_file))

    def test_missing_cookie_file_returns_empty_without_raising(self) -> None:
        missing = self.root / "不存在.txt"

        cookie, path = weibo.resolve_cookie("", "不存在.txt", str(self.root))

        self.assertEqual(cookie, "")
        self.assertEqual(path, str(missing))

    def test_literal_cookie_string_passthrough(self) -> None:
        cookie, path = weibo.resolve_cookie("", "SUB=literal", str(self.root))
        self.assertEqual(cookie, "SUB=literal")
        self.assertIsNone(path)


class GetUserInfoRetryTest(unittest.TestCase):
    """用户信息重试耗尽返回 1，不再 sys.exit 终止整轮抓取。"""

    def test_retry_exhausted_returns_one(self) -> None:
        class _FailSession:
            calls = 0

            def get(self, *args, **kwargs):
                _FailSession.calls += 1
                raise weibo.RequestException("模拟网络失败")

        wb = new_wb()
        wb.user_config = {"user_id": "123"}
        wb.long_sleep_count_before_each_user = 0
        wb.session = _FailSession()
        wb.get_random_headers = lambda: {}
        wb.calculate_dynamic_delay = lambda: 0
        wb.update_crawl_stats = lambda **kwargs: None
        wb.handle_captcha = lambda js: True
        sleeps = []

        with mock.patch.object(weibo, "sleep", sleeps.append):
            code = wb.get_user_info()

        self.assertEqual(code, 1)
        self.assertEqual(_FailSession.calls, 5)  # max_retries=5
        self.assertEqual(len(sleeps), 5)  # 退避确实发生，但被 mock 掉没真睡


class UserConfigListTest(unittest.TestCase):
    """用户列表解析（四字段、多空格、非法日期回退）。"""

    def _wb(self) -> "weibo.Weibo":
        wb = new_wb()
        wb.since_date = "2024-01-01T00:00:00"
        wb.end_date = "2024-12-31T00:00:00"
        wb.query_list = ["默认"]
        return wb

    def _parse(self, content: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "user_id_list.txt")
            Path(path).write_text(content, encoding="utf-8")
            return self._wb().get_user_config_list(path)

    def test_four_fields_keep_since_date_and_query_list(self) -> None:
        configs = self._parse("123 昵称 2020-01-01 梦想,希望\n")

        self.assertEqual(len(configs), 1)
        cfg = configs[0]
        self.assertEqual(cfg["user_id"], "123")
        self.assertEqual(cfg["since_date"], "2020-01-01T00:00:00")
        self.assertEqual(cfg["since_date_source"], "user_list")
        self.assertEqual(cfg["query_list"], ["梦想", "希望"])
        self.assertEqual(cfg["end_date"], "2024-12-31T00:00:00")

    def test_multiple_spaces_between_fields(self) -> None:
        configs = self._parse("456  昵称  2021-02-03\n")

        self.assertEqual(len(configs), 1)
        cfg = configs[0]
        self.assertEqual(cfg["user_id"], "456")
        self.assertEqual(cfg["since_date"], "2021-02-03T00:00:00")
        self.assertEqual(cfg["since_date_source"], "user_list")
        self.assertEqual(cfg["query_list"], ["默认"])

    def test_invalid_third_column_falls_back_to_global(self) -> None:
        wb = self._wb()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "user_id_list.txt")
            Path(path).write_text("789 昵称 abc\n", encoding="utf-8")
            configs = wb.get_user_config_list(path)

        self.assertEqual(len(configs), 1)
        cfg = configs[0]
        self.assertEqual(cfg["since_date"], wb.since_date)
        self.assertEqual(cfg["since_date_source"], "config")


class GetLongWeiboTest(unittest.TestCase):
    """长微博详情页请求失败返回 None，不再让异常冒泡。"""

    def test_request_failure_returns_none(self) -> None:
        class _FailSession:
            def get(self, *args, **kwargs):
                raise weibo.RequestException("模拟详情页失败")

        wb = new_wb()
        wb.headers = {}
        wb.session = _FailSession()

        with mock.patch.object(weibo, "sleep", lambda _seconds: None):
            result = wb.get_long_weibo("5001")

        self.assertIsNone(result)


class InitializeInfoTest(unittest.TestCase):
    """initialize_info 不得重置 new_written_weibo（多关键词 query 共享）。"""

    def test_new_written_weibo_preserved(self) -> None:
        wb = new_wb()
        wb.new_written_weibo = {"1": "/tmp/2024-06-01.md"}

        wb.initialize_info({"user_id": "123"})

        self.assertEqual(wb.new_written_weibo, {"1": "/tmp/2024-06-01.md"})
        # 其余轮次级状态仍照常重置
        self.assertEqual(wb.weibo, [])
        self.assertEqual(wb.user, {})
        self.assertEqual(wb.got_count, 0)
        self.assertEqual(wb.weibo_id_list, [])
        self.assertEqual(wb.user_config, {"user_id": "123"})

    def test_first_initialize_creates_empty_dict(self) -> None:
        wb = new_wb()

        wb.initialize_info({"user_id": "456"})

        self.assertEqual(wb.new_written_weibo, {})


class CommentRetryListTest(unittest.TestCase):
    """评论抓取失败写持久化重试清单，下一轮自动重试（不当作「无评论」）。"""

    WEIBO_ID = "101"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.md_path = self.root / "2024-06" / "2024-06-01.md"
        self.md_path.parent.mkdir(parents=True)
        self.original_md = (
            "## 2024-06-01 [测试用户] 微博存档\n\n"
            "### 10:00:00\n<!-- weibo_id: 101 -->\n内容\n\n---\n"
        )
        self.md_path.write_text(self.original_md, encoding="utf-8")

        wb = new_wb()
        wb.download_comment = 1
        wb.write_mode = ["markdown"]
        wb.user_config = {"user_id": "123"}
        wb.user = {"screen_name": "测试用户"}
        wb.output_directory = str(self.root)  # 绝对路径：报告目录落在临时目录
        wb.comment_summary_dir = "report"
        wb.markdown_file_dir = {}
        wb.comment_max_download_count = 0
        wb.comment_reply_max_download_count = 0
        wb.comment_request_delay_min = 0
        wb.comment_request_delay_max = 0
        wb.comment_user_filter_file = ""
        wb.session = object()
        wb.headers = {}
        wb.new_written_weibo = {self.WEIBO_ID: str(self.md_path)}
        self.wb = wb
        # 报告按用户分目录：report/<用户名>/comment_retry_<user_id>.json
        self.retry_path = self.root / "report" / "测试用户" / "comment_retry_123.json"
        self.calls: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _fetch_result(self, *, failed: bool):
        stats = comment_fetch.FetchStats(
            stopped_reason="error" if failed else "empty",
            error_messages=["HTTP 403"] if failed else [],
        )
        return comment_fetch.FetchResult(
            weibo_id=self.WEIBO_ID,
            total_number=0,
            roots=[],
            stats=stats,
            failed=failed,
        )

    def _fake_fetch(self, failed: bool):
        def _fetch(session, headers, weibo_id, options):
            self.calls.append(str(weibo_id))
            return self._fetch_result(failed=failed)

        return _fetch

    def test_failed_fetch_recorded_to_retry_list(self) -> None:
        with mock.patch.object(
            weibo.comment_fetch, "fetch_comments", self._fake_fetch(True)
        ):
            self.wb.write_markdown_comments_for_new_weibo()

        # 失败不写评论块，md 内容保持原样
        self.assertEqual(self.md_path.read_text(encoding="utf-8"), self.original_md)
        self.assertNotIn("<!-- comments:", self.original_md)
        self.assertTrue(self.retry_path.is_file())
        retry_map = json.loads(self.retry_path.read_text(encoding="utf-8"))
        self.assertIn(self.WEIBO_ID, retry_map)
        self.assertEqual(retry_map[self.WEIBO_ID]["md_path"], str(self.md_path))
        self.assertIn("HTTP 403", retry_map[self.WEIBO_ID]["note"])

    def test_retry_list_processed_next_round_and_consumed(self) -> None:
        with mock.patch.object(
            weibo.comment_fetch, "fetch_comments", self._fake_fetch(True)
        ):
            self.wb.write_markdown_comments_for_new_weibo()
        self.assertTrue(self.retry_path.is_file())

        # 第二轮：本轮没有新微博，但清单里的 101 仍要被重试
        self.calls.clear()
        self.wb.new_written_weibo = {}
        with mock.patch.object(
            weibo.comment_fetch, "fetch_comments", self._fake_fetch(False)
        ):
            self.wb.write_markdown_comments_for_new_weibo()

        self.assertEqual(self.calls, [self.WEIBO_ID])
        # 明确「无评论」（failed=False, roots=[]）才算处理完成：清单被消费
        self.assertFalse(self.retry_path.exists())

    def test_no_targets_skips_fetch(self) -> None:
        self.wb.new_written_weibo = {}
        with mock.patch.object(
            weibo.comment_fetch, "fetch_comments", self._fake_fetch(False)
        ):
            self.wb.write_markdown_comments_for_new_weibo()

        self.assertEqual(self.calls, [])


class MarkdownRenderToggleTest(unittest.TestCase):
    """媒体下载开关关闭时，Markdown 不写本地媒体链接（避免死链）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        wb = new_wb()
        wb.user = {"screen_name": "测试用户"}
        wb.user_config = {"user_id": "123"}
        wb.markdown_file_dir = {}
        wb.markdown_preview = {}
        wb.markdown_image_order = "live_photo_first"
        wb.markdown_sort_order = "new_to_old"
        wb.markdown_split_by = "day"
        wb.only_crawl_original = 1
        wb.original_pic_download = 0
        wb.retweet_pic_download = 0
        wb.original_video_download = 0
        wb.original_live_photo_download = 0
        wb.retweet_video_download = 0
        wb.retweet_live_photo_download = 0
        wb.get_filepath = lambda _type: str(self.root)
        wb.new_written_weibo = {}
        self.wb = wb
        self.post = {
            "id": "5001",
            "created_at": "2024-05-03T10:00:00",
            "text": "查看图片",
            "pics": "https://example.com/a.jpg",
            "video_url": "https://example.com/v.mp4",
            "live_photo_url": "https://example.com/lp.mov",
        }
        self.md_path = self.root / "2024-05-03.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_links_follow_download_switches(self) -> None:
        self.wb.generate_markdown_file("2024-05-03", [self.post])
        text = self.md_path.read_text(encoding="utf-8")
        self.assertNotIn("](img/", text)
        self.assertNotIn("原创微博视频/", text)
        self.assertNotIn("Live Photo", text)
        self.assertIn("查看图片", text)  # 占位保留原文

        # 打开下载开关后重跑（先删掉 md，避免同 ID 去重跳过）
        self.md_path.unlink()
        self.wb.original_pic_download = 1
        self.wb.original_video_download = 1
        self.wb.generate_markdown_file("2024-05-03", [self.post])
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("![查看图片](img/2024-05-03_10-00-00.jpg)", text)
        self.assertIn("[视频](原创微博视频/2024-05-03_10-00-00.mp4)", text)


class DownloadFilesPathTest(unittest.TestCase):
    """markdown 模式媒体落盘目录与 md 链接目录一致（不再多一层 video/）。"""

    def test_media_dir_has_no_extra_type_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wb = new_wb()
            wb.write_mode = ["markdown"]
            wb.markdown_split_by = "day"
            wb.markdown_file_dir = {"original_video_dir": "原创微博视频"}
            wb.download_folder_name = {"original_video_folder": "原创微博视频"}
            wb.weibo = [
                {
                    "id": "5001",
                    "created_at": "2024-05-03T10:00:00",
                    "video_url": "https://example.com/v.mp4",
                }
            ]
            # 模拟真实 get_filepath：markdown → 用户目录；video → 用户目录/video
            wb.get_filepath = lambda type: (
                os.path.join(tmp, "video") if type == "video" else tmp
            )
            recorded: list[str] = []
            wb.handle_download = (
                lambda file_type, file_dir, urls, w: recorded.append(file_dir)
            )

            wb.download_files("video", "original", 0)

            self.assertEqual(recorded, [os.path.join(tmp, "原创微博视频")])
            self.assertNotEqual(
                recorded[0], os.path.join(tmp, "video", "原创微博视频")
            )


class RetweetImageDirTest(unittest.TestCase):
    """转发图必须落到 retweet_image_dir，不能混进原创图目录。"""

    def test_retweet_images_use_retweet_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wb = new_wb()
            wb.markdown_split_by = "day"
            wb.original_pic_download = 0
            wb.retweet_pic_download = 1
            wb.only_crawl_original = 0
            wb.markdown_file_dir = {
                "original_image_dir": "img",
                "retweet_image_dir": "转发图片",
            }
            wb.weibo = [
                {
                    "id": "5001",
                    "created_at": "2024-05-03T10:00:00",
                    "retweet": {"pics": "https://example.com/r.jpg"},
                }
            ]
            wb.get_filepath = lambda _type: tmp
            recorded: list[tuple[str, bool]] = []
            wb._download_weibo_images = (
                lambda weibo_obj, img_dir, is_retweet=False: recorded.append(
                    (img_dir, is_retweet)
                )
            )

            wb.download_markdown_images(0)

            self.assertEqual(len(recorded), 1)
            img_dir, is_retweet = recorded[0]
            self.assertEqual(img_dir, os.path.join(tmp, "转发图片"))
            self.assertTrue(is_retweet)
            self.assertNotEqual(img_dir, os.path.join(tmp, "img"))


if __name__ == "__main__":
    unittest.main()
