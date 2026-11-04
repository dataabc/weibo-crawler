"""覆盖：识别、歧义保护、dry-run 只读、--fix 修改 + 备份 + 其他内容不变。"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


from tests import _support  # noqa: E402

_support.bootstrap()

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts" / "legacy")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import weibo_fix_dead_links as fixer  # noqa: E402

sys.modules.pop("json5", None)

MD_TEMPLATE = """### 10:00:00
<!-- weibo_id: 101 -->
正文

![img](img/a.jpg)

[视频](原创微博视频/v.mov)

Live Photo ![LP](原创微博Live视频/lp.mov)

---

### 11:00:00
<!-- weibo_id: 102 -->
其他

---
"""


class FindLinkFixesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.user_dir = self.root / "用户A"
        (self.user_dir / "img").mkdir(parents=True)
        (self.user_dir / "原创微博视频").mkdir()
        (self.user_dir / "原创微博Live视频").mkdir()
        self.md_path = self.user_dir / "2024-05-03.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, text: str) -> None:
        self.md_path.write_text(text, encoding="utf-8")

    def test_mov_to_mp4_and_jpg_to_gif_fixed(self) -> None:
        # 实际落盘：视频/图片都是另一种扩展名
        (self.user_dir / "img" / "a.gif").write_bytes(b"GIF89a" + b"0" * 10 + b";")
        (self.user_dir / "原创微博视频" / "v.mp4").write_bytes(b"x" * 10)
        (self.user_dir / "原创微博Live视频" / "lp.mp4").write_bytes(b"x" * 10)
        self._write(MD_TEMPLATE)
        fixes, ambiguous = fixer.find_link_fixes(MD_TEMPLATE, str(self.md_path))
        self.assertEqual(ambiguous, [])
        self.assertEqual(len(fixes), 3)
        by_old = {fix.old_link: fix.new_link for fix in fixes}
        self.assertEqual(by_old["img/a.jpg"], "img/a.gif")
        self.assertEqual(by_old["原创微博视频/v.mov"], "原创微博视频/v.mp4")
        self.assertEqual(by_old["原创微博Live视频/lp.mov"], "原创微博Live视频/lp.mp4")

    def test_existing_file_not_touched(self) -> None:
        (self.user_dir / "img" / "a.jpg").write_bytes(b"\xff\xd8\xff0\xff\xd9")
        self._write(MD_TEMPLATE)
        fixes, _ambiguous = fixer.find_link_fixes(MD_TEMPLATE, str(self.md_path))
        self.assertEqual(fixes, [])

    def test_ambiguous_candidates_reported_not_fixed(self) -> None:
        (self.user_dir / "img" / "a.gif").write_bytes(b"x")
        (self.user_dir / "img" / "a.webp").write_bytes(b"x")
        self._write(MD_TEMPLATE)
        fixes, ambiguous = fixer.find_link_fixes(MD_TEMPLATE, str(self.md_path))
        self.assertEqual(fixes, [])
        self.assertEqual(len(ambiguous), 1)

    def test_apply_fixes_updates_link_and_backs_up(self) -> None:
        (self.user_dir / "img" / "a.gif").write_bytes(b"GIF89a" + b"0" * 10 + b";")
        self._write(MD_TEMPLATE)
        fixes, _ = fixer.find_link_fixes(MD_TEMPLATE, str(self.md_path))
        self.assertTrue(fixer.apply_fixes(str(self.md_path), fixes, backup=True))
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("img/a.gif", text)
        self.assertNotIn("img/a.jpg", text)
        # 其他内容原样保留
        self.assertIn("### 11:00:00", text)
        self.assertIn("<!-- weibo_id: 102 -->", text)
        backups = [p for p in self.user_dir.iterdir() if ".bak." in p.name]
        self.assertEqual(len(backups), 1)
        self.assertEqual(
            [p for p in self.user_dir.glob("*.tmp")], []
        )

    def test_main_dry_run_is_read_only(self) -> None:
        (self.user_dir / "img" / "a.gif").write_bytes(b"GIF89a" + b"0" * 10 + b";")
        self._write(MD_TEMPLATE)
        before = self.md_path.read_text(encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fixer.main(["--output-dir", str(self.root), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(self.md_path.read_text(encoding="utf-8"), before)
        self.assertIn("可修复死链", out.getvalue())
        self.assertIn("dry-run", out.getvalue())


if __name__ == "__main__":
    unittest.main()
