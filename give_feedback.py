from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json

from paper_agent.storage import append_jsonl

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
FEEDBACK_PATH = BASE_DIR / "feedback.jsonl"
try:
    _config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    _output_dir = Path(_config.get("output_dir", "reports"))
    _output_dir = _output_dir if _output_dir.is_absolute() else BASE_DIR / _output_dir
    LATEST_PATH = _output_dir / "latest.json"
except (OSError, json.JSONDecodeError):
    LATEST_PATH = BASE_DIR / "reports" / "latest.json"


def main() -> int:
    if not LATEST_PATH.exists():
        print("尚无 latest.json。请先运行 run_now.bat 生成一份推荐。")
        return 1
    try:
        paper = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读取最新推荐失败：{exc}")
        return 1
    if not paper.get("id"):
        print("最新报告没有推荐论文，暂时无法提交反馈。")
        return 1
    print("\n今日论文：", paper.get("title", "无标题"))
    while True:
        raw = input("总体推荐质量 1-5 分（1=很不相关，5=非常值得读）：").strip()
        try:
            rating = int(raw)
            if 1 <= rating <= 5:
                break
        except ValueError:
            pass
        print("请输入 1 到 5 的整数。")
    raw_tags = input("相关主题标签（逗号分隔，如 GNN, diffusion, enzyme design；可留空）：").strip()
    tags = [x.strip() for x in raw_tags.replace("，", ",").split(",") if x.strip()]
    note = input("可选备注（如‘不想看综述’或‘更关注实验验证’）：").strip()
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "paper_id": paper.get("id", ""),
        "title": paper.get("title", ""),
        "rating": rating,
        "tags": tags,
        "note": note,
    }
    append_jsonl(FEEDBACK_PATH, record)
    print("反馈已保存。后续排序会根据标签和评分调整候选优先级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
