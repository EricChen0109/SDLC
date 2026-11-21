"""
test_doc/llm_enhancer.py

測試文件功能專屬的 LLM 步驟：替「風險最高的前 N 個對象」產生結構化的測試案例（CaseSpec），
寫進 TargetInfo.test_cases。N 由 test_doc/models.py 的 FOCUS_TARGET_COUNT 決定。

為什麼不重用 core/llm_enhancer.py 的 enhance_with_llm：
那個是「替沒有 docstring 的函式補說明」，對測試文件沒有用，還會白花 token。
每個功能各自帶著自己的 LLM 步驟（見 core/base.py 的 Feature.llm_enhancer）。

設計重點：
- 要求 LLM 輸出 JSON，而不是自由文字，因為 docx 要排成表格、未來的 pytest 輸出要轉成程式碼，
  都需要結構化的資料。
- LLM 的回應可能被截斷（超過 max_tokens）或夾雜多餘文字，所以解析時盡量「救」出能用的案例，
  而不是整個丟掉。
- 單一對象失敗（呼叫錯誤、回應無法解析）只會讓那個對象沒有案例，不會中斷其他對象。
"""

import json
import re
from typing import Any, Dict, List, Optional

from core.llm_client import BaseLLMClient, get_llm_client
from core.models import AnalysisResult
from test_doc.models import TEST_DOC_EXTRA_KEY, CaseSpec, TargetInfo

# 每個對象最多產生幾個測試案例
MAX_CASES_PER_TARGET = 5
VALID_CATEGORIES = ("normal", "boundary", "error")

OUTPUT_FORMAT_INSTRUCTION = """請只輸出一個 JSON 陣列，不要有任何其他文字，也不要用 markdown 的程式碼圍欄包起來。
陣列中每個元素的格式如下：
{
  "title": "一句話的案例名稱（繁體中文）",
  "category": "normal 或 boundary 或 error 三選一",
  "inputs": {"參數名稱": "該參數的值，寫成 Python 運算式的字串"},
  "setup": "如果要測的是類別的方法，這裡寫建立物件的 Python 運算式字串，例如 \\"Calculator(precision=2)\\"；若是一般函式則為 null",
  "expected": "預期回傳值，寫成 Python 運算式的字串；如果無法單靠閱讀程式碼確定，請填 null",
  "expected_exception": "預期會拋出的例外類別名稱；沒有則為 null",
  "description": "一到兩句繁體中文，說明這個案例在驗證什麼"
}"""


def enhance_with_test_cases(
    analysis_result: AnalysisResult, client: Optional[BaseLLMClient] = None
) -> AnalysisResult:
    """
    替風險最高的前 N 個對象產生測試案例，直接修改傳入的物件（也回傳它方便串接）。
    client 參數只是為了方便測試時換成假的 LLM；平常不用傳，會自動用 get_llm_client() 取得。
    """
    data = analysis_result.extra.get(TEST_DOC_EXTRA_KEY)
    if data is None:
        return analysis_result

    client = client or get_llm_client()
    data.extra["llm_applied"] = True  # 讓 generator 知道「LLM 有跑過」，才能區分「沒啟用」與「啟用了但失敗」

    for target in data.focus_targets():
        if not target.source_code:
            continue
        try:
            response = client.complete(_build_prompt(target))
            target.test_cases = _parse_cases(response)
            if not target.test_cases:
                print(f"[警告] LLM 對「{target.qualified_name}」的回應無法解析成測試案例")
        except Exception as e:
            print(f"[警告] 替「{target.qualified_name}」產生測試案例時失敗：{e}")
    return analysis_result


def _build_prompt(target: TargetInfo) -> str:
    kind_label = "方法" if target.owner else "函式"
    lines = [
        "你是一位資深的軟體測試工程師。",
        f"請替下面這個 Python {kind_label}設計最多 {MAX_CASES_PER_TARGET} 個測試案例，"
        "涵蓋正常情況（normal）、邊界條件（boundary）、錯誤處理（error）。",
        "",
        "重要原則：",
        "- 只根據程式碼實際寫出來的行為設計測試，不要臆測程式碼沒有的功能。",
        "- 預期結果如果無法單靠閱讀程式碼確定，expected 一律填 null，不要亂猜。",
        "- 預期例外只能寫程式碼裡確實會拋出的例外。",
        "",
    ]
    if target.owner:
        lines.append(f"這是類別 {target.owner} 的方法。")
        if target.owner_init_signature:
            lines.append(f"該類別的建構子：{target.owner_init_signature}")
    lines.append(f"簽名：{target.signature}")
    lines.append(f"docstring：{target.docstring or '（無）'}")
    if target.raises:
        lines.append(f"已知會拋出的例外：{', '.join(target.raises)}")
    if target.boundary_hints:
        lines.append("建議關注的邊界條件：")
        lines.extend(f"  - {hint}" for hint in target.boundary_hints)
    lines += ["", "程式碼：", "```python", target.source_code, "```", "", OUTPUT_FORMAT_INSTRUCTION]
    return "\n".join(lines)


def _parse_cases(text: str) -> List[CaseSpec]:
    cases = []
    for item in _extract_json_items(text):
        case = _to_case(item)
        if case is not None:
            cases.append(case)
    return cases[:MAX_CASES_PER_TARGET]


def _extract_json_items(text: str) -> List[Any]:
    """
    從 LLM 的回應裡取出 JSON 陣列的元素。
    先嘗試整段當完整的 JSON 解析；失敗（常見原因：回應超過 max_tokens 被截斷在半途）
    就改成一個元素一個元素地解析，能救幾個算幾個。
    """
    # 找「[ 後面緊接著 {」的位置當作 JSON 陣列的起點，
    # 這樣 LLM 在 JSON 前面多說了帶方括號的話（例如「[注意]」），也不會從錯的地方開始解析
    match = re.search(r"\[\s*\{", text)
    if match is None:
        return []
    start = match.start()
    end = text.rfind("]")
    if end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except ValueError:
            pass

    items: List[Any] = []
    decoder = json.JSONDecoder()
    position = start + 1
    while position < len(text):
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] == "]":
            break
        try:
            item, position = decoder.raw_decode(text, position)
        except ValueError:
            break  # 剩下的是被截斷的殘缺內容，放棄
        items.append(item)
    return items


def _to_case(item: Any) -> Optional[CaseSpec]:
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    category = item.get("category")
    inputs = item.get("inputs")
    return CaseSpec(
        title=title.strip(),
        category=category if category in VALID_CATEGORIES else "normal",
        inputs={str(k): _as_expression(v) for k, v in inputs.items()} if isinstance(inputs, dict) else {},
        setup=_as_optional_expression(item.get("setup")),
        expected=_as_optional_expression(item.get("expected")),
        expected_exception=_as_optional_expression(item.get("expected_exception")),
        description=str(item.get("description") or "").strip(),
    )


def _as_expression(value: Any) -> str:
    """LLM 偶爾會把數字、布林直接寫成 JSON 的值（而不是字串），統一轉成 Python 運算式字串"""
    return value if isinstance(value, str) else repr(value)


def _as_optional_expression(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = _as_expression(value).strip()
    return text or None
