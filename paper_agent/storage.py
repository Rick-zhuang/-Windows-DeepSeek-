from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("无法读取 %s，将使用默认值：%s", path.name, exc)
        return default


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def load_feedback(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
                except json.JSONDecodeError:
                    logging.warning("忽略损坏的反馈记录（%s）", path.name)
    except OSError as exc:
        logging.warning("读取反馈文件失败：%s", exc)
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())


class RunLock:
    """Small cross-process lock to prevent Scheduler and manual runs colliding."""
    def __init__(self, path: Path, stale_after_seconds: int = 6 * 60 * 60):
        self.path = path
        self.stale_after_seconds = stale_after_seconds
        self.acquired = False

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                    file.write(json.dumps({"pid": os.getpid(), "started_at": time.time()}))
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_after_seconds and attempt == 0:
                        logging.warning("发现超过 6 小时的锁文件，尝试清理：%s", self.path)
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise RuntimeError(f"检测到另一个任务正在运行，或锁文件存在：{self.path}")
        raise RuntimeError("无法获取任务锁")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc2:
                logging.warning("无法移除锁文件 %s：%s", self.path, exc2)
