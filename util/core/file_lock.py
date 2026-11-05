"""跨进程文件锁：让主爬与评论回填互斥，避免并发整文件读改写互相覆盖。

实现：
- POSIX（macOS/Linux）：``fcntl.flock``。进程退出时内核自动释放，磁盘上
  残留的锁文件不会造成永久死锁；锁文件本身只作为 flock 的锚点。
- Windows（无 fcntl）：退化为 ``O_CREAT|O_EXCL`` 创建锁文件；进程崩溃时
  可能残留，需要手动删除（次要平台，仅作兜底）。"""

from __future__ import annotations

import os

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None


class LockBusy(RuntimeError):
    """锁已被其他进程占用。"""

    def __init__(self, path: str, holder: str = ""):
        self.path = path
        self.holder = holder
        message = "锁被占用: %s" % path
        if holder:
            message += "（持有者: %s）" % holder
        super().__init__(message)


class FileLock:
    """优先非阻塞获取的跨进程文件锁，支持 ``with`` 语法。"""

    def __init__(self, path: str):
        self.path = path
        self._handle = None
    def acquire(self, blocking: bool = False) -> "FileLock":
        directory = os.path.dirname(os.path.abspath(self.path)) or os.getcwd()
        os.makedirs(directory, exist_ok=True)
        if fcntl is not None:
            self._acquire_posix(blocking)
        else:
            self._acquire_windows()
        return self

    def _acquire_posix(self, blocking: bool) -> None:
        handle = open(self.path, "a+", encoding="utf-8")
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(handle.fileno(), flags)
        except OSError:
            holder = self._read_holder(handle)
            handle.close()
            raise LockBusy(self.path, holder)
        self._handle = handle
        self._write_holder(handle)

    def _acquire_windows(self) -> None:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = ""
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    holder = handle.read().strip()
            except OSError:
                pass
            raise LockBusy(self.path, holder)
        self._handle = os.fdopen(fd, "w", encoding="utf-8")
        self._write_holder(self._handle)

    @staticmethod
    def _read_holder(handle) -> str:
        try:
            handle.seek(0)
            return handle.read().strip()
        except OSError:
            return ""

    def _write_holder(self, handle) -> None:
        try:
            handle.seek(0)
            handle.truncate()
            handle.write("%d %s\n" % (os.getpid(), "weibo-crawler"))
            handle.flush()
        except OSError:
            pass

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()
        else:
            handle.close()
            try:
                os.remove(self.path)
            except OSError:
                pass

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


def lock_path_for_output(output_dir: str) -> str:
    """主爬与回填共用的锁文件路径（放在数据输出目录根部）。"""
    return os.path.join(os.path.abspath(output_dir), ".weibo_crawler.lock")


def resolve_output_dir(project_root: str, configured) -> str:
    """把 ``output_directory`` 配置解析为绝对路径（绝对路径原样返回）。"""
    value = str(configured or "weibo_data")
    if os.path.isabs(value):
        return value
    return os.path.join(project_root, value)
