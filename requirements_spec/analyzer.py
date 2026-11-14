"""
requirements_spec/analyzer.py

Python 靜態分析器已經搬到 core/python_analyzer.py，讓各功能共用。
這個檔案只是「轉接」，讓 requirements_spec/__init__.py 原本的 import 不用修改。

以後如果需求規格文件需要「這個功能專屬」的分析邏輯（例如多抓某些只有它才要的資訊），
可以繼承 PythonAstAnalyzer 寫在這裡，而不是去改共用的版本。
"""

from core.python_analyzer import PythonAstAnalyzer

__all__ = ["PythonAstAnalyzer"]