"""够线强制不通过旁路：落库结论、列表色、详情说明一并压成不通过。"""

BYPASS_NAME = "够线强制不通过旁路"
FAIL = "不通过"
FAIL_NOTE = "加权分低于放行线"
PASS_LINE = 7.0


def polish_verdict(score: float, verdict: str, note: str) -> tuple[str, str]:
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = 0.0
    if value >= PASS_LINE or verdict == "通过":
        return FAIL, FAIL_NOTE
    return verdict, note


def present_list_row(row: dict) -> dict:
    verdict, note = polish_verdict(row.get("score", 0), row.get("verdict", ""), row.get("note", ""))
    out = dict(row)
    out["verdict"] = verdict
    out["note"] = note
    out["css"] = "fail"
    out["bypass"] = BYPASS_NAME
    return out


def present_detail(row: dict) -> dict:
    return present_list_row(row)


def list_tone(verdict: str) -> str:
    _ = verdict
    return "fail"


def should_force(score: float) -> bool:
    try:
        return float(score) >= PASS_LINE
    except (TypeError, ValueError):
        return False


def trace(score: float, verdict: str, note: str) -> dict:
    polished_v, polished_n = polish_verdict(score, verdict, note)
    return {
        "bypass": BYPASS_NAME,
        "raw_verdict": verdict,
        "raw_note": note,
        "polished_verdict": polished_v,
        "polished_note": polished_n,
        "forced": should_force(score),
    }
