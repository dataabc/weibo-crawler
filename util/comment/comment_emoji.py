"""评论表情的本地化存储与内链改写。

命名与去重（尽量让 Markdown 原始内容干净）：
- 微博表情 CDN 路径带版本段（如 ``.../t4/appstyle/expression/...`` 的 ``t4``），
  所以按版本分目录：``emoji/t4/泪.png``；没有版本段的 URL 直接放 ``emoji/泪.png``；
- 文件名只用表情 alt（如 ``泪``），不把哈希等冗余信息写进文件名；
- ``emoji_index.json`` 记录 URL → 相对路径、相对路径 → 内容摘要：
  - 同一 URL 直接复用；
  - 同名同内容（换 CDN 路径/签名）复用同一个文件；
  - 同名不同内容（平台升级换图）自动落成 ``泪-2.png``、``泪-3.png``，不串图；
- 下载失败保留原外链（宁可外链也不丢内容）。

设计：
- 解析层（comment_fetch）只把表情输出成 Markdown 外链图片；
- 写回层（comment_markdown.update_markdown_file_status）在落盘前调用
  :class:`EmojiStore` 做下载 + 链接替换。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from urllib.parse import urlparse

logger = logging.getLogger("weibo")

# 评论块里由 comment_fetch 渲染出的外链表情图片：![表情名](https://face.t.sinajs.cn/...)
EXTERNAL_EMOJI_RE = re.compile(
    r"!\[([^\]]*)\]\((https?://[^)\s]*face\.t\.sinajs\.cn/[^)\s]+)\)"
)
EMOJI_CDN_HOST_MARKERS = ("face.t.sinajs.cn",)
EMOJI_CLASS_MARKERS = ("emoji", "face", "expression")
_VALID_EXTENSIONS = (".png", ".gif", ".jpg", ".jpeg", ".webp")
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|\s]+')
EMOJI_INDEX_NAME = "emoji_index.json"
_VERSION_SEGMENT_RE = re.compile(r"^t\d+$", re.IGNORECASE)
# 已本地化/待本地化的评论表情 Markdown 图片（只匹配 emoji 目录或表情 CDN）
_EMOJI_MARKDOWN_RE = re.compile(
    r"!\[([^\]]*)\]\(([^)\s]*(?:emoji/|face\.t\.sinajs\.cn)[^)\s]*)\)"
)


def is_emoji_img(src: str, class_attr: str = "") -> bool:
    """判断一个评论里的 <img> 是否表情：表情 CDN 或 class 含 emoji/face/expression。

    CDN 判定用域名精确匹配（允许子域），避免 ``face.t.sinajs.cn.evil.com``
    这类域名被误判；与 ``comment_fetch._TextExtractor._is_emoji_img`` 口径一致。
    """
    class_text = str(class_attr or "").lower()
    if any(marker in class_text for marker in EMOJI_CLASS_MARKERS):
        return True
    host = (urlparse(str(src or "").strip()).hostname or "").lower()
    return host == "face.t.sinajs.cn" or host.endswith(".face.t.sinajs.cn")


def emoji_name_from_alt(alt: str) -> str:
    """从 alt（如 ``[微笑]``）提取文件名主干；不合法或为空返回空串。"""
    name = str(alt or "").strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1].strip()
    name = _ILLEGAL_CHARS_RE.sub("_", name).strip("._")
    return name[:40]


def emoji_stem(alt: str) -> str:
    """表情文件名主干；alt 为空时用「表情」。"""
    return emoji_name_from_alt(alt) or "表情"


def emoji_extension(src: str) -> str:
    path = urlparse(str(src or "")).path
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in _VALID_EXTENSIONS else ".png"


def emoji_version(src: str) -> str:
    """从表情 CDN 路径提取版本段（如 ``/t4/`` → ``t4``）；没有则返回空串。"""
    try:
        segments = [seg for seg in urlparse(str(src or "")).path.split("/") if seg]
    except ValueError:
        return ""
    for seg in segments:
        if _VERSION_SEGMENT_RE.match(seg):
            return seg.lower()
    return ""


def emoji_relative_path(alt: str, src: str, suffix: str = "") -> str:
    """表情文件的相对路径：``<版本>/<表情名><后缀><扩展名>``；无版本时放根目录。"""
    filename = f"{emoji_stem(alt)}{suffix}{emoji_extension(src)}"
    version = emoji_version(src)
    return f"{version}/{filename}" if version else filename


def resolve_emoji_dir(configured: str, project_root: str) -> str:
    """解析表情存储目录：绝对路径原样；相对路径按项目根解析。"""
    value = str(configured or "weibo_data/emoji").strip()
    if os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(project_root, value))


class EmojiStore:
    """表情下载 + 评论块外链改写。``download(url, dest_path)`` 返回是否成功。"""

    def __init__(self, emoji_dir: str, download, logger_=None, inline_size=0):
        self.emoji_dir = os.path.abspath(emoji_dir)
        self._download = download
        self._logger = logger_ or logger
        # >0 时把评论表情由 ![]() 改成行内 HTML <img>，按像素控制显示大小
        self.inline_size = max(0, int(inline_size or 0))
        self._attempted: set[str] = set()
        self._manifest: dict | None = None
        self.downloaded = 0
        self.failed = 0

    # ---- 索引文件：URL→相对路径，相对路径→内容摘要 ----
    def _manifest_path(self) -> str:
        return os.path.join(self.emoji_dir, EMOJI_INDEX_NAME)

    def _load_manifest(self) -> dict:
        if self._manifest is not None:
            return self._manifest
        urls: dict[str, str] = {}
        files: dict[str, str] = {}
        try:
            data = json.load(open(self._manifest_path(), encoding="utf-8"))
            if isinstance(data, dict):
                if isinstance(data.get("urls"), dict):
                    urls = {str(k): str(v) for k, v in data["urls"].items()}
                if isinstance(data.get("files"), dict):
                    files = {str(k): str(v) for k, v in data["files"].items()}
        except (OSError, ValueError):
            pass
        self._manifest = {"urls": urls, "files": files}
        return self._manifest

    def _save_manifest(self) -> None:
        if self._manifest is None:
            return
        os.makedirs(self.emoji_dir, exist_ok=True)
        path = self._manifest_path()
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "urls": self._manifest["urls"],
                 "files": self._manifest["files"]},
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
        os.replace(temp, path)

    def _digest_file(self, path: str) -> str:
        digest = hashlib.sha1()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _absolute(self, relative: str) -> str:
        return os.path.join(self.emoji_dir, relative.replace("/", os.sep))

    def _resolve_relative(self, src: str, alt: str) -> str:
        """返回该 URL 对应的本地相对路径；必要时下载。"""
        manifest = self._load_manifest()
        urls = manifest["urls"]
        files = manifest["files"]
        cached = urls.get(src)
        if cached and os.path.isfile(self._absolute(cached)):
            return cached
        if src in self._attempted:
            return ""
        self._attempted.add(src)
        os.makedirs(self.emoji_dir, exist_ok=True)
        url_digest = hashlib.sha1(src.encode("utf-8")).hexdigest()[:10]
        temp = os.path.join(
            self.emoji_dir, f".tmp_{url_digest}{emoji_extension(src)}"
        )
        try:
            ok = bool(self._download(src, temp))
        except Exception as exc:  # noqa: BLE001 下载失败不阻断评论写入
            self._logger.warning("表情下载失败 %s: %s", src, exc)
            ok = False
        if not ok or not os.path.isfile(temp) or os.path.getsize(temp) == 0:
            self.failed += 1
            try:
                if os.path.isfile(temp):
                    os.remove(temp)
            except OSError:
                pass
            return ""
        digest = self._digest_file(temp)
        version = emoji_version(src)
        stem = emoji_stem(alt)
        ext = emoji_extension(src)
        number = 1
        relative = ""
        while number <= 99:
            suffix = "" if number == 1 else f"-{number}"
            candidate = (
                f"{version}/{stem}{suffix}{ext}" if version else f"{stem}{suffix}{ext}"
            )
            absolute = self._absolute(candidate)
            if not os.path.isfile(absolute):
                relative = candidate
                break
            existing = files.get(candidate) or self._digest_file(absolute)
            files[candidate] = existing
            if existing == digest:
                try:
                    os.remove(temp)
                except OSError:
                    pass
                urls[src] = candidate
                self._save_manifest()
                return candidate
            number += 1
        if not relative:
            self.failed += 1
            try:
                os.remove(temp)
            except OSError:
                pass
            return ""
        try:
            os.makedirs(os.path.dirname(self._absolute(relative)), exist_ok=True)
            os.replace(temp, self._absolute(relative))
            self.downloaded += 1
        except OSError as exc:  # noqa: BLE001
            self._logger.warning("表情落盘失败 %s: %s", src, exc)
            self.failed += 1
            return ""
        files[relative] = digest
        urls[src] = relative
        self._save_manifest()
        return relative

    def _to_inline_html(self, text: str) -> str:
        if self.inline_size <= 0:
            return text
        size = self.inline_size

        def _replace(match: re.Match) -> str:
            alt, src = match.group(1), match.group(2)
            return (
                f'<img src="{html.escape(src, quote=True)}" '
                f'width="{size}" height="{size}" '
                f'alt="{html.escape(alt, quote=True)}">'
            )

        return _EMOJI_MARKDOWN_RE.sub(_replace, text)

    def localize_block(self, block_text: str, md_path: str) -> str:
        """把评论块里的表情外链改写为相对 md 的内链；失败保留外链。"""
        if not block_text:
            return block_text
        compact = "face.t.sinajs.cn" not in block_text and not (
            "emoji/" in block_text and _EMOJI_MARKDOWN_RE.search(block_text)
        )
        if compact:
            return block_text
        md_dir = os.path.dirname(os.path.abspath(md_path))
        try:
            rel_dir = os.path.relpath(self.emoji_dir, md_dir).replace(os.sep, "/")
        except ValueError:  # Windows 跨盘符等极端情况
            rel_dir = self.emoji_dir.replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""

        def _replace(match: re.Match) -> str:
            alt, src = match.group(1), match.group(2)
            relative = self._resolve_relative(src, alt)
            if not relative:
                return match.group(0)
            target = f"{rel_dir}/{relative}" if rel_dir else relative
            return f"![{alt}]({target})"

        localized = EXTERNAL_EMOJI_RE.sub(_replace, block_text)
        return self._to_inline_html(localized)


def make_session_downloader(session, headers, validate_bytes=None):
    """构造基于 requests.Session 的下载器（供回填/修复脚本使用）。"""

    def _download(url: str, dest: str) -> bool:
        response = session.get(url, headers=headers, timeout=20)
        if getattr(response, "status_code", 0) != 200:
            return False
        data = response.content
        if not data:
            return False
        if validate_bytes is not None:
            ok, _reason, _kind, _ext = validate_bytes(
                data, url, response.headers.get("Content-Type")
            )
            if not ok:
                return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(data)
        return True

    return _download
