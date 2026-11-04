#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复存量死链：md 链接扩展名与实际落盘文件不一致的历史遗留。

判定规则（保守）：
1. 链接指向的本地文件不存在；
2. 同目录下存在**唯一**一个「同文件名、等价扩展名」的文件；
3. 命中后只替换 md 中该链接的扩展名，不改动文件内容其他部分。

默认 dry-run（只读，只出清单）；``--fix`` 才实际修改，并默认备份为
``<文件名>.bak.<时间戳>``（一次运行一个文件一份备份）。

用法：
    python scripts/weibo_fix_dead_links.py --dry-run
    python scripts/weibo_fix_dead_links.py --fix
    python scripts/weibo_fix_dead_links.py --output-dir /path/to/weibo_data --dry-run"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote

PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from util.core import archive_index  # noqa: E402
from util.media import media_report  # noqa: E402

logger = None


def _get_logger():
    global logger
    if logger is None:
        import logging

        logger = logging.getLogger("weibo")
    return logger


# 等价扩展名组：只允许「同文件名换扩展名」这一种修复
EXT_EQUIV = {
    ".mov": (".mp4", ".m4v"),
    ".mp4": (".mov",),
    ".jpg": (".jpeg", ".gif", ".webp"),
    ".jpeg": (".jpg", ".gif"),
    ".gif": (".jpg", ".jpeg"),
    ".webp": (".jpg", ".jpeg", ".png"),
    ".png": (".jpg", ".webp"),
}


@dataclass
class LinkFix:
    md_path: str
    weibo_id: str | None
    old_link: str
    new_link: str
    resolved_path: str
    candidate_path: str
    pos: int = 0


@dataclass
class FixPlan:
    files_scanned: int = 0
    links_scanned: int = 0
    fixes: list[LinkFix] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)


def _normalize_link(link: str) -> str | None:
    value = (link or "").strip().strip("<>").strip()
    if not value or value.startswith(("http://", "https://", "data:", "mailto:", "#")):
        return None
    value = value.split("#", 1)[0].strip()
    if not value:
        return None
    return unquote(value)


def _resolve(md_path: str, link: str) -> str:
    if os.path.isabs(link):
        return os.path.normpath(link)
    return os.path.normpath(os.path.join(os.path.dirname(md_path), link))


def find_link_fixes(text: str, md_path: str) -> tuple[list[LinkFix], list[str]]:
    """在单个 md 文本里找「可安全修复」的死链；返回 (修复列表, 歧义列表)。"""
    fixes: list[LinkFix] = []
    ambiguous: list[str] = []
    file_date = archive_index.file_date_from_name(md_path)
    for raw_link, weibo_id, _dt, pos in media_report._iter_links_with_context(
        text, file_date
    ):
        link = _normalize_link(raw_link)
        if link is None:
            continue
        ext = os.path.splitext(link.split("?", 1)[0])[1].lower()
        if ext not in media_report.MEDIA_EXTENSIONS:
            continue
        resolved = _resolve(md_path, link)
        if os.path.exists(resolved):
            continue
        stem = os.path.splitext(resolved)[0]
        candidates = [
            stem + alt
            for alt in EXT_EQUIV.get(ext, ())
            if os.path.isfile(stem + alt)
        ]
        if len(candidates) > 1:
            ambiguous.append(f"{md_path}: {link}")
            continue
        if not candidates:
            continue
        candidate = candidates[0]
        candidate_ext = os.path.splitext(candidate)[1]
        # 只替换链接末尾的扩展名（保留原始相对路径写法与 URL 编码）
        old_ext = os.path.splitext(link)[1]
        if not old_ext:
            continue
        new_link = link[: -len(old_ext)] + candidate_ext
        fixes.append(
            LinkFix(
                md_path=md_path,
                weibo_id=weibo_id,
                old_link=link,
                new_link=new_link,
                resolved_path=resolved,
                candidate_path=candidate,
                pos=pos,
            )
        )
    return fixes, ambiguous


def iter_user_dirs(output_dir: str, exclude_names, only_users=None):
    """遍历数据目录下的用户目录（排除隐藏目录/排除名单/指定用户）。"""
    names = set(exclude_names or ())
    wanted = set(only_users or ())
    try:
        entries = sorted(os.listdir(output_dir))
    except OSError:
        return
    for name in entries:
        if name.startswith(".") or name in names:
            continue
        if wanted and name not in wanted:
            continue
        path = os.path.join(output_dir, name)
        if os.path.isdir(path):
            yield name, path


def plan_user_dir(user_dir: str, exclude_dirs) -> FixPlan:
    plan = FixPlan()
    for md_path in archive_index.iter_active_md_paths(user_dir, exclude_dirs):
        plan.files_scanned += 1
        try:
            text = archive_index.read_md_text(md_path)
        except OSError:
            continue
        fixes, ambiguous = find_link_fixes(text, md_path)
        plan.links_scanned += len(fixes)
        plan.fixes.extend(fixes)
        plan.ambiguous.extend(ambiguous)
    return plan


def plan_output_dir(output_dir: str, exclude_dirs, only_users=None) -> FixPlan:
    merged = FixPlan()
    for user_name, user_dir in iter_user_dirs(output_dir, exclude_dirs, only_users):
        plan = plan_user_dir(user_dir, exclude_dirs)
        merged.files_scanned += plan.files_scanned
        merged.links_scanned += plan.links_scanned
        merged.fixes.extend(plan.fixes)
        merged.ambiguous.extend(plan.ambiguous)
    return merged


def _next_backup_path(path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = f"{path}.bak.{stamp}"
    index = 1
    while os.path.exists(candidate):
        candidate = f"{path}.bak.{stamp}_{index}"
        index += 1
    return candidate


def apply_fixes(md_path: str, fixes: list[LinkFix], backup: bool = True) -> bool:
    """把修复应用到单个 md；返回是否有改动。"""
    try:
        with open(md_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as e:
        _get_logger().warning("读取失败，跳过: %s（%s）", md_path, e)
        return False
    # 按位置从后往前替换，避免位置偏移
    ordered = sorted(fixes, key=lambda item: item.pos, reverse=True)
    changed = False
    for fix in ordered:
        if fix.old_link not in text:
            continue
        # 只在链接出现的位置替换一次：优先使用正则匹配该行
        pattern = re.compile(r"(\]\(|<)" + re.escape(fix.old_link) + r"(\)|>)")
        new_text, count = pattern.subn(
            lambda match: match.group(1) + fix.new_link + match.group(2),
            text,
            count=1,
        )
        if count:
            text = new_text
            changed = True
    if not changed:
        return False
    if backup:
        backup_path = _next_backup_path(md_path)
        import shutil

        shutil.copy2(md_path, backup_path)
        _get_logger().info("已备份: %s", backup_path)
    directory = os.path.dirname(os.path.abspath(md_path)) or "."
    import tempfile

    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(md_path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            writer.write(text)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(tmp_path, md_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="修复 md 链接扩展名与实际落盘文件不一致的存量死链（默认 dry-run）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="数据目录（默认读 config.json 的 output_directory）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只输出清单（默认行为，可显式写）"
    )
    parser.add_argument(
        "--fix", action="store_true", help="实际修改（默认只输出清单）"
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="不生成 .bak.<时间戳> 备份（不推荐）"
    )
    parser.add_argument(
        "--users", nargs="*", default=None, help="只处理指定用户昵称/目录名"
    )
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 输出清单（便于程序处理）"
    )
    return parser


def _resolve_output_dir(args) -> str:
    if args.output_dir:
        return os.path.abspath(args.output_dir)
    from util.config import config_migrate
    from util.config import config_paths

    config_path, _example = config_paths.config_files(PROJECT_DIR)
    if not os.path.isfile(config_path):
        raise FileNotFoundError(
            f"未找到 {config_path}，请用 --output-dir 显式指定数据目录"
        )

    config = config_migrate.load_config_file(config_path)
    configured = str(config.get("output_directory") or "weibo_data")
    if os.path.isabs(configured):
        return configured
    return os.path.abspath(os.path.join(PROJECT_DIR, configured))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.fix and args.dry_run:
        print("--dry-run 与 --fix 不能同时使用", file=sys.stderr)
        return 2
    try:
        output_dir = _resolve_output_dir(args)
    except Exception as e:  # noqa: BLE001 - 配置读取失败直接给出可读错误
        print(f"无法确定数据目录：{e}", file=sys.stderr)
        return 2
    if not os.path.isdir(output_dir):
        print(f"数据目录不存在：{output_dir}", file=sys.stderr)
        return 2

    exclude_dirs = list(archive_index.DEFAULT_EXCLUDE_DIRS)
    plan = plan_output_dir(output_dir, exclude_dirs, args.users)
    # 同一 md 的修复按文件分组
    by_file: dict[str, list[LinkFix]] = {}
    for fix in plan.fixes:
        by_file.setdefault(fix.md_path, []).append(fix)

    if args.json:
        import json

        payload = {
            "mode": "fix" if args.fix else "dry-run",
            "output_dir": output_dir,
            "files_scanned": plan.files_scanned,
            "links_fixable": len(plan.fixes),
            "files_to_change": len(by_file),
            "ambiguous": plan.ambiguous,
            "fixes": [
                {
                    "md_path": fix.md_path,
                    "weibo_id": fix.weibo_id,
                    "old": fix.old_link,
                    "new": fix.new_link,
                    "candidate": fix.candidate_path,
                }
                for fix in plan.fixes
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"模式：{'修复' if args.fix else 'dry-run（只读）'}")
        print(f"数据目录：{output_dir}")
        print(f"扫描文件：{plan.files_scanned} 个；可修复死链：{len(plan.fixes)} 条"
              f"（涉及 {len(by_file)} 个文件）")
        if plan.ambiguous:
            print(f"歧义（多个候选，未处理）：{len(plan.ambiguous)} 条")
            for item in plan.ambiguous:
                print(f"  - {item}")
        for md_path in sorted(by_file):
            fixes = by_file[md_path]
            print(f"\n{md_path}")
            for fix in fixes:
                print(f"  微博 {fix.weibo_id or '未知'}: {fix.old_link} → {fix.new_link}")

    if not args.fix:
        print("\ndry-run：未修改任何文件；确认清单后加 --fix 执行（默认备份）。")
        return 0

    changed_files = 0
    for md_path, fixes in sorted(by_file.items()):
        if apply_fixes(md_path, fixes, backup=not args.no_backup):
            changed_files += 1
    print(f"\n修复完成：修改 {changed_files} 个文件，共 {len(plan.fixes)} 条死链。")
    return 0


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
