from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------

class Complexity(BaseModel):
    # Serial algorithms
    time: str | None = None
    space: str | None = None
    # Parallel algorithms
    work: str | None = None
    span: str | None = None


class AlgorithmInfo(BaseModel):
    name: str
    type: str
    execution_model: Literal["serial", "parallel"]
    category: str   # "array" | "tree" | "graph" | "matrix"
    subtype: str    # "default" | "linked_list" | "stack" | "queue" | "none"
    complexity: Complexity
    input_size: int
    description: str
    steps: list[str] = []  # ordered high-level implementation steps


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    message: str | None = None


class ValidationResult(BaseModel):
    passed: bool
    checks: list[ValidationCheck]


class ResponseMeta(BaseModel):
    ai_model: str
    generation_time_ms: int
    execution_time_ms: int


# ---------------------------------------------------------------------------
# Step Trace
# ---------------------------------------------------------------------------

class TraceStep(BaseModel):
    step_id: int
    stage: int
    operation: str
    description: str
    parallel_group: int = 0

    # Array-specific
    array_snapshot: list[Any] | None = None
    input_indices: list[int] | None = None
    output_index: int | None = None
    input_values: list[Any] | None = None
    output_value: Any | None = None

    # Tree-specific
    node_id: str | None = None
    node_value: Any | None = None
    parent_id: str | None = None
    children: list[str] | None = None
    tree_snapshot: dict | None = None

    # Graph-specific
    from_node: str | None = None
    to_node: str | None = None
    edge_weight: Any | None = None
    visited_nodes: list[str] | None = None
    distances: dict | None = None
    graph_snapshot: dict | None = None

    # Matrix-specific
    row: int | None = None
    col: int | None = None
    cell_value: Any | None = None
    grid_snapshot: list | None = None   # list[list]


class StepTrace(BaseModel):
    algorithm_name: str
    algorithm_category: str  # "array" | "tree" | "graph" | "matrix"
    algorithm_subtype: str   # "default" | "linked_list" | "stack" | "queue" | "none"
    total_steps: int
    stages: int
    input: Any
    final_output: Any
    steps: list[TraceStep]


# ---------------------------------------------------------------------------
# Scene Timeline
# ---------------------------------------------------------------------------

class Connection(BaseModel):
    from_index: int = Field(..., alias="from")
    to_index: int = Field(..., alias="to")
    label: str = ""

    model_config = {"populate_by_name": True}


class EdgeHighlight(BaseModel):
    from_node: str
    to_node: str
    label: str = ""


class SceneFrame(BaseModel):
    frame_id: int
    type: Literal["init", "operation", "result", "summary"]
    step_ref: int | None = None
    narration_index: int
    description: str

    # Array
    array_state: list[Any] | None = None
    highlight_indices: list[int] = []
    active_connections: list[Connection] = []
    value_labels: dict[str, str] = {}

    # Tree / Graph
    highlighted_nodes: list[str] = []
    highlighted_edges: list[EdgeHighlight] = []
    node_states: dict | None = None   # node_id → {value, visited, distance, parent, ...}
    graph_state: dict | None = None   # full graph snapshot for this frame

    # Matrix
    grid_state: list | None = None         # list[list] — full 2D grid snapshot
    highlighted_cells: list | None = None  # [[row,col], ...] — currently active cells
    path_cells: list | None = None         # [[row,col], ...] — final path cells


class Scene(BaseModel):
    scene_id: int
    label: str
    frames: list[SceneFrame]


class SceneTimeline(BaseModel):
    algorithm_name: str
    algorithm_category: str  # "array" | "tree" | "graph" | "matrix"
    algorithm_subtype: str   # "default" | "linked_list" | "stack" | "queue" | "none"
    total_frames: int
    duration_hint_ms: int
    input: Any
    scenes: list[Scene]


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------

class GenerateResponse(BaseModel):
    request_id: str
    status: Literal["success", "partial", "failed"]
    confidence_score: float
    algorithm: AlgorithmInfo | None = None
    code: str | None = None
    narration: list[str] = []
    step_indices: list[int] = []  # one per stage sentence: index into algorithm.steps
    trace: StepTrace | None = None
    scene_timeline: SceneTimeline | None = None
    validation: ValidationResult | None = None
    errors: list[str] = []
    meta: ResponseMeta
