"""导出 /v1/voice/tts/live 控制面协议的 JSON Schema 到 docs/technical/。

双端契约的唯一权威定义在 src/psych_support_bot/infra/voice/protocol.py；
本脚本只做落盘。协议或模型变更后重跑：

    .venv/bin/python scripts/export_voice_protocol.py

前端特征测试（tests/frontend/protocol.test.mjs）读取导出的 schema 文件
交叉校验 static/js/voice/protocol.js 的事件名常量——schema 与前端常量
漂移会直接红。
"""

from __future__ import annotations

import json
from pathlib import Path

from psych_support_bot.infra.voice.protocol import export_json_schema

OUT = Path(__file__).resolve().parent.parent / "docs" / "technical" / "voice-protocol.schema.json"


def main() -> None:
    schema = export_json_schema()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
