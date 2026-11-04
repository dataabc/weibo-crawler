"""编辑误判清理脚本 单元测试。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts" / "legacy")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import weibo_fix_edit_misdetection as fixer  # noqa: E402

SNAPSHOT_MISDETECTED = """# 微博 100 · 版本快照

保存时间: 2026-10-26 01:00:00
微博发布时间: 2024-05-03T10:00:00
该版本最后编辑时间: 未知
可见性: 公开

---

## 正文

正文内容

[网页链接](https://example.com/a)
"""

SNAPSHOT_REAL = """# 微博 200 · 版本快照

保存时间: 2026-10-26 01:00:00
微博发布时间: 2024-05-03T11:00:00
该版本最后编辑时间: 未知
可见性: 公开

---

## 正文

编辑前的旧正文（与当前不同）
"""

CURRENT_MD = """## 2024-05-03 [用户] 微博存档

### 10:00:00

<!-- weibo_id: 100 -->
<!-- edited_at: 2026-10-26T01:00:00 -->

正文内容

![网页链接](https://example.com/a)

---

### 11:00:00

<!-- weibo_id: 200 -->
<!-- edited_at: 2026-10-26T01:00:00 -->

编辑后的新正文

---
"""


class FixMisdetectionTest(unittest.TestCase):
    def _tree(self, tmpdir):
        user_dir = os.path.join(tmpdir, "用户A")
        month_dir = os.path.join(user_dir, "2024-05")
        snap_dir = os.path.join(user_dir, "编辑历史", "2026-10")
        os.makedirs(month_dir)
        os.makedirs(snap_dir)
        md_path = os.path.join(month_dir, "2024-05-03.md")
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(CURRENT_MD)
        with open(
            os.path.join(snap_dir, "2026-10-26_100.md"), "w", encoding="utf-8"
        ) as handle:
            handle.write(SNAPSHOT_MISDETECTED)
        with open(
            os.path.join(snap_dir, "2026-10-26_200.md"), "w", encoding="utf-8"
        ) as handle:
            handle.write(SNAPSHOT_REAL)
        state = {
            "weibos": {
                "100": {
                    "last_edit_at": "2026-10-26T01:00:00",
                    "versions": [
                        {"path": "编辑历史/2026-10/2026-10-26_100.md"}
                    ],
                },
                "200": {
                    "last_edit_at": "2026-10-26T01:00:00",
                    "versions": [
                        {"path": "编辑历史/2026-10/2026-10-26_200.md"}
                    ],
                },
            }
        }
        with open(
            os.path.join(user_dir, "archive_state_1.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(state, handle, ensure_ascii=False)
        return user_dir, md_path, snap_dir

    def test_dry_run_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir, _md, snap_dir = self._tree(tmpdir)
            result = fixer.fix_user(user_dir, dry_run=True, backup=True)
            self.assertEqual(result["snapshots"], 2)
            self.assertEqual(result["misdetected"], 1)
            self.assertEqual(result["real_edits"], 1)
            self.assertTrue(
                os.path.isfile(os.path.join(snap_dir, "2026-10-26_100.md"))
            )

    def test_fix_removes_mark_and_moves_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir, md_path, snap_dir = self._tree(tmpdir)
            result = fixer.fix_user(user_dir, dry_run=False, backup=True)
            self.assertEqual(result["misdetected"], 1)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            # 100（误判）的 edited_at 被移除；200（真实编辑）保留
            self.assertNotIn("weibo_id: 100 -->\n<!-- edited_at", text)
            self.assertIn("weibo_id: 200 -->\n<!-- edited_at", text)
            # 快照 100 被移到备份目录，200 保留
            self.assertFalse(
                os.path.isfile(os.path.join(snap_dir, "2026-10-26_100.md"))
            )
            self.assertTrue(
                os.path.isfile(os.path.join(snap_dir, "2026-10-26_200.md"))
            )
            backup_root = os.path.join(user_dir, "编辑历史")
            backup_dirs = [
                name for name in os.listdir(backup_root) if name == "_误判备份"
            ]
            self.assertEqual(len(backup_dirs), 1)
            # 状态索引：100 的版本移除、200 保留
            with open(
                os.path.join(user_dir, "archive_state_1.json"), encoding="utf-8"
            ) as handle:
                state = json.load(handle)
            self.assertEqual(state["weibos"]["100"]["versions"], [])
            self.assertEqual(len(state["weibos"]["200"]["versions"]), 1)


if __name__ == "__main__":
    unittest.main()
