"""已存档微博的编辑检测与更新。

逻辑：内容指纹变化作为「疑似编辑」的第一道信号，再用正文/媒体/链接的内容
签名二次确认（避免接口字段波动、媒体 URL 签名变化被误判为编辑）；首次见到
时以 md 当前正文为基线；确认编辑后保存旧版快照、更新 md 正文与 edited_at、
记录变化供报告使用。长文详情页取全文失败时不做编辑写回，保留已有存档全文
并写入变化报告。依赖的 wb 属性/方法由调用方（Weibo 实例）提供。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

from util.core import archive_state
from util.core import markdown_blocks

logger = logging.getLogger("weibo")


def _location_values(weibo) -> list[str]:
    """收集微博（含被转发微博）的定位文字，用于签名时忽略显示位置差异。"""
    values = []
    for item in (weibo or {}, (weibo or {}).get("retweet") or {}):
        value = str((item or {}).get("location") or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _strip_location_lines(body: str, locations: list[str]) -> str:
    """去掉正文里的定位行（定位改到媒体之后显示，不能因此被判成编辑）。"""
    if not body or not locations:
        return body
    wanted = set(locations)
    return "\n".join(
        line for line in body.split("\n") if line.strip() not in wanted
    )


def _same_content(wb, block, weibo, created_at) -> bool:
    """md 旧正文与「最新内容重渲染结果」是否语义一致（正文/媒体/链接集合）。"""
    if block is None:
        return False
    locations = _location_values(weibo)
    old_body = _strip_location_lines(wb._normalize_block_body(block.body), locations)
    rendered = wb._render_weibo_block_body(weibo, created_at)
    new_body = _strip_location_lines(wb._normalize_block_body(rendered), locations)
    if not old_body or not new_body:
        return False
    return wb._content_signature(old_body) == wb._content_signature(new_body)


def _record_long_text_failure(wb, weibo_id, created_at, md_rel, *, archived) -> None:
    """记录「长文详情页取全文失败」：编辑流程跳过写回，供变化报告展示。"""
    items = getattr(wb, "long_text_failures", None)
    if not isinstance(items, list):
        items = []
        wb.long_text_failures = items
    items.append(
        {
            "weibo_id": str(weibo_id),
            "created_at": str(created_at or ""),
            "md_path": str(md_rel or ""),
            "archived": bool(archived),
        }
    )
    logger.warning(
        "长文获取失败（详情页取全文失败），%s: %s",
        "已保留旧存档、本轮不更新" if archived else "新微博仅保存截断正文",
        weibo_id,
    )


def apply(wb, wrote_count) -> object:
    """检测已存档微博的编辑，保存旧版快照并把 md 正文更新为最新版。

    - 状态索引已有记录：内容指纹变化 → 判定编辑；
    - 首次见到（状态索引无记录）：如果 md 里已有该微博旧块，把 md 当前
      正文当作旧版本与最新内容比较，不同也判定编辑（首次全量即可检测）；
    - 编辑处理：保存旧版快照 → 用最新内容重渲染块正文 → 写 edited_at →
      备份 + 原子写 → 更新状态索引 → 记录 edit_changes（供变化报告）。
    """
    batch = wb.weibo[wrote_count:]
    if not batch:
        return
    state = wb._ensure_archive_state()
    user_dir = wb.get_filepath("markdown")
    docs = {}
    dirty_files = set()
    new_written = getattr(wb, "new_written_weibo", None) or {}

    def load_doc(md_file) -> object:
        doc = docs.get(md_file)
        if doc is not None:
            return doc
        try:
            with open(md_file, "r", encoding="utf-8") as handle:
                original = handle.read()
        except OSError as exc:
            wb._record_write_failure("编辑更新", md_file, f"读取失败: {exc}")
            return None
        fallback = os.path.splitext(os.path.basename(md_file))[0]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", fallback):
            fallback = None
        doc = markdown_blocks.parse_markdown(original, fallback_date=fallback)
        docs[md_file] = doc
        return doc

    def record_change(wid, created_at, md_file, snapshot_rel, old_edit_at, new_edit_at, first_seen) -> None:
        changes = getattr(wb, "edit_changes", None)
        if not isinstance(changes, list):
            changes = []
            wb.edit_changes = changes
        changes.append(
            {
                "weibo_id": wid,
                "created_at": created_at,
                "md_path": os.path.relpath(md_file, user_dir) if md_file else "",
                "snapshot": snapshot_rel,
                "old_edit_at": old_edit_at,
                "new_edit_at": new_edit_at,
                "first_seen": first_seen,
            }
        )

    for w in batch:
        wid = str(w.get("id") or "")
        if not wid:
            continue
        created_at = str(w.get("created_at") or "")
        edit_at = str(w.get("edit_at") or "")
        visible = archive_state.normalize_visibility(w.get("visible"))
        fingerprint = archive_state.content_fingerprint(
            text=w.get("text", ""),
            pics=w.get("pics", ""),
            video_url=w.get("video_url", ""),
            live_photo_url=w.get("live_photo_url", ""),
            article_url=w.get("article_url", ""),
            topics=w.get("topics", ""),
            edit_at=edit_at,
            visible=json.dumps(visible, sort_keys=True, ensure_ascii=False),
        )
        item = archive_state.get_weibo(state, wid)
        first_seen = item is None
        old_visibility = (item or {}).get("visibility") or {}
        old_fp = str((item or {}).get("content_hash") or "")
        old_edit_at = str((item or {}).get("last_edit_at") or "")
        if not first_seen and old_visibility and visible and old_visibility != visible:
            changes = getattr(wb, "visibility_changes", None)
            if not isinstance(changes, list):
                changes = []
                wb.visibility_changes = changes
            changes.append(
                {
                    "weibo_id": wid,
                    "created_at": created_at,
                    "old": old_visibility,
                    "new": visible,
                    "old_label": archive_state.visibility_label(old_visibility),
                    "new_label": archive_state.visibility_label(visible),
                }
            )
        baseline = first_seen or not old_fp
        md_file, md_rel = wb._locate_block_file(wid, item or {}, created_at)
        long_text_failed = bool(w.get("long_text_failed"))
        if long_text_failed:
            _record_long_text_failure(
                wb, wid, created_at, md_rel, archived=not first_seen
            )
        if baseline:
            # 首次见到 / 状态索引重建后无指纹：把 md 当前正文当作旧版本比较
            written = new_written.get(wid) or ""
            if not md_rel and written:
                md_rel = os.path.relpath(written, user_dir)
            doc = load_doc(md_file) if md_file else None
            block = doc.find(wid) if doc is not None else None
            if long_text_failed and block is not None:
                # 长文没取到全文：不能拿截断正文覆盖已存档全文，本轮只登记状态
                archive_state.upsert_weibo(
                    state,
                    wid,
                    created_at=created_at or (item or {}).get("created_at", ""),
                    md_path=md_rel,
                    content_hash=fingerprint,
                    last_edit_at=edit_at,
                    visibility=visible,
                    touch=True,
                )
                continue
            should_edit = bool(block is not None) and not _same_content(
                wb, block, w, created_at
            )
            if not should_edit:
                archive_state.upsert_weibo(
                    state,
                    wid,
                    created_at=created_at or (item or {}).get("created_at", ""),
                    md_path=md_rel,
                    content_hash=fingerprint,
                    last_edit_at=edit_at,
                    visibility=visible,
                    touch=True,
                )
                continue
            # 无基准场景下 md 内容不同：登记状态后按编辑处理
            item = archive_state.upsert_weibo(
                state,
                wid,
                created_at=created_at or (item or {}).get("created_at", ""),
                md_path=md_rel,
                content_hash=fingerprint,
                last_edit_at=edit_at,
                visibility=visible,
                touch=True,
            )
            old_edit_at = ""
        else:
            item = archive_state.upsert_weibo(
                state,
                wid,
                content_hash=fingerprint,
                last_edit_at=edit_at,
                visibility=visible,
                touch=True,
            )
            if not old_fp or old_fp == fingerprint:
                continue
            if not md_file:
                wb._record_write_failure("编辑更新", wid, "找不到对应 md 文件")
                continue
            doc = load_doc(md_file)
            block = doc.find(wid) if doc is not None else None
            if block is None:
                wb._record_write_failure("编辑更新", md_file, f"块未找到: {wid}")
                continue
            if long_text_failed:
                # 长文没取到全文：不写回（保留旧存档全文），失败已在上面记录
                continue
            if _same_content(wb, block, w, created_at):
                # 指纹变化只来自接口易变字段（如编辑时间/媒体链接签名），内容没变：
                # 只更新状态索引，不重写 md、不产生 .bak / 编辑历史快照。
                logger.info(
                    "内容指纹变化但正文/媒体/链接一致，忽略编辑（weibo_id=%s）", wid
                )
                continue
        if md_rel:
            item["md_path"] = md_rel
        try:
            snapshot_rel = wb._save_edit_snapshot(
                wid,
                block,
                old_edit_at,
                created_at,
                item.get("visibility"),
                md_file,
            )
        except OSError as exc:
            wb._record_write_failure("编辑快照", wid, f"保存失败: {exc}")
            continue
        rendered = wb._render_weibo_block_body(w, created_at)
        # 平台列表接口不返回 edit_at：只对检测到编辑的少量微博补查详情接口
        if not edit_at:
            fetched_edit_at = wb._fetch_edit_at(wid)
            if fetched_edit_at:
                edit_at = fetched_edit_at
        effective_edit_at = edit_at or "平台未提供（详情接口变动）"
        wb._update_block_body(block, rendered, effective_edit_at)
        archive_state.record_version(
            state,
            wid,
            fingerprint=old_fp or "baseline",
            saved_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            path=snapshot_rel,
        )
        record_change(
            wid, created_at, md_file, snapshot_rel,
            old_edit_at, effective_edit_at, first_seen,
        )
        dirty_files.add(md_file)
    for md_file in sorted(dirty_files):
        doc = docs.get(md_file)
        if doc is None:
            continue
        try:
            final_content = doc.render()
            wb._backup_markdown(md_file)
            wb._atomic_write_text(md_file, final_content)
            logger.info("编辑更新已写回: %s（%d 个块）", md_file, len(doc.blocks))
        except Exception as exc:  # noqa: BLE001
            wb._record_write_failure("编辑更新写回", md_file, str(exc))
    try:
        archive_state.save_state(state, wb._state_path, wb._atomic_write_text)
    except Exception as exc:  # noqa: BLE001
        wb._record_write_failure("状态索引保存", wb._state_path, str(exc))


def save_edit_snapshot(wb, weibo_id, block, version_edit_at, created_at, visibility, md_file=None) -> object:
    """保存旧版快照（一条微博的一个旧版本），返回相对用户目录的路径。

    快照放在 ``编辑历史/YYYY-MM/``，正文里的媒体链接原本相对原 md 的
    月目录；这里改写为「相对快照目录」的路径（如 ``../../YYYY-MM/原创微博图片/x.jpg``），
    保证打开快照时图片仍能显示。
    """
    user_dir = wb.get_filepath("markdown")
    base = str(getattr(wb, "edit_snapshot_dir", "编辑历史") or "编辑历史")
    root = base if os.path.isabs(base) else os.path.join(user_dir, base)
    # 年/月目录与文件名日期：取「该旧版本文件（微博）发布时间」；取不到才兜底
    day = str(created_at or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        day = str(version_edit_at or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        day = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join(root, day[:7])
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, f"{day}_{weibo_id}.md")
    if os.path.exists(path):
        stamp = datetime.now().strftime("%H%M%S")
        path = os.path.join(dir_path, f"{day}_{weibo_id}_{stamp}.md")
    body_lines = []
    for line in block.body.split("\n"):
        if markdown_blocks.WEIBO_ID_RE.search(line):
            continue
        if markdown_blocks.EDITED_AT_RE.search(line):
            continue
        body_lines.append(line)
    body_text = "\n".join(body_lines).strip()
    if md_file:
        try:
            snap_dir = dir_path
            rel_base = os.path.relpath(os.path.dirname(md_file), snap_dir)
            prefixes = {
                str(value).strip("/")
                for value in (getattr(wb, "markdown_file_dir", {}) or {}).values()
                if str(value).strip("/")
            }

            def _rewrite(match):
                target = match.group(2)
                first = target.split("/", 1)[0]
                if target.startswith(("http://", "https://")) or first not in prefixes:
                    return match.group(0)
                return f"{match.group(1)}{rel_base}/{target})"

            body_text = re.sub(
                r"(!?\[[^\]]*\]\()([^)]+)\)", _rewrite, body_text
            )
        except (OSError, ValueError):
            pass
    snapshot = "\n".join(
        [
            f"# 微博 {weibo_id} · 版本快照",
            "",
            f"保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"微博发布时间: {created_at or '未知'}",
            f"该版本最后编辑时间: {version_edit_at or '未知'}",
            f"可见性: {archive_state.visibility_label(visibility)}",
            "",
            "---",
            "",
            "## 正文",
            "",
            body_text,
            "",
        ]
    )
    wb._atomic_write_text(path, snapshot)
    return os.path.relpath(path, user_dir)
