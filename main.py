"""
main.py

整個程式的進入點。只負責「調度」，不寫任何分析或產生文件的實際邏輯：
1. 解析命令列參數（分析來源、要跑哪個功能）
2. 把來源路徑交給 source_loader，統一整理成檔案清單
3. 依序呼叫每個功能（Feature）的 analyzer 跟 generator
4. 印出結果

用法：
    python main.py --source ./my_project
        → 掃過 REGISTERED_FEATURES 清單裡的每一個功能，全部都跑一次

    python main.py --source ./my_project --doc requirements_spec
        → 只跑 key 是 "requirements_spec" 的那個功能
"""

import argparse
import os
from typing import List

from core.base import Feature
from core.source_loader import get_source_loader

# ============================================================
# 功能註冊清單（手動註冊）
#
# 新增功能時，要做兩件事：
#   1. 在上面 import 區塊，仿照下面這行的樣子，import 新功能資料夾的 get_feature：
#        from architecture_diagram import get_feature as get_architecture_diagram_feature
#   2. 在下面的 REGISTERED_FEATURES 清單裡，加一行呼叫它：
#        get_architecture_diagram_feature(),
#
# 除了這兩處，不需要改動 main.py 其他任何地方。
# ============================================================
from requirements_spec import get_feature as get_requirements_spec_feature

REGISTERED_FEATURES: List[Feature] = [
    get_requirements_spec_feature(),
    # 未來新增功能，依照上面說明，在這裡加一行 ↓↓↓
    # get_architecture_diagram_feature(),
    # get_test_doc_feature(),
]


def build_feature_lookup() -> dict:
    """把註冊清單轉成 {key: Feature} 方便用 --doc 參數查找"""
    return {feature.key: feature for feature in REGISTERED_FEATURES}


def parse_args() -> argparse.Namespace:
    feature_keys = [f.key for f in REGISTERED_FEATURES]

    parser = argparse.ArgumentParser(
        description="分析程式碼並自動產生 SDLC 相關文件"
    )
    parser.add_argument(
        "--source",
        required=True,
        help="要分析的來源路徑，可以是單一檔案或資料夾",
    )
    parser.add_argument(
        "--doc",
        choices=feature_keys,
        default=None,
        help="只產生指定的文件類型；不指定則會產生所有已註冊的文件類型。"
        f"目前可選：{feature_keys}",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="輸出文件存放的資料夾，預設為 ./output",
    )
    return parser.parse_args()


def run_feature(feature: Feature, file_paths: List[str], source: str, output_dir: str) -> None:
    print(f"[執行中] {feature.display_name} ...")

    analysis_result = feature.analyzer.analyze(file_paths)
    analysis_result.source_path = source  # analyzer 只知道檔案清單，來源路徑由這裡補上

    output_path = os.path.join(output_dir, f"{feature.key}.docx")
    actual_path = feature.generator.generate(analysis_result, output_path)

    print(f"[完成] {feature.display_name} → {actual_path}")


def main() -> None:
    args = parse_args()
    feature_lookup = build_feature_lookup()

    os.makedirs(args.output_dir, exist_ok=True)

    # 檔案清單只需要載入一次，多個功能可以共用同一份分析來源
    file_paths = get_source_loader(args.source).load()
    if not file_paths:
        print(f"[警告] 在 {args.source} 底下找不到任何支援的原始碼檔案")
        return

    if args.doc:
        features_to_run = [feature_lookup[args.doc]]
    else:
        features_to_run = REGISTERED_FEATURES

    for feature in features_to_run:
        run_feature(feature, file_paths, args.source, args.output_dir)


if __name__ == "__main__":
    main()
