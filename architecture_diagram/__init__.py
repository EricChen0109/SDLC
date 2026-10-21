
"""
architecture_diagram/__init__.py

對外提供 get_feature()，沿用現有的 Feature 註冊機制。
"""

from core.base import Feature
from architecture_diagram.analyzer import ArchitectureAnalyzer
from architecture_diagram.generator import ArchitectureGenerator


def get_feature() -> Feature:
    return Feature(
        key="architecture_diagram",
        display_name="系統架構圖",
        analyzer=ArchitectureAnalyzer(),
        generator=ArchitectureGenerator(),
    )
