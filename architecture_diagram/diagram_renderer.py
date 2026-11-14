"""
architecture_diagram/diagram_renderer.py

負責「把關係畫成圖片」，只處理畫圖，不管 docx 排版（那是 generator.py 的工作）。
用 networkx 算出每個節點該擺在第幾層，再用 matplotlib 把方塊跟箭頭畫出來，
輸出 PNG 的位元組，由 generator 直接塞進 docx（不用在硬碟上留暫存圖檔）。

設計備註：
- 圖上的文字刻意只用「檔案名稱/類別名稱」（英文與數字為主），不放中文。
  因為 matplotlib 預設字型不含中文，在沒有裝中文字型的電腦上會變成一格格方塊。
  中文說明一律由 generator 寫在 docx 的文字裡。
- matplotlib / networkx 都是在函式裡面才 import（延遲載入），
  這樣沒裝這兩個套件時，只有架構圖功能會出錯，不會連累其他功能。
"""

import io
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# 圖片尺寸上限（英吋），避免專案很大時產生一張大到無法處理的圖
MAX_FIGURE_WIDTH = 26.0
MAX_FIGURE_HEIGHT = 32.0

LABEL_FONT_SIZE = 8
BOX_PAD = 0.35  # 方塊內距，單位是「字級的倍數」
CYCLE_EDGE_COLOR = "#c0392b"
NORMAL_EDGE_COLOR = "#555555"

Point = Tuple[float, float]
# render_xxx 的回傳值：(PNG 位元組, 圖片寬高比)。寬高比讓 generator 能算出放進 docx 的大小
RenderedImage = Tuple[bytes, float]


def render_dependency_diagram(
    modules: List[str], edges: List[Tuple[str, str]]
) -> Optional[RenderedImage]:
    """
    畫檔案依賴圖。edges 裡的 (A, B) 代表「A 依賴 B」，箭頭從 A 指向 B。
    沒有任何依賴關係時回傳 None。只畫「有跟別人連線」的檔案，孤立的檔案只會製造雜訊。
    """
    if not edges:
        return None
    nodes = sorted({node for edge in edges for node in edge})
    labels = _make_file_labels(nodes)
    groups = {node: _top_folder(node) for node in nodes}
    # 版面上「依賴別人的」放上面、「被依賴的」放下面；箭頭方向就是依賴方向
    return _draw_layered_graph(labels, groups, edges, reverse_arrows=False)


def render_inheritance_diagram(
    class_labels: Dict[str, str],
    class_files: Dict[str, str],
    inheritances: List[Tuple[str, str]],
) -> Optional[RenderedImage]:
    """
    畫類別繼承圖。inheritances 裡的 (子, 父) 代表「子繼承自父」。
    照 UML 的習慣：父類別畫在上面，箭頭從子類別指向父類別。
    """
    if not inheritances:
        return None
    groups = {key: _top_folder(path) for key, path in class_files.items()}
    layout_edges = [(parent, child) for child, parent in inheritances]  # 版面：父在上、子在下
    return _draw_layered_graph(class_labels, groups, layout_edges, reverse_arrows=True)


# =====================================================================
# 內部實作
# =====================================================================


def _top_folder(path: str) -> str:
    """'core/models.py' -> 'core'；放在根目錄的檔案 -> '(root)'。用來決定方塊的顏色分組"""
    return path.split("/")[0] if "/" in path else "(root)"


def _make_file_labels(nodes: List[str]) -> Dict[str, str]:
    """圖上顯示檔名就好（'analyzer.py'）；不同資料夾有同名檔案時，才補上資料夾（'a/' 換行 'analyzer.py'）"""
    name_counts: Dict[str, int] = defaultdict(int)
    for node in nodes:
        name_counts[node.split("/")[-1]] += 1

    labels = {}
    for node in nodes:
        base = node.split("/")[-1]
        if name_counts[base] > 1 and "/" in node:
            labels[node] = "/\n".join(node.split("/")[-2:])
        else:
            labels[node] = base
    return labels


def _assign_layers(graph, nx) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    決定每個節點在第幾層。
    先把「互相循環依賴的一群節點」縮成一個點（condensation），讓圖變成沒有環的 DAG，
    才能做拓撲分層：第 0 層是沒有人指向它的節點，往下每層都是「被上一層依賴」的節點。
    回傳 (節點 -> 層數, 節點 -> 所屬循環群編號)。
    """
    condensed = nx.condensation(graph)
    layers: Dict[str, int] = {}
    for layer_index, generation in enumerate(nx.topological_generations(condensed)):
        for component in generation:
            for member in condensed.nodes[component]["members"]:
                layers[member] = layer_index
    return layers, condensed.graph["mapping"]


def _layout(graph, layers: Dict[str, int]) -> Dict[str, Point]:
    """
    算出每個節點的座標：第 N 層的 y 就是 -N，同一層的節點在 x 方向平均排開、置中。
    同一層內的先後順序，用「重心法」排：節點盡量靠近它上一層的來源節點，
    這樣箭頭比較不會亂交叉。
    """
    by_layer: Dict[int, List[str]] = defaultdict(list)
    for node, layer in layers.items():
        by_layer[layer].append(node)

    positions: Dict[str, Point] = {}
    for layer in sorted(by_layer):
        nodes = by_layer[layer]

        def barycenter(node: str) -> float:
            xs = [positions[p][0] for p in graph.predecessors(node) if p in positions]
            return sum(xs) / len(xs) if xs else 0.0

        nodes.sort(key=lambda n: (barycenter(n) if layer > 0 else 0.0, n))
        for i, node in enumerate(nodes):
            positions[node] = ((i - (len(nodes) - 1) / 2), -float(layer))
    return positions


def _edge_point(center: Point, other: Point, half_size: Point) -> Point:
    """
    從方塊中心朝著 other 方向畫一條線，回傳這條線穿出方塊邊框的那個點。
    箭頭從這個點出發/到達，才不會被方塊蓋住或穿過方塊。
    全部在「資料座標」裡計算，不依賴像素，所以輸出時 dpi 怎麼變都不會錯位。
    """
    dx, dy = other[0] - center[0], other[1] - center[1]
    if dx == 0 and dy == 0:
        return center
    scale_x = half_size[0] / abs(dx) if dx else float("inf")
    scale_y = half_size[1] / abs(dy) if dy else float("inf")
    t = min(scale_x, scale_y)
    return (center[0] + dx * t, center[1] + dy * t)


def _draw_layered_graph(
    labels: Dict[str, str],
    groups: Dict[str, str],
    layout_edges: List[Tuple[str, str]],
    reverse_arrows: bool,
) -> RenderedImage:
    """
    layout_edges 的 (A, B) 代表版面上「A 在 B 的上面」。
    reverse_arrows=False：箭頭從 A 畫到 B（依賴圖）；True：箭頭從 B 畫到 A（繼承圖）。
    """
    import matplotlib

    matplotlib.use("Agg")  # 不開視窗、直接輸出圖片，在沒有螢幕的環境（例如伺服器）也能跑
    import matplotlib.pyplot as plt
    import networkx as nx
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from PIL import Image

    graph = nx.DiGraph()
    graph.add_nodes_from(labels)
    graph.add_edges_from(layout_edges)

    layers, cycle_group = _assign_layers(graph, nx)
    positions = _layout(graph, layers)

    layer_count = max(layers.values()) + 1
    widest_layer = max(
        sum(1 for l in layers.values() if l == layer) for layer in range(layer_count)
    )
    figure_width = min(MAX_FIGURE_WIDTH, max(7.0, widest_layer * 1.9))
    figure_height = min(MAX_FIGURE_HEIGHT, max(3.5, layer_count * 1.25 + 0.8))

    fig, ax = plt.subplots(figsize=(figure_width, figure_height))
    ax.axis("off")
    ax.set_xlim(-widest_layer / 2 - 0.7, widest_layer / 2 + 0.7)
    ax.set_ylim(-(layer_count - 1) - 0.7, 0.7)

    # 同一個資料夾的方塊用同一個顏色
    group_names = sorted(set(groups.values()))
    palette = plt.get_cmap("tab20")
    group_color = {name: palette(i % 20) for i, name in enumerate(group_names)}

    boxes = {}
    for node, (x, y) in positions.items():
        r, g, b, _a = group_color[groups[node]]
        boxes[node] = ax.text(
            x, y, labels[node],
            ha="center", va="center", fontsize=LABEL_FONT_SIZE, zorder=3,
            bbox=dict(
                boxstyle=f"round,pad={BOX_PAD}", fc=(r, g, b, 0.45), ec="#444444", lw=0.8
            ),
        )

    # 先實際繪製一次，才量得出每個方塊佔多大；再把大小換算回「資料座標」，
    # 後面算箭頭與方塊邊框的交點時用得到
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    to_data = ax.transData.inverted()
    pad_px = BOX_PAD * LABEL_FONT_SIZE / 72 * fig.dpi
    half_sizes: Dict[str, Point] = {}
    for node, text in boxes.items():
        bb = text.get_window_extent(renderer)
        (x0, y0), (x1, y1) = to_data.transform(
            [(bb.x0 - pad_px, bb.y0 - pad_px), (bb.x1 + pad_px, bb.y1 + pad_px)]
        )
        half_sizes[node] = ((x1 - x0) / 2, (y1 - y0) / 2)

    has_cycle_edge = False
    for upper, lower in layout_edges:
        start, end = (lower, upper) if reverse_arrows else (upper, lower)
        in_cycle = cycle_group[upper] == cycle_group[lower]
        has_cycle_edge = has_cycle_edge or in_cycle
        ax.annotate(
            "",
            xy=_edge_point(positions[end], positions[start], half_sizes[end]),
            xytext=_edge_point(positions[start], positions[end], half_sizes[start]),
            zorder=2,
            arrowprops=dict(
                arrowstyle="-|>", lw=1.0, shrinkA=0, shrinkB=0,
                color=CYCLE_EDGE_COLOR if in_cycle else NORMAL_EDGE_COLOR,
                # 輕微彎曲：A 指向 B、B 又指回 A（循環依賴）時，兩條箭頭才不會疊在一起
                connectionstyle="arc3,rad=0.08",
            ),
        )

    legend_items = [
        Patch(facecolor=group_color[name][:3] + (0.45,), edgecolor="#444444", label=name)
        for name in group_names
    ]
    if has_cycle_edge:
        legend_items.append(
            Line2D([0], [0], color=CYCLE_EDGE_COLOR, lw=1.5, label="circular dependency")
        )
    ax.legend(
        handles=legend_items, title="Folder", loc="upper left",
        bbox_to_anchor=(1.0, 1.0), fontsize=8, title_fontsize=9, frameon=False,
    )

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    png_bytes = buffer.getvalue()
    width, height = Image.open(io.BytesIO(png_bytes)).size
    return png_bytes, width / height
