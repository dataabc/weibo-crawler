"""util/archive_index.py 的单元测试（纯本地临时目录，无网络）。"""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from util import archive_index as ai


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_binary(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_md(*entries: str, title: str = "微博存档") -> str:
    return f"## 2024-01-01 [{title}]\n\n" + "\n".join(entries) + "\n"


def entry(heading: str, weibo_id: str, text: str = "内容") -> str:
    return f"### {heading}\n<!-- weibo_id: {weibo_id} -->\n{text}\n\n---\n"


class ArchiveIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.user_dir = Path(self.tmp.name) / "测试用户"
        self.user_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_layout_aware_scan(self) -> None:
        """根目录 + YYYY-MM 目录被扫描；媒体/隐藏目录不进入。"""
        write_text(
            self.user_dir / "2024-05.md",
            make_md(entry("2024-05-01 10:00:00", "1001")),
        )
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "1002")),
        )
        write_text(
            self.user_dir / "原创微博图片" / "不要扫.md",
            make_md(entry("10:00:00", "9999")),
        )
        write_text(
            self.user_dir / ".obsidian" / "不要扫.md",
            make_md(entry("10:00:00", "8888")),
        )
        paths = {os.path.basename(p) for p in ai.iter_active_md_paths(str(self.user_dir))}
        self.assertEqual(paths, {"2024-05.md", "2024-06-01.md"})

    def test_skip_hidden_and_appledouble(self) -> None:
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "2001")),
        )
        # AppleDouble 二进制文件，读取时必须被跳过且不抛异常
        write_binary(self.user_dir / "2024-06" / "._2024-06-01.md", b"\x00\x05\x16\x07Mac OS X")
        ids, stats = ai.collect_archive_ids(str(self.user_dir))
        self.assertIn("2001", ids)
        self.assertEqual(stats.files_scanned, 1)

    def test_known_gone_directory(self) -> None:
        """默认排除目录「已删除或隐藏」被识别，且不计入活跃存档。"""
        write_text(
            self.user_dir / "已不可见" / "2024-06" / "2024-06-02.md",
            make_md(
                entry("10:00:00", "3001"),
                "### 11:00:00\n<!-- weibo_id: 3002\t -->\n内容\n",
            ),
        )
        known = ai.collect_known_invisible_ids(str(self.user_dir))
        self.assertEqual(known, {"3001", "3002"})
        # 活跃存档扫描不应把排除目录算进去
        ids, _ = ai.collect_archive_ids(str(self.user_dir))
        self.assertNotIn("3001", ids)

    def test_window_filter(self) -> None:
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(
                entry("10:00:00", "4001"),
                entry("12:00:00", "4002"),
            ),
        )
        write_text(
            self.user_dir / "2024-07" / "2024-07-01.md",
            make_md(entry("10:00:00", "4003")),
        )
        start = datetime(2024, 6, 1, 11, 0, 0)
        end = datetime(2024, 6, 30, 23, 59, 59)
        ids, _ = ai.collect_archive_ids(str(self.user_dir), start, end)
        self.assertEqual(set(ids), {"4002"})

    def test_parse_heading_variants(self) -> None:
        fd = datetime(2024, 6, 1)
        self.assertEqual(
            ai.parse_heading_datetime("10:20:30", fd), datetime(2024, 6, 1, 10, 20, 30)
        )
        self.assertEqual(
            ai.parse_heading_datetime("2024-06-02 10:20:30", fd),
            datetime(2024, 6, 2, 10, 20, 30),
        )
        self.assertIsNone(ai.parse_heading_datetime("不是时间", None))

    def test_month_file_full_heading(self) -> None:
        write_text(
            self.user_dir / "2024-08.md",
            make_md(
                entry("2024-08-03 09:00:00", "5001"),
                entry("2024-08-04 09:00:00", "5002"),
            ),
        )
        ids, _ = ai.collect_archive_ids(str(self.user_dir))
        self.assertEqual(ids["5001"], datetime(2024, 8, 3, 9, 0, 0))
        self.assertEqual(ids["5002"], datetime(2024, 8, 4, 9, 0, 0))

    def test_unknown_date_excluded_from_window(self) -> None:
        write_text(
            self.user_dir / "全量.md",
            make_md("### 无时间标题\n<!-- weibo_id: 6001 -->\n内容\n"),
        )
        ids, stats = ai.collect_archive_ids(
            str(self.user_dir),
            datetime(2024, 1, 1),
            datetime(2024, 12, 31),
        )
        self.assertEqual(ids, {})
        self.assertEqual(stats.unknown_dates, 1)

    def test_filter_ids_in_range(self) -> None:
        ids = {
            "1": datetime(2024, 1, 1),
            "2": datetime(2024, 6, 1),
            "3": None,
        }
        out = ai.filter_ids_in_range(
            ids, datetime(2024, 5, 1), datetime(2024, 12, 31)
        )
        self.assertEqual(set(out), {"2"})

    def test_canonical_marker(self) -> None:
        self.assertEqual(ai.canonical_marker(123), "<!-- weibo_id: 123 -->")


if __name__ == "__main__":
    unittest.main()
