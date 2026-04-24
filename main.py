"""
大島樂眠 AI 輔睡員 — Railway 入口點
main.py

Skill 資料夾名稱含 hyphen（例：lovefu-cs-brain），不符合 Python 模組命名規則。
此檔案在啟動時把 hyphenated 資料夾註冊成 underscored package，
讓 `from lovefu_cs_brain.scripts.app import app` 這類 import 能正常運作。

Railway / 本機跑：
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import importlib.util
import sys
import pathlib

ROOT = pathlib.Path(__file__).parent.resolve()

SKILLS = [
    "lovefu-cs-brain",
    "lovefu-cs-guard",
    "lovefu-cs-memory",
    "lovefu-cs-persona",
    "lovefu-cs-shopline",
    "lovefu-cs-knowledge",
    # 新增（2026-04-15）
    "lovefu-cs-logistics",   # WMS 暢流物流
    "lovefu-cs-instore",     # 門市試躺追客
    "lovefu-cs-handoff",     # 即時轉人工斷點
]


def _register_skill_as_package(skill_dir: str) -> None:
    """把 hyphenated 資料夾註冊成 underscored Python package。"""
    pkg_name = skill_dir.replace("-", "_")
    pkg_path = ROOT / skill_dir

    if not pkg_path.is_dir():
        return

    # 確保根目錄有 __init__.py
    init_file = pkg_path / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        pkg_name,
        str(init_file),
        submodule_search_locations=[str(pkg_path)],
    )
    if spec is None or spec.loader is None:
        return

    module = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = module
    spec.loader.exec_module(module)


# 啟動時註冊所有 skill
for skill in SKILLS:
    _register_skill_as_package(skill)


# 載入 brain 的 FastAPI app（必須在 register 之後）
from lovefu_cs_brain.scripts.app import app  # noqa: E402


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
