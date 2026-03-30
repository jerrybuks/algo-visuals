import ast
import multiprocessing
import time
from dataclasses import dataclass, field
from typing import Any

from app.utils.exceptions import SandboxSecurityError, SandboxTimeoutError

# ---------------------------------------------------------------------------
# Restricted builtins
# ---------------------------------------------------------------------------

SAFE_BUILTINS: dict[str, Any] = {
    "len": len, "range": range, "enumerate": enumerate,
    "zip": zip, "min": min, "max": max, "sum": sum,
    "abs": abs, "round": round, "int": int, "float": float,
    "str": str, "list": list, "dict": dict, "tuple": tuple,
    "set": set, "bool": bool, "isinstance": isinstance, "type": type,
    "print": print, "sorted": sorted, "reversed": reversed,
    "map": map, "filter": filter, "any": any, "all": all,
}

FORBIDDEN_CALL_NAMES = {
    "open", "eval", "exec", "compile", "__import__",
    "getattr", "setattr", "delattr", "vars", "dir",
    "globals", "locals", "breakpoint",
}

FORBIDDEN_ATTR_PREFIXES = {"os", "sys", "subprocess", "socket", "pathlib", "shutil", "importlib"}


@dataclass
class SandboxResult:
    success: bool
    output: Any = None
    steps: list[dict] = field(default_factory=list)
    execution_time_ms: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# AST security scan
# ---------------------------------------------------------------------------

def pre_scan(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxSecurityError(f"Syntax error: {e}") from e

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise SandboxSecurityError("Import statements are not allowed in generated code")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                raise SandboxSecurityError(f"Forbidden call: {node.func.id}()")
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in FORBIDDEN_ATTR_PREFIXES:
                    raise SandboxSecurityError(f"Forbidden module access: {node.func.value.id}")
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in FORBIDDEN_ATTR_PREFIXES:
                raise SandboxSecurityError(f"Forbidden module access: {node.value.id}")


# ---------------------------------------------------------------------------
# Category-specific helpers injected into exec scope
# ---------------------------------------------------------------------------

def _make_record_step(category: str):
    if category == "matrix":
        def record_step(steps, stage, operation, row, col, cell_value, grid_snapshot, description):
            steps.append({
                "stage": int(stage), "operation": str(operation), "description": str(description),
                "row": int(row), "col": int(col),
                "cell_value": cell_value,
                "grid_snapshot": grid_snapshot,
            })
    elif category == "tree":
        def record_step(steps, stage, operation, node_id, node_value, parent_id, children, tree_snapshot, description):
            steps.append({
                "stage": int(stage), "operation": str(operation), "description": str(description),
                "node_id": str(node_id) if node_id is not None else None,
                "node_value": node_value,
                "parent_id": str(parent_id) if parent_id is not None else None,
                "children": list(children) if children else [],
                "tree_snapshot": tree_snapshot,
            })
    elif category == "graph":
        def record_step(steps, stage, operation, from_node, to_node, edge_weight, visited_nodes, distances, graph_snapshot, description):
            steps.append({
                "stage": int(stage), "operation": str(operation), "description": str(description),
                "from_node": str(from_node) if from_node is not None else None,
                "to_node": str(to_node) if to_node is not None else None,
                "edge_weight": edge_weight,
                "visited_nodes": list(visited_nodes) if visited_nodes else [],
                "distances": dict(distances) if distances else {},
                "graph_snapshot": graph_snapshot,
            })
    else:
        def record_step(steps, stage, operation, input_indices, output_index, input_values, output_value, array_snapshot, description):
            steps.append({
                "stage": int(stage), "operation": str(operation), "description": str(description),
                "input_indices": list(input_indices) if input_indices else [],
                "output_index": output_index,
                "input_values": list(input_values) if input_values else [],
                "output_value": output_value,
                "array_snapshot": list(array_snapshot) if array_snapshot else [],
            })
    return record_step


def _make_tree_helpers():
    def get_children(tree, node_id):
        return tree["nodes"].get(str(node_id), {}).get("children", [])

    def get_parent(tree, node_id):
        return tree["nodes"].get(str(node_id), {}).get("parent")

    def get_value(tree, node_id):
        return tree["nodes"].get(str(node_id), {}).get("value")

    def snapshot_tree(tree):
        return {k: dict(v) for k, v in tree["nodes"].items()}

    return {"get_children": get_children, "get_parent": get_parent,
            "get_value": get_value, "snapshot_tree": snapshot_tree}


def _make_graph_helpers():
    def get_neighbors(graph, node_id):
        return graph.get("adjacency", {}).get(str(node_id), [])

    def get_edge_weight(graph, from_node, to_node):
        for edge in graph.get("edges", []):
            if str(edge["from"]) == str(from_node) and str(edge["to"]) == str(to_node):
                return edge.get("weight", 1)
            if not graph.get("directed", False):
                if str(edge["to"]) == str(from_node) and str(edge["from"]) == str(to_node):
                    return edge.get("weight", 1)
        return None

    def snapshot_graph(graph, visited=None, distances=None, parent_map=None):
        return {
            "nodes": list(graph.get("nodes", [])),
            "edges": list(graph.get("edges", [])),
            "visited": list(visited or []),
            "distances": dict(distances or {}),
            "parent_map": dict(parent_map or {}),
        }

    return {"get_neighbors": get_neighbors, "get_edge_weight": get_edge_weight,
            "snapshot_graph": snapshot_graph}


def _make_matrix_helpers():
    def get_cell(grid, row, col):
        return grid[row][col]

    def set_cell(grid, row, col, value):
        grid[row][col] = value

    def in_bounds(grid, row, col):
        return 0 <= row < len(grid) and 0 <= col < (len(grid[0]) if grid else 0)

    def get_neighbors_4(grid, row, col):
        result = []
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            r, c = row + dr, col + dc
            if in_bounds(grid, r, c):
                result.append((r, c))
        return result

    def get_neighbors_8(grid, row, col):
        result = []
        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                if dr == 0 and dc == 0:
                    continue
                r, c = row + dr, col + dc
                if in_bounds(grid, r, c):
                    result.append((r, c))
        return result

    def snapshot_grid(grid):
        return [list(row) for row in grid]

    return {
        "get_cell": get_cell, "set_cell": set_cell, "in_bounds": in_bounds,
        "get_neighbors_4": get_neighbors_4, "get_neighbors_8": get_neighbors_8,
        "snapshot_grid": snapshot_grid,
    }


def _build_graph_adjacency(input_data: dict) -> dict:
    """Pre-compute adjacency list and merge into graph dict."""
    adjacency: dict[str, list] = {str(n): [] for n in input_data.get("nodes", [])}
    directed = input_data.get("directed", False)
    for edge in input_data.get("edges", []):
        src, dst = str(edge["from"]), str(edge["to"])
        adjacency.setdefault(src, []).append(dst)
        if not directed:
            adjacency.setdefault(dst, []).append(src)
    return {**input_data, "adjacency": adjacency}


# ---------------------------------------------------------------------------
# Isolated worker process
# ---------------------------------------------------------------------------

def _worker(code: str, input_data: Any, category: str, result_queue: multiprocessing.Queue) -> None:
    steps: list[dict] = []

    exec_globals: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "record_step": _make_record_step(category),
    }

    if category == "tree":
        exec_globals.update(_make_tree_helpers())
    elif category == "graph":
        exec_globals.update(_make_graph_helpers())
    elif category == "matrix":
        exec_globals.update(_make_matrix_helpers())

    try:
        exec(code, exec_globals)  # noqa: S102
        run_fn = exec_globals.get("run")
        if run_fn is None:
            result_queue.put({"success": False, "error": "Generated code does not define a 'run' function"})
            return

        output = run_fn(input_data, steps)
        result_queue.put({"success": True, "output": output, "steps": steps, "error": None})
    except Exception as e:  # noqa: BLE001
        result_queue.put({"success": False, "steps": steps, "error": str(e)})


# ---------------------------------------------------------------------------
# Public execute interface
# ---------------------------------------------------------------------------

def execute(code: str, input_data: Any, category: str = "array", timeout: int = 10) -> SandboxResult:
    # Pre-process graph input to include adjacency dict
    if category == "graph" and isinstance(input_data, dict):
        input_data = _build_graph_adjacency(input_data)

    result_queue: multiprocessing.Queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_worker, args=(code, input_data, category, result_queue))

    start = time.monotonic()
    process.start()
    process.join(timeout=timeout)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
        raise SandboxTimeoutError(f"Code execution exceeded {timeout}s timeout")

    if result_queue.empty():
        return SandboxResult(success=False, execution_time_ms=elapsed_ms, error="Process exited without result")

    result = result_queue.get_nowait()
    return SandboxResult(
        success=result.get("success", False),
        output=result.get("output"),
        steps=result.get("steps", []),
        execution_time_ms=elapsed_ms,
        error=result.get("error"),
    )
