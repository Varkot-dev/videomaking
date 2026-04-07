"""
GraphTheoryTemplate — generates ManimGL scenes for graph theory visualizations.
Beat types: graph_appear, highlight_node, highlight_edge, highlight_path,
            traverse_bfs, traverse_dfs, annotation, transition.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class GraphTheoryTemplate(TemplateScene):

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "graph_appear":
            return self._graph_appear(beat)
        if t == "highlight_node":
            return self._highlight_node(beat)
        if t == "highlight_edge":
            return self._highlight_edge(beat)
        if t == "highlight_path":
            return self._highlight_path(beat)
        if t == "traverse_bfs":
            return self._traverse_bfs(beat)
        if t == "traverse_dfs":
            return self._traverse_dfs(beat)
        if t == "annotation":
            return self._annotation(beat)
        if t == "transition":
            return self._transition(beat)
        return []

    def _graph_appear(self, beat: dict) -> list[str]:
        nodes = beat.get("nodes", [[0, 0]])
        edges = beat.get("edges", [])
        labels = beat.get("labels", None)
        node_color = normalize_color(beat.get("node_color", "WHITE"))
        edge_color = normalize_color(beat.get("edge_color", "GREY_A"))
        # Normalize coords from [-1,1] to [-5,5]×[-3,3]
        lines = [
            f"_raw_nodes = {nodes}",
            f"_edges_idx = {edges}",
            "_graph_nodes = VGroup()",
            "_node_pts = []",
            "for _nx, _ny in _raw_nodes:",
            "    _pt = np.array([_nx * 5.0, _ny * 3.0, 0])",
            "    _node_pts.append(_pt)",
            f"    _dot = Dot(_pt, radius=0.2, fill_color={node_color})",
            "    _graph_nodes.add(_dot)",
            "_graph_edges = VGroup()",
            "for _ei, _ej in _edges_idx:",
            f"    _line = Line(_node_pts[_ei], _node_pts[_ej], stroke_width=3, color={edge_color})",
            "    _graph_edges.add(_line)",
            "_graph_group = VGroup(_graph_edges, _graph_nodes)",
        ]
        if self.title:
            lines.append("_graph_group.center().shift(DOWN * 0.5)")
        else:
            lines.append("_graph_group.center()")

        if labels:
            lines += [
                f"_labels_list = {labels}",
                "_graph_labels = VGroup()",
                "for _li, (_dot, _ltext) in enumerate(zip(_graph_nodes, _labels_list)):",
                "    _lbl = Text(str(_ltext), font_size=18, color=WHITE)",
                "    _lbl.next_to(_dot, UP, buff=0.08)",
                "    _graph_labels.add(_lbl)",
                "self.play(ShowCreation(_graph_edges), run_time=1.0)",
                "self.play(LaggedStart(*[GrowFromCenter(d) for d in _graph_nodes], lag_ratio=0.1), run_time=1.5)",
                "self.play(LaggedStart(*[FadeIn(l) for l in _graph_labels], lag_ratio=0.05))",
                "self.wait(0.5)",
            ]
        else:
            lines += [
                "self.play(ShowCreation(_graph_edges), run_time=1.0)",
                "self.play(LaggedStart(*[GrowFromCenter(d) for d in _graph_nodes], lag_ratio=0.1), run_time=1.5)",
                "self.wait(0.5)",
            ]
        return lines

    def _highlight_node(self, beat: dict) -> list[str]:
        index = beat.get("index", 0)
        color = normalize_color(beat.get("color", "YELLOW"))
        label = beat.get("label", None)
        duration = beat.get("duration", 1.0)
        lines = [
            f"self.play(_graph_nodes[{index}].animate.set_color({color}), run_time={duration})",
        ]
        if label:
            escaped = label.replace('"', '\\"')
            lines += [
                f'_hn_label = Text("{escaped}", font_size=28, color={color})',
                f"_hn_label.next_to(_graph_nodes[{index}], UP, buff=0.2)",
                "self.play(Write(_hn_label))",
                "self.wait(0.5)",
            ]
        else:
            lines.append("self.wait(0.5)")
        return lines

    def _highlight_edge(self, beat: dict) -> list[str]:
        i = beat.get("i", 0)
        j = beat.get("j", 1)
        color = normalize_color(beat.get("color", "YELLOW"))
        duration = beat.get("duration", 1.0)
        return [
            "# find edge between nodes i and j",
            f"_he_i, _he_j = {i}, {j}",
            "for _he_edge in _graph_edges:",
            f"    _si = np.array(_node_pts[_he_i]); _sj = np.array(_node_pts[_he_j])",
            "    if (np.allclose(_he_edge.get_start()[:2], _si[:2], atol=0.05) and np.allclose(_he_edge.get_end()[:2], _sj[:2], atol=0.05)) or \\",
            "       (np.allclose(_he_edge.get_start()[:2], _sj[:2], atol=0.05) and np.allclose(_he_edge.get_end()[:2], _si[:2], atol=0.05)):",
            f"        self.play(_he_edge.animate.set_color({color}), run_time={duration})",
            "        break",
            "self.wait(0.5)",
        ]

    def _highlight_path(self, beat: dict) -> list[str]:
        path = beat.get("path", [])
        color = normalize_color(beat.get("color", "GREEN"))
        duration = beat.get("duration", 1.5)
        return [
            f"_path_nodes = {path}",
            f"_path_anims = []",
            "for _pi in _path_nodes:",
            f"    _path_anims.append(_graph_nodes[_pi].animate.set_color({color}))",
            f"self.play(*_path_anims, run_time={duration})",
            "self.wait(0.5)",
        ]

    def _traverse_bfs(self, beat: dict) -> list[str]:
        start = beat.get("start", 0)
        color = normalize_color(beat.get("color", "YELLOW"))
        duration = beat.get("duration", 2.0)
        return [
            f"# BFS traversal from node {start}",
            f"_bfs_visited = []",
            f"_bfs_queue = [{start}]",
            "_bfs_adj = [[] for _ in range(len(_graph_nodes))]",
            "for _be_i, _be_j in _edges_idx:",
            "    _bfs_adj[_be_i].append(_be_j)",
            "    _bfs_adj[_be_j].append(_be_i)",
            "while _bfs_queue:",
            "    _bfs_cur = _bfs_queue.pop(0)",
            "    if _bfs_cur in _bfs_visited: continue",
            "    _bfs_visited.append(_bfs_cur)",
            f"    self.play(_graph_nodes[_bfs_cur].animate.set_color({color}), run_time={duration}/max(1,len(_graph_nodes)))",
            "    for _bfs_nb in _bfs_adj[_bfs_cur]:",
            "        if _bfs_nb not in _bfs_visited:",
            "            _bfs_queue.append(_bfs_nb)",
            "self.wait(0.5)",
        ]

    def _traverse_dfs(self, beat: dict) -> list[str]:
        start = beat.get("start", 0)
        color = normalize_color(beat.get("color", "RED"))
        duration = beat.get("duration", 2.0)
        return [
            f"# DFS traversal from node {start}",
            f"_dfs_visited = []",
            f"_dfs_stack = [{start}]",
            "_dfs_adj = [[] for _ in range(len(_graph_nodes))]",
            "for _de_i, _de_j in _edges_idx:",
            "    _dfs_adj[_de_i].append(_de_j)",
            "    _dfs_adj[_de_j].append(_de_i)",
            "while _dfs_stack:",
            "    _dfs_cur = _dfs_stack.pop()",
            "    if _dfs_cur in _dfs_visited: continue",
            "    _dfs_visited.append(_dfs_cur)",
            f"    self.play(_graph_nodes[_dfs_cur].animate.set_color({color}), run_time={duration}/max(1,len(_graph_nodes)))",
            "    for _dfs_nb in reversed(_dfs_adj[_dfs_cur]):",
            "        if _dfs_nb not in _dfs_visited:",
            "            _dfs_stack.append(_dfs_nb)",
            "self.wait(0.5)",
        ]

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        position = beat.get("position", "right")
        escaped = text.replace('"', '\\"')
        pos_map = {
            "right": "RIGHT * 4.5 + DOWN * 0.5",
            "bottom": "DOWN * 3.0",
        }
        pos = pos_map.get(position, "RIGHT * 4.5 + DOWN * 0.5")
        return [
            f'_gt_ann = Text("{escaped}", font_size=28, color=WHITE)',
            f"_gt_ann.move_to({pos})",
            "self.play(Write(_gt_ann))",
            "self.wait(1.0)",
        ]

    def _transition(self, beat: dict) -> list[str]:
        return [
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)",
            "self.wait(0.2)",
        ]
