"""存档核对公共核心的纯逻辑测试（不联网、不依赖第三方库）。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from util import archive_check_core, archive_index


def dt(text: str) -> datetime:
    return datetime.strptime(text, archive_check_core.DTFORMAT)


class CompareArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.since = dt("2024-01-01T00:00:00")
        self.until = dt("2024-12-31T00:00:00")

    def _entries(self, data):
        return {wid: (d, f"/tmp/{wid}.md") for wid, d in data.items()}

    def test_double_diff_and_range_filter(self) -> None:
        local = self._entries(
            {
                "1001": dt("2024-02-01T00:00:00"),
                "1002": dt("2024-03-01T00:00:00"),
                "1003": dt("2024-05-01T00:00:00"),  # 本地有、平台没有
                "9999": dt("2023-01-01T00:00:00"),  # 范围外
                "8888": None,                       # 无日期
            }
        )
        platform = {
            "1001": dt("2024-02-01T00:00:00"),
            "1002": dt("2024-03-01T00:00:00"),
            "2002": dt("2024-04-01T00:00:00"),      # 平台有、本地没有
        }
        diff = archive_check_core.compare_archive(
            local, platform, (), self.since, self.until
        )
        self.assertEqual(diff.not_returned, ["1003"])
        self.assertEqual(diff.not_saved, ["2002"])

    def test_invisible_excluded_from_both_directions(self) -> None:
        local = self._entries(
            {
                "3001": dt("2024-03-01T00:00:00"),
                "3003": dt("2024-03-15T00:00:00"),
            }
        )
        platform = {"3002": dt("2024-04-01T00:00:00")}
        diff = archive_check_core.compare_archive(
            local, platform, {"3001", "3002", "3003"}, self.since, self.until
        )
        self.assertEqual(diff.not_returned, [])
        self.assertEqual(diff.not_saved, [])

    def test_archive_entry_without_date_not_participating(self) -> None:
        local = {"4001": (None, "/tmp/4001.md")}
        diff = archive_check_core.compare_archive(local, {}, (), self.since, self.until)
        self.assertEqual(diff.not_returned, [])
        self.assertEqual(diff.not_saved, [])

    def test_sort_ids_desc(self) -> None:
        local = self._entries(
            {
                "100": dt("2024-02-01T00:00:00"),
                "300": dt("2024-03-01T00:00:00"),
                "200": dt("2024-04-01T00:00:00"),
            }
        )
        diff = archive_check_core.compare_archive(
            local, {}, (), self.since, self.until
        )
        self.assertEqual(diff.not_returned, ["300", "200", "100"])

    def test_render_report(self) -> None:
        local = self._entries(
            {
                "5001": dt("2024-05-01T00:00:00"),
                "5003": dt("2024-06-01T00:00:00"),
            }
        )
        platform = {"5002": dt("2024-07-01T00:00:00")}
        diff = archive_check_core.compare_archive(
            local, platform, (), self.since, self.until
        )
        text = archive_check_core.render_report(
            "本轮存档核对报告",
            "123",
            "测试用户",
            diff,
            header_lines=["本轮抓取是否顺利完成: 是（测试）"],
            range_start=self.since,
            range_end=self.until,
            summary_lines=["not_returned_count: 1", "not_saved_count: 1"],
            base_dir="/tmp",
        )
        self.assertIn("本轮存档核对报告", text)
        self.assertIn("https://m.weibo.cn/detail/5001", text)
        self.assertIn("https://m.weibo.cn/detail/5002", text)
        self.assertIn("发布日期: 2024-05-01", text)
        self.assertIn("本地文件: 5001.md", text)
        self.assertIn("本地文件: （本地无文件）", text)
        self.assertIn("not_returned_count: 1", text)
        self.assertIn("报告完毕。", text)


class IgnoreListTest(unittest.TestCase):
    def test_parse_ignore_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "用户"
            user_dir.mkdir()
            (user_dir / "ignore_ids.txt").write_text(
                "# 人工忽略\n"
                "5000000000000001\n"
                "5000000000000002  # 不想补回\n"
                "5000000000000003\t原因\n"
                "这不是ID\n",
                encoding="utf-8",
            )
            ids = archive_index.collect_ignore_file_ids(str(user_dir))
            self.assertEqual(
                ids,
                {"5000000000000001", "5000000000000002", "5000000000000003"},
            )

    def test_collect_invisible_weibo_ids_merges_gone_dir_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "用户"
            gone = user_dir / "已不可见" / "2024-06"
            gone.mkdir(parents=True)
            (gone / "2024-06-01.md").write_text(
                "### 10:00:00\n<!-- weibo_id: 1001 -->\n内容\n", encoding="utf-8"
            )
            (user_dir / "ignore_ids.txt").write_text("2002\n", encoding="utf-8")
            ids = archive_index.collect_invisible_weibo_ids(str(user_dir), ["已不可见"])
            self.assertEqual(ids, {"1001", "2002"})

    def test_tab_marker_in_gone_dir_detected(self) -> None:
        """`已不可见/` 里带 tab 的 marker 也要能识别（B6）。"""
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "用户"
            gone = user_dir / "已不可见" / "2023-03"
            gone.mkdir(parents=True)
            (gone / "2023-03-06.md").write_text(
                "### 10:00:00\n<!-- weibo_id: 3003\t -->\n内容\n", encoding="utf-8"
            )
            ids = archive_index.collect_known_invisible_ids(str(user_dir), ["已不可见"])
            self.assertEqual(ids, {"3003"})


if __name__ == "__main__":
    unittest.main()
