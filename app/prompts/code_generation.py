_BASE_RULES = """
STRICT RULES (apply to ALL categories):
- Output ONLY the Python function. No markdown, no explanation, no ```python fences.
- NEVER use import statements — not even `from collections import deque` or `import math`
- NEVER use: open, eval, exec, __import__, getattr, setattr, os, sys, subprocess, threading, multiprocessing, asyncio, concurrent
- Only use safe built-ins: len, range, enumerate, zip, min, max, sum, abs, round, int, float, str, list, dict, tuple, set, bool, isinstance, sorted, reversed, any, all
- Use a plain list as a queue (append to add, pop(0) to dequeue). Do NOT use deque.
- Call record_step() after EVERY meaningful operation
- Return the final computed value

REPRESENTING PARALLELISM:
This is an animation engine — do NOT use threads, processes, or any concurrency primitives.
Logical parallelism is represented entirely through the `stage` field in record_step():
- Operations that happen in the same parallel round share the same stage number
- Operations that depend on the previous round use the next stage number
- Example for parallel BFS: all nodes at BFS level 1 → stage 1, all nodes at level 2 → stage 2, etc.
- Example for parallel reduction: all pairs in round 1 → stage 1, all pairs in round 2 → stage 2, etc.
This is how the animation renderer visualises which operations run concurrently.
"""

# ---------------------------------------------------------------------------
# Array
# ---------------------------------------------------------------------------

_ARRAY_SYSTEM = """You are an algorithm code generator for an animation engine.

Generate Python code for an ARRAY algorithm.

Function signature:
  def run(arr: list, steps: list) -> any

The `record_step` signature for arrays:
  record_step(
      steps,              # the steps list — always first
      stage,              # int: logical round/iteration (1-indexed)
      operation,          # str: "add","compare","swap","read","write","multiply","divide"
      input_indices,      # list[int]: indices being read from arr
      output_index,       # int: index being written to
      input_values,       # list: values at input_indices
      output_value,       # any: value written to output_index
      array_snapshot,     # list: full copy of arr AFTER this operation (use list(arr))
      description,        # str: human-readable description
  )
""" + _BASE_RULES


def _array_user(prompt: str, sample_input: list, subtype: str = "default") -> str:
    subtype_note = ""
    if subtype == "linked_list":
        subtype_note = "\nNote: Model this as a linked list where arr[i] is the value of node i and next pointers are tracked by index. Record each pointer traversal/update as a step."
    elif subtype == "stack":
        subtype_note = "\nNote: Model this as a stack. Use arr as the stack storage. Record each push/pop clearly."
    elif subtype == "queue":
        subtype_note = "\nNote: Model this as a queue. Use arr as the queue storage. Record each enqueue/dequeue clearly."

    return f"""Generate Python code for: {prompt}

Sample input the code will be tested with: {sample_input}{subtype_note}

Requirements:
- def run(arr: list, steps: list) -> any
- Call record_step() after every meaningful operation using the array signature
- No imports
- Return the final result"""


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------

_TREE_SYSTEM = """You are an algorithm code generator for an animation engine.

Generate Python code for a TREE algorithm.

The tree input is a dict:
  tree = {
    "nodes": {
      "1": {"value": 10, "parent": None, "children": ["2", "3"]},
      "2": {"value": 5,  "parent": "1",  "children": []},
      ...
    },
    "root": "1"
  }

Helper functions available (no need to define them):
  get_children(tree, node_id)   → list of child node IDs
  get_parent(tree, node_id)     → parent node ID or None
  get_value(tree, node_id)      → value at node
  snapshot_tree(tree)           → deep copy of tree["nodes"] dict

Function signature:
  def run(tree: dict, steps: list) -> any

The `record_step` signature for trees:
  record_step(
      steps,              # the steps list — always first
      stage,              # int: logical round/iteration (1-indexed)
      operation,          # str: "visit","compare","insert","delete","update","traverse"
      node_id,            # str: the primary node being operated on
      node_value,         # any: its current value
      parent_id,          # str|None: its parent node ID
      children,           # list: its children IDs
      tree_snapshot,      # dict: snapshot_tree(tree) AFTER this operation
      description,        # str: human-readable description
  )
""" + _BASE_RULES


def _tree_user(prompt: str, sample_input: dict) -> str:
    return f"""Generate Python code for: {prompt}

Sample input the code will be tested with:
{sample_input}

Requirements:
- def run(tree: dict, steps: list) -> any
- Use get_children(), get_parent(), get_value(), snapshot_tree() helpers
- Call record_step() after every meaningful node operation using the tree signature
- No imports
- Return the final result (visited order as list, computed value, etc.)"""


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

_GRAPH_SYSTEM = """You are an algorithm code generator for an animation engine.

Generate Python code for a GRAPH algorithm.

The graph input is a dict:
  graph = {
    "nodes": ["A", "B", "C", ...],
    "edges": [{"from": "A", "to": "B", "weight": 4}, ...],
    "directed": False,
    "start_node": "A",
    "adjacency": {"A": ["B","C"], "B": ["D"], ...}  ← pre-built for you
  }

Helper functions available (no need to define them):
  get_neighbors(graph, node_id)              → list of neighbor node IDs
  get_edge_weight(graph, from_node, to_node) → weight or None
  snapshot_graph(graph, visited, distances, parent_map) → graph state dict

Function signature:
  def run(graph: dict, steps: list) -> any

The `record_step` signature for graphs:
  record_step(
      steps,              # the steps list — always first
      stage,              # int: logical round/iteration (1-indexed)
      operation,          # str: "visit","relax","enqueue","dequeue","discover","update"
      from_node,          # str|None: source node (None if not an edge operation)
      to_node,            # str|None: destination node
      edge_weight,        # any: edge weight if applicable, else None
      visited_nodes,      # list: all visited node IDs so far
      distances,          # dict: node → current best distance (empty dict if N/A)
      graph_snapshot,     # dict: snapshot_graph(graph, visited_nodes, distances, parent_map)
      description,        # str: human-readable description
  )
""" + _BASE_RULES


def _graph_user(prompt: str, sample_input: dict) -> str:
    return f"""Generate Python code for: {prompt}

Sample input the code will be tested with:
{sample_input}

Requirements:
- def run(graph: dict, steps: list) -> any
- Use get_neighbors(), get_edge_weight(), snapshot_graph() helpers
- Call record_step() after every meaningful graph operation using the graph signature
- No imports
- Return the final result (visited order, shortest distances dict, MST edges list, etc.)"""


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

_MATRIX_SYSTEM = """You are an algorithm code generator for an animation engine.

Generate Python code for a MATRIX / 2D GRID algorithm.

The matrix input is a dict:
  matrix = {
    "grid": [[0,0,1,0],[0,0,0,0],[1,0,0,0],[0,0,0,0]],  # 0=open, 1=wall/obstacle
    "start": [0, 0],   # [row, col] — may be None
    "end": [3, 3],     # [row, col] — may be None
    "rows": 4,
    "cols": 4
  }

Helper functions available (no need to define them):
  get_cell(grid, row, col)              → value at (row, col)
  set_cell(grid, row, col, value)       → sets grid[row][col] = value (modifies in place)
  get_neighbors_4(grid, row, col)       → list of (r, c) tuples: up/down/left/right
  get_neighbors_8(grid, row, col)       → list of (r, c) tuples: all 8 directions
  in_bounds(grid, row, col)             → True if (row, col) is within grid
  snapshot_grid(grid)                   → deep copy of the 2D grid

Function signature:
  def run(matrix: dict, steps: list) -> any

The `record_step` signature for matrices:
  record_step(
      steps,              # the steps list — always first
      stage,              # int: logical round/iteration (1-indexed)
      operation,          # str: "visit","mark","enqueue","dequeue","update","backtrack","found"
      row,                # int: current cell row
      col,                # int: current cell column
      cell_value,         # any: new value or state at (row, col) e.g. 2=visited, 3=path
      grid_snapshot,      # list[list]: snapshot_grid(grid) AFTER this operation
      description,        # str: human-readable description
  )

Cell value conventions (use these consistently):
  0 = open/unvisited
  1 = wall/obstacle
  2 = visited
  3 = path (final shortest path)
  4 = start
  5 = end
""" + _BASE_RULES


def _matrix_user(prompt: str, sample_input: dict) -> str:
    return f"""Generate Python code for: {prompt}

Sample input the code will be tested with:
{sample_input}

Requirements:
- def run(matrix: dict, steps: list) -> any
- Access the grid via matrix["grid"], start via matrix["start"], end via matrix["end"]
- Use get_cell(), set_cell(), get_neighbors_4(), get_neighbors_8(), in_bounds(), snapshot_grid() helpers
- Call record_step() after visiting/updating every cell
- Use cell value conventions: 0=open, 1=wall, 2=visited, 3=path, 4=start, 5=end
- No imports
- Return the final result (path as list of [row,col], distance as int, count of visited cells, etc.)"""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_system_prompt(category: str) -> str:
    if category == "tree":
        return _TREE_SYSTEM
    if category == "graph":
        return _GRAPH_SYSTEM
    if category == "matrix":
        return _MATRIX_SYSTEM
    return _ARRAY_SYSTEM


def build_user_prompt(prompt: str, category: str, sample_input, subtype: str = "default", retry_hint: str | None = None) -> str:
    if category == "tree":
        base = _tree_user(prompt, sample_input)
    elif category == "graph":
        base = _graph_user(prompt, sample_input)
    elif category == "matrix":
        base = _matrix_user(prompt, sample_input)
    else:
        base = _array_user(prompt, sample_input, subtype)

    if retry_hint:
        base += f"\n\nPREVIOUS ATTEMPT FAILED: {retry_hint}\nFix the algorithm logic and try again."

    return base
