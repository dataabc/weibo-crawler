"""配置路径解析。

把「输出根目录 / 用户目录名 / 报告目录（按用户分目录）」的解析集中到一处，
供数据目录、媒体目录、各类报告目录共用，避免每处重复实现与规则漂移。

规则（与既有行为保持一致）：

- ``output_directory``：绝对路径原样使用，否则相对项目目录；
- 用户目录名：``user_id_as_folder_name=1`` 用用户 ID，否则昵称；空值回退用户 ID；
  报告目录名对非法文件名字符做替换（数据目录保持历史行为不替换）；
- 报告目录：``<报告根目录>/<用户目录名>/``；报告根目录为空用 default（默认
  ``report``），绝对路径原样，相对路径相对 ``output_directory``。
"""

from __future__ import annotations

import os
import re

_ILLEGAL_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')


def safe_dir_name(name: str | None, fallback: str = "unknown") -> str:
    """目录名安全化（非法文件名字符替换为下划线，空值回退）。"""
    value = _ILLEGAL_FILENAME_RE.sub("_", str(name or "")).strip()
    return value or fallback


def config_files(project_dir: str) -> tuple[str, str]:
    """返回 (配置文件, 模板文件) 的绝对路径。

    优先级：``config/config.json`` > 根目录旧位置 ``config.json``（兼容旧部署）；
    模板同理（``config/config.json.example`` > 根目录）。get_config 与
    --update-config 共用这一处解析，避免两套规则再次漂移。
    """
    config_dir = os.path.join(project_dir, "config")
    config_path = os.path.join(config_dir, "config.json")
    if not os.path.isfile(config_path):
        legacy = os.path.join(project_dir, "config.json")
        if os.path.isfile(legacy):
            config_path = legacy
    example_path = os.path.join(config_dir, "config.json.example")
    if not os.path.isfile(example_path):
        legacy_example = os.path.join(project_dir, "config.json.example")
        if os.path.isfile(legacy_example):
            example_path = legacy_example
    return config_path, example_path


def resolve_config_file(project_dir: str, value: str) -> str:
    """解析「配置相关文件」路径（config.json 之外的 txt/conf 等）。

    规则：绝对路径原样；带目录的相对路径按项目根解析；纯文件名优先
    ``config/`` 下同名文件（存在时），否则回退项目根（兼容旧部署）。
    统一后，comment_user_filter.txt / user_id_list.txt / cookie.txt /
    logging.conf 等配置相关文件默认都放在 ``config/`` 目录下。
    """
    value = str(value or "").strip()
    if not value:
        return value
    if os.path.isabs(value):
        return value
    if os.path.dirname(value):
        return os.path.join(project_dir, value)
    in_config = os.path.join(project_dir, "config", value)
    if os.path.isfile(in_config):
        return in_config
    return os.path.join(project_dir, value)


def account_dir_name(
    user: dict | None,
    user_config: dict | None,
    user_id_as_folder_name: int | bool = 0,
    *,
    sanitize: bool = True,
) -> str:
    """用户目录名：user_id_as_folder_name 用 ID，否则昵称；空值回退 ID。

    ``sanitize=True`` 时替换非法文件名字符（报告目录用）；数据目录历史上不做
    替换，传 ``sanitize=False`` 保持行为。
    """
    user = user or {}
    user_config = user_config or {}
    if user_id_as_folder_name:
        name = str(user_config.get("user_id") or "")
    else:
        name = str(user.get("screen_name") or "")
    if not name:
        name = str(user_config.get("user_id") or "unknown")
    return safe_dir_name(name) if sanitize else name


def report_dir(
    project_dir: str,
    output_directory: str | None,
    configured: str | None,
    account_name: str,
    default: str = "report",
) -> str:
    """报告目录：``<报告根目录>/<用户目录名>/``（按用户分目录）。"""
    value = configured.strip() if isinstance(configured, str) else ""
    if not value:
        value = default
    if os.path.isabs(value):
        base_dir = value
    else:
        base_dir = os.path.join(project_dir, str(output_directory or "weibo_data"), value)
    return os.path.join(base_dir, account_name)
