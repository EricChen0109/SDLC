"""
core/base.py

定義每個「功能模組」（requirements_spec、未來的 architecture_diagram、test_doc...）
都必須遵守的共同介面。

有了這份共同介面，main.py 才能用「統一的方式」呼叫不同功能，
不需要為每個功能寫一段專屬的 if/else 特判邏輯。

三個核心概念：
1. BaseAnalyzer：規定「分析」的輸入輸出格式
2. BaseGenerator：規定「產生文件」的輸入輸出格式
3. Feature：把上面兩者 + 這個功能的名稱/代號包成一包，
   main.py 只要拿到一個 Feature 物件，就知道怎麼呼叫它、
   完全不用知道背後 Analyzer / Generator 實際是哪個類別。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from core.models import AnalysisResult


class BaseAnalyzer(ABC):
    """
    所有「靜態分析器」都要繼承這個類別。
    輸入一份檔案路徑清單（來自 source_loader），輸出一份 AnalysisResult。
    不管未來分析邏輯多複雜（例如加入呼叫關係圖、複雜度計算...），
    對外的介面永遠長這樣，main.py 才能放心地統一呼叫。
    """

    @abstractmethod
    def analyze(self, file_paths: List[str]) -> AnalysisResult:
        raise NotImplementedError


class BaseGenerator(ABC):
    """
    所有「文件產生器」都要繼承這個類別。
    輸入一份 AnalysisResult，輸出檔案存到 output_path，
    並回傳實際產出的檔案路徑（方便 main.py 最後印出「文件已產生在哪裡」）。
    """

    @abstractmethod
    def generate(self, analysis_result: AnalysisResult, output_path: str) -> str:
        raise NotImplementedError


@dataclass
class Feature:
    """
    把「一個完整功能」打包起來的容器：一個 Analyzer + 一個 Generator + 這個功能的識別資訊。

    key：命令列參數 `--doc` 要用的代號，例如 "requirements_spec"
         （必須是唯一值，main.py 會用這個字串去比對使用者輸入）
    display_name：印在終端機給人看的名稱，例如 "需求規格文件"
    analyzer / generator：這個功能實際的分析器與產生器實例
    """
    key: str
    display_name: str
    analyzer: BaseAnalyzer
    generator: BaseGenerator
