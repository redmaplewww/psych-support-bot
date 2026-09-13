"""面板整卷批量提交（/sessions/{id}/bulk）端到端测试。

背景：评估页改为 shadcn 式逐题分页后，作答状态在客户端本地持有，
整卷一次提交。服务端必须在此处完成全部临床守门：
- 长度/取值范围按量表全量校验（422），半卷不落库
- 会话归属校验（403）/ 未知会话（404）/ 重复提交（409）
- PHQ-9 第 9 题（index 8）> 0 → needs_safety_followup 信号必须点亮
- 确定性评分落库（source=panel），完成会话
"""

import uuid

from fastapi.testclient import TestClient

from psych_support_bot.app import app

client = TestClient(app)


def _start_session(user_id: str, assessment_type: str) -> str:
    res = client.post(
        "/v1/assessments/sessions",
        json={"user_id": user_id, "assessment_type": assessment_type, "consent_acknowledged": True},
    )
    assert res.status_code == 200, res.text
    return res.json()["session_id"]


def _phq9_answers(item9: int = 0) -> list[int]:
    return [1, 1, 1, 1, 1, 1, 1, 1, item9]


def test_bulk_submit_happy_path_scores_and_completes() -> None:
    user = f"u_bulk_{uuid.uuid4().hex[:8]}"
    session_id = _start_session(user, "phq9")
    answers = _phq9_answers()  # 8×1 + 第9题0 = 8 分 → mild

    res = client.post(f"/v1/assessments/sessions/{session_id}/bulk?user_id={user}", json={"answers": answers})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result"]["score"] == 8
    assert body["result"]["severity_band"] == "mild"
    assert body["session"]["status"] == "completed"
    assert body["session"]["answers"] == answers
    # 确定性结果立即返回——完成轮不含 LLM 等待
    assert body["result"]["interpretation"]["needs_safety_followup"] is False

    # 落库可查（source=panel）
    history = client.get(f"/v1/assessments?user_id={user}").json()
    assert any(r["assessment_type"] == "phq9" and r["source"] == "panel" for r in history)


def test_bulk_submit_item9_endorsed_raises_safety_flag() -> None:
    user = f"u_bulk_{uuid.uuid4().hex[:8]}"
    session_id = _start_session(user, "phq9")

    res = client.post(
        f"/v1/assessments/sessions/{session_id}/bulk?user_id={user}", json={"answers": _phq9_answers(item9=2)}
    )
    assert res.status_code == 200
    interp = res.json()["result"]["interpretation"]
    assert interp["needs_safety_followup"] is True
    assert any(f["code"] == "self_harm_signal" for f in interp["safety_flags"])


def test_bulk_submit_rejects_wrong_length_without_polluting_session() -> None:
    user = f"u_bulk_{uuid.uuid4().hex[:8]}"
    session_id = _start_session(user, "phq9")

    res = client.post(f"/v1/assessments/sessions/{session_id}/bulk?user_id={user}", json={"answers": [1, 2, 3]})
    assert res.status_code == 422
    # 半卷不得污染会话：会话仍在进行中且无答案
    view = client.get(f"/v1/assessments/sessions/{session_id}?user_id={user}").json()
    assert view["status"] == "in_progress"
    assert view["answers"] == []


def test_bulk_submit_rejects_out_of_range_value() -> None:
    user = f"u_bulk_{uuid.uuid4().hex[:8]}"
    session_id = _start_session(user, "gad7")

    res = client.post(
        f"/v1/assessments/sessions/{session_id}/bulk?user_id={user}",
        json={"answers": [4] * 7},  # gad7 每题上限 3
    )
    assert res.status_code == 422


def test_bulk_submit_rejects_completed_session() -> None:
    user = f"u_bulk_{uuid.uuid4().hex[:8]}"
    session_id = _start_session(user, "isi")
    res = client.post(f"/v1/assessments/sessions/{session_id}/bulk?user_id={user}", json={"answers": [1] * 7})
    assert res.status_code == 200
    res = client.post(f"/v1/assessments/sessions/{session_id}/bulk?user_id={user}", json={"answers": [1] * 7})
    assert res.status_code == 409


def test_bulk_submit_rejects_foreign_user() -> None:
    owner = f"u_bulk_{uuid.uuid4().hex[:8]}"
    stranger = f"u_bulk_{uuid.uuid4().hex[:8]}"
    session_id = _start_session(owner, "phq9")

    res = client.post(
        f"/v1/assessments/sessions/{session_id}/bulk?user_id={stranger}", json={"answers": _phq9_answers()}
    )
    assert res.status_code == 403


def test_bulk_submit_unknown_session_404() -> None:
    user = f"u_bulk_{uuid.uuid4().hex[:8]}"
    res = client.post(f"/v1/assessments/sessions/{uuid.uuid4()}/bulk?user_id={user}", json={"answers": _phq9_answers()})
    assert res.status_code == 404


def test_bulk_submit_empty_answers_422() -> None:
    user = f"u_bulk_{uuid.uuid4().hex[:8]}"
    session_id = _start_session(user, "phq9")
    res = client.post(f"/v1/assessments/sessions/{session_id}/bulk?user_id={user}", json={"answers": []})
    assert res.status_code == 422
