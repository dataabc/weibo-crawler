"""评论刷新目标选择。

「已存档微博评论轮转刷新」逻辑：确定本轮要刷新的已存档微博列表——
按「最久未刷新优先」排序，单轮预算限制条数；scope=all 覆盖全部存档，
scope=recent 只看最近 N 天发布的微博。新写入/待重试项由调用方单独加入，
不受预算限制。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from util.core.time_format import DTFORMAT


def select_archive_refresh_candidates(
    weibo_list,
    state,
    locate_block_file,
    *,
    scope: str = "all",
    recent_days: int = 30,
    limit: int = 300,
    skip_ids=None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """返回本轮要刷新的已存档微博 [(weibo_id, md_file)]（已排序并截断）。

    - ``scope``：all / recent（recent 只看最近 recent_days 天发布的）；
    - ``limit``：单轮预算（<=0 表示不限制）；
    - ``skip_ids``：已在其它目标里的微博 ID（新写入/待重试），不重复加入；
    - ``locate_block_file``：``(weibo_id, item, created_at) -> (md_file, rel)``，
      由调用方提供（块文件定位逻辑在 weibo.py）。
    """
    scope = str(scope or "all").lower()
    if scope not in ("all", "recent"):
        return []
    skip = {str(i) for i in (skip_ids or ())}
    cutoff = None
    if scope == "recent":
        cutoff = (now or datetime.now()) - timedelta(days=int(recent_days or 30))
    candidates = []
    for weibo in weibo_list or []:
        weibo_id = str(weibo.get("id") or "")
        if not weibo_id or weibo_id in skip:
            continue
        created_at = str(weibo.get("created_at") or "")
        if cutoff is not None and created_at:
            try:
                if datetime.strptime(created_at, DTFORMAT) < cutoff:
                    continue
            except ValueError:
                pass
        item = (state or {}).get("weibos", {}).get(weibo_id) or {}
        md_file, _rel = locate_block_file(weibo_id, item, created_at)
        if not md_file:
            continue
        candidates.append(
            (
                str(item.get("comment_refreshed_at") or ""),
                created_at,
                weibo_id,
                md_file,
            )
        )
    # 轮转刷新：最久没刷新的优先
    candidates.sort(key=lambda row: (row[0], row[1]))
    if limit and int(limit) > 0:
        candidates = candidates[: int(limit)]
    return [(row[2], row[3]) for row in candidates]
