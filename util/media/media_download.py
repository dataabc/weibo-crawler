"""媒体文件下载。

把 ``Weibo.download_one_file`` 的核心下载逻辑抽成纯函数：校验已存在文件、
流式下载、Content-Length/文件头尾部校验、扩展名修正、原子落盘、失败重试与
not_downloaded.txt 记录。与 Weibo 实例的耦合通过回调参数注入（原子写、EXIF/
时间、sqlite 插入、失败记录路径）。

- 已存在且校验通过 → 不消耗流量，返回原路径并清理历史失败记录；
- 0 字节/损坏 → 删除重下；
- HTML 错误页、长度不符、明显不完整 → 不落盘，重试；
- 失败 → 追加 not_downloaded.txt 记录并返回 None。"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable

from util.core import errors

def _load_requests():
    """按需导入 requests，兼容只注册了 stub 的测试环境。"""
    try:
        import requests  # noqa: F401
        return requests
    except Exception:  # noqa: BLE001
        return None


class _RequestExceptionFallback(Exception):
    pass


def _infer_extension_from_response(url: str, content_type: str) -> str:
    """从 URL 后缀 / Content-Type 兜底推断扩展名（返回含点或空串）。"""
    url_path = url.split("?")[0]
    inferred = os.path.splitext(url_path)[1].lower().strip(".")
    allowed = {
        "mp4", "mov", "webm", "gif", "bmp", "tiff",
        "jpg", "jpeg", "png", "webp",
    }
    if inferred in allowed:
        return "." + inferred
    content_type = (content_type or "").lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
        "image/gif": ".gif",
    }
    for marker, ext in mapping.items():
        if marker in content_type:
            return ext
    return "." + inferred if inferred else ""


def download_one_file(
    url: str,
    file_path: str,
    headers: dict,
    *,
    type: str = "img",
    clear_record: Callable[[], object] | None = None,
    atomic_write_bytes: Callable[[str, bytes], object] | None = None,
    finalize: Callable[[str, str | None], object] | None = None,
    on_success_sqlite: Callable[[str, bytes], object] | None = None,
    validate_file: Callable[[str], tuple[bool, str]] | None = None,
    check_content_length: Callable[[str | None, int], tuple[bool, str]] | None = None,
    validate_bytes: Callable | None = None,
    image_extensions: set | None = None,
    video_extensions: set | None = None,
    session_factory: Callable[[], object] | None = None,
    sleep_func: Callable[[float], None] | None = None,
    logger=None,
    max_try: int = 3,
    stall_timeout: int = 60,
) -> str | None:
    """下载单个文件（图片/视频），返回最终保存路径；失败返回 None。"""

    def _log_warning(message, *args):
        if logger is not None:
            logger.warning(message, *args)

    def _log_debug(message):
        if logger is not None:
            logger.debug(message)

    requests_module = _load_requests()
    _sleep = sleep_func or time.sleep
    request_exception = _RequestExceptionFallback
    if requests_module is not None:
        request_exception = getattr(
            getattr(requests_module, "exceptions", object),
            "RequestException",
            _RequestExceptionFallback,
        )
    saved_path = None
    try:
        media_dir = os.path.dirname(os.path.abspath(file_path)) or os.getcwd()
        os.makedirs(media_dir, exist_ok=True)

        file_exist = os.path.isfile(file_path)
        file_size = os.path.getsize(file_path) if file_exist else 0

        if file_exist and file_size > 0:
            if validate_file is None:
                return file_path
            ok, reason = validate_file(file_path)
            if ok:
                if clear_record is not None:
                    clear_record()
                return file_path
            _log_warning("已有文件校验失败，删除后重下: %s（%s）", file_path, reason)
            try:
                os.remove(file_path)
            except OSError as exc:
                _log_warning("删除损坏文件失败: %s", exc)
        elif file_exist:
            try:
                os.remove(file_path)
            except OSError as exc:
                _log_warning("删除空文件失败: %s", exc)

        if requests_module is None and session_factory is None:
            _log_warning("requests 未安装，无法下载: %s", url[:80])
            return None
        session = session_factory() if session_factory is not None else requests_module.Session()
        adapter = getattr(getattr(requests_module, "adapters", None), "HTTPAdapter", None)
        if adapter is not None:
            try:
                session.mount("http://", adapter(max_retries=2))
                session.mount("https://", adapter(max_retries=2))
            except Exception:  # noqa: BLE001
                pass

        try_count = 0
        success = False
        detected_extension = None
        downloaded = b""

        while try_count < max_try:
            try:
                response = session.get(
                    url,
                    headers=headers,
                    timeout=(5, 30),
                    verify=False,
                    stream=True,
                )
                response.raise_for_status()

                chunks = []
                last_data_time = time.time()
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        chunks.append(chunk)
                        last_data_time = time.time()
                    if time.time() - last_data_time > stall_timeout:
                        _log_warning(
                            "下载停滞(%ss无数据)，跳过: %s...", stall_timeout, url[:80]
                        )
                        raise request_exception(
                            f"下载停滞：连续 {stall_timeout} 秒未收到数据"
                        )
                downloaded = b"".join(chunks)
                try_count += 1

                if not downloaded:
                    _log_warning(
                        "下载内容为空: %s... (%s/%s)", url[:80], try_count, max_try
                    )
                    continue

                if check_content_length is not None:
                    ok, reason = check_content_length(
                        response.headers.get("Content-Length"), len(downloaded)
                    )
                    if not ok:
                        _log_warning(
                            "下载长度不符: %s...（%s，尝试 %s/%s）",
                            url[:80], reason, try_count, max_try,
                        )
                        continue

                expected_ext = os.path.splitext(file_path)[1].lower()
                if validate_bytes is not None:
                    ok, reason, _kind, detected_extension = validate_bytes(
                        downloaded,
                        url,
                        response.headers.get("Content-Type", ""),
                        expected_ext,
                    )
                    if not ok:
                        _log_warning(
                            "下载内容校验失败: %s...（%s，尝试 %s/%s）",
                            url[:80], reason, try_count, max_try,
                        )
                        continue

                if not detected_extension:
                    detected_extension = _infer_extension_from_response(
                        url, response.headers.get("Content-Type", "")
                    )

                if detected_extension:
                    current_ext = os.path.splitext(file_path)[1].lower()
                    image_exts = set(image_extensions or ())
                    video_exts = set(video_extensions or ())
                    same_class = (
                        current_ext in image_exts
                        and detected_extension.lower() in image_exts
                    ) or (
                        current_ext in video_exts
                        and detected_extension.lower() in video_exts
                    )
                    if type == "img" or not same_class:
                        file_path = re.sub(
                            r"\.\w+$", detected_extension, file_path
                        )

                if atomic_write_bytes is not None:
                    atomic_write_bytes(file_path, downloaded)
                else:
                    with open(file_path, "wb") as handle:
                        handle.write(downloaded)
                saved_path = file_path
                _log_debug("[DEBUG] save " + file_path)
                if os.path.getsize(file_path) <= 0:
                    _log_warning("保存后文件为空，删除重试: %s", file_path)
                    os.remove(file_path)
                    saved_path = None
                    continue

                if finalize is not None:
                    try:
                        finalize(file_path, detected_extension)
                    except Exception as exc:  # noqa: BLE001
                        _log_warning("下载后处理失败（不影响文件）: %s", exc)

                success = True
                _log_debug("[DEBUG] success " + url + "  " + str(try_count))
                break

            except Exception as exc:  # noqa: BLE001
                # 代码缺陷（NameError/TypeError 等）不应被当成网络失败重试；
                # 直接打堆栈并结束本文件，正常网络失败路径不变。
                if errors.is_programming_error(exc):
                    if logger is not None:
                        logger.exception(
                            "媒体下载出现程序性错误（疑似代码缺陷，不再重试）: %s", exc
                        )
                    break
                # 部分测试/环境把 RequestException 定义为普通 Exception）
                try_count += 1
                _log_warning(
                    "[ERROR] 请求失败，错误信息：%s。尝试次数：%s/%s",
                    exc, try_count, max_try,
                )
                _sleep(2 ** try_count)

        if success:
            if on_success_sqlite is not None:
                on_success_sqlite(file_path, downloaded)
            if clear_record is not None:
                clear_record()
            return file_path

        _log_warning("下载失败（已重试 %d 次）: %s", max_try, url[:120])
        if saved_path and os.path.isfile(saved_path):
            try:
                os.remove(saved_path)
            except OSError as exc:
                _log_warning("删除失败文件失败: %s", exc)
        return None

    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.exception("%s", exc)
        if saved_path and os.path.isfile(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass
        return None
