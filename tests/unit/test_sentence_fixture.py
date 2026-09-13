"""分句服务端语义 fixture 回归：_split_complete_sentences 对照共享 fixture。

历史说明：fixture 原为前后端双端一致性而设（客户端朗读分句
splitIntoSentences 已随 HTTP 句队列退役——对话朗读二选一收敛到 WS live，
见 VOICE_DECISIONS.md D6），现仅钉住服务端切分语义防漂移。
golden/server 各节的期望值维持不变。
"""

import json
from pathlib import Path

from psych_support_bot.services.conversation import _split_complete_sentences

FIXTURE = Path(__file__).resolve().parent.parent / "frontend" / "fixtures" / "sentences.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_golden_both_agree() -> None:
    """纯句末标点文本：两个实现必须逐字一致（分句语义的共同基线）。"""
    for case in _fixture()["golden_both_agree"]:
        got_first, rest_first = _split_complete_sentences(case["input"], first=True)
        got_later, rest_later = _split_complete_sentences(case["input"], first=False)
        assert got_first == case["expected"], case["name"]
        assert got_later == case["expected"], case["name"]
        assert rest_first == rest_later == "", case["name"]


def test_server_semantics() -> None:
    """服务端口径：首句软切阈值 14 / 后续句 56 / 无标点返回累积余量。"""
    for case in _fixture()["server_only"]:
        got, rest = _split_complete_sentences(case["input"], first=case["first"])
        assert got == case["expected_sentences"], case["name"]
        assert rest == case["expected_remainder"], case["name"]
