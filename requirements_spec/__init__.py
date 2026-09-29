"""
requirements_spec/__init__.py

這個功能模組對外只暴露一個東西：get_feature()。
main.py 不需要知道 PythonAstAnalyzer、RequirementsSpecGenerator
這些內部類別的存在，只要呼叫 get_feature() 拿到組裝好的 Feature 物件即可。

未來新增其他功能資料夾（architecture_diagram/、test_doc/...）時，
照這個檔案的模式：各自寫一個 get_feature()，就能用同樣的方式被 main.py 接上。
"""

from core.base import Feature
from requirements_spec.analyzer import PythonAstAnalyzer
from requirements_spec.generator import RequirementsSpecGenerator


def get_feature() -> Feature:
    return Feature(
        key="requirements_spec",
        display_name="需求規格文件（技術規格）",
        analyzer=PythonAstAnalyzer(),
        generator=RequirementsSpecGenerator(),
    )
