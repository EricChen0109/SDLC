"""
core/llm_enhancer.py

走訪 AnalysisResult，解決靜態分析做不到的兩件事：
1. 函式/類別有結構，但沒寫 docstring → 讀程式碼內容，請 LLM 生成一段說明
2. 模組層級沒有結構化的程式碼（CodeBlockInfo）→ 請 LLM 理解這段程式碼在做什麼

生成的說明一律放進對應物件的 extra["llm_explanation"]，
不會動到任何既有欄位 —— 這是當初設計 extra 欄位時就預留好的擴充方式。

放在 core/ 而不是 requirements_spec/ 底下，是因為「用 LLM 補充程式碼說明」
是跨功能共用的能力，未來 architecture_diagram、test_doc 等功能應該也用得到。
"""

from core.llm_client import BaseLLMClient, get_llm_client
from core.models import AnalysisResult, ClassInfo, CodeBlockInfo, FunctionInfo

FUNCTION_PROMPT_TEMPLATE = """你是一位資深工程師。請用一到兩句繁體中文，簡潔說明以下函式的用途。
直接給出說明文字本身，不要加任何開頭或結尾的客套話，不要重複函式名稱。

程式碼：
```python
{source_code}
```"""

CODE_BLOCK_PROMPT_TEMPLATE = """你是一位資深工程師。請用一到兩句繁體中文，簡潔說明以下這段程式碼在做什麼事情、用途是什麼。
直接給出說明文字本身，不要加任何開頭或結尾的客套話。

程式碼：
```python
{source_code}
```"""


def enhance_with_llm(analysis_result: AnalysisResult) -> AnalysisResult:
    """
    對分析結果做 LLM 補強。會直接修改傳入的物件（也回傳它方便串接呼叫）。
    """
    client = get_llm_client()

    for module in analysis_result.modules:
        for func in module.functions:
            _enhance_function(func, client)
        for cls in module.classes:
            _enhance_class(cls, client)
        for block in module.code_blocks:
            _enhance_code_block(block, client)

    return analysis_result


def _enhance_function(func: FunctionInfo, client: BaseLLMClient) -> None:
    if func.docstring:
        return  # 已經有人寫好的說明了，不需要 LLM 幫忙
    source_code = func.extra.get("source_code")
    if not source_code:
        return
    try:
        prompt = FUNCTION_PROMPT_TEMPLATE.format(source_code=source_code)
        func.extra["llm_explanation"] = client.complete(prompt).strip()
    except Exception as e:
        print(f"[警告] LLM 補充函式「{func.name}」說明時失敗：{e}")


def _enhance_class(cls: ClassInfo, client: BaseLLMClient) -> None:
    if not cls.docstring:
        source_code = cls.extra.get("source_code")
        if source_code:
            try:
                prompt = FUNCTION_PROMPT_TEMPLATE.format(source_code=source_code)
                cls.extra["llm_explanation"] = client.complete(prompt).strip()
            except Exception as e:
                print(f"[警告] LLM 補充類別「{cls.name}」說明時失敗：{e}")

    for method in cls.methods:
        _enhance_function(method, client)


def _enhance_code_block(block: CodeBlockInfo, client: BaseLLMClient) -> None:
    try:
        prompt = CODE_BLOCK_PROMPT_TEMPLATE.format(source_code=block.source_code)
        block.extra["llm_explanation"] = client.complete(prompt).strip()
    except Exception as e:
        print(f"[警告] LLM 補充程式碼區塊說明時失敗：{e}")
