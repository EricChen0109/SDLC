"""
architecture_diagram/__init__.py

這個功能模組對外只暴露一個東西：get_feature()（跟 requirements_spec 是同一個模式）。

這個功能刻意不設定 use_llm（預設 False）：架構圖的原料都是確定性的結構關係，
不需要也不應該花 LLM 的 token。
"""

from architecture_diagram.analyzer import ArchitectureAnalyzer
from architecture_diagram.generator import ArchitectureDiagramGenerator
from core.base import Feature


def get_feature() -> Feature:
    return Feature(
        key="architecture_diagram",
        display_name="系統架構圖",
        analyzer=ArchitectureAnalyzer(),
        generator=ArchitectureDiagramGenerator(),
    )
