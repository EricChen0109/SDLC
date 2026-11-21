"""
test_doc/__init__.py

這個功能模組對外只暴露一個東西：get_feature()（跟其他功能是同一個模式）。

未來要加「pytest 程式碼輸出」時：
  1. 新增 test_doc/pytest_generator.py，讀同一份 TargetInfo.test_cases 產生 .py 檔
  2. 讓這個功能可以同時產生兩種輸出（目前 Feature 只有一個 generator，到時候再擴充）
LLM 的部分不用動：測試案例本來就是結構化資料。
"""

from core.base import Feature
from test_doc.analyzer import TargetAnalyzer
from test_doc.generator import PlanDocxGenerator
from test_doc.llm_enhancer import enhance_with_test_cases


def get_feature() -> Feature:
    return Feature(
        key="test_doc",
        display_name="測試文件",
        analyzer=TargetAnalyzer(),
        generator=PlanDocxGenerator(),
        llm_enhancer=enhance_with_test_cases,
    )
