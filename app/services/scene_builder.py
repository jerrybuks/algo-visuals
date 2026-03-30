from typing import Any
from app.schemas.response import (
    Connection, EdgeHighlight, Scene, SceneFrame, SceneTimeline, StepTrace, TraceStep,
)

FRAME_DURATION_MS = 500

# Two concept-explanation clips play before the walkthrough begins (indices 0 and 1).
# The intro/bridge clip is index 2. Stage clips start at index 3.
N_CONCEPT_CLIPS = 2
_INTRO_IDX = N_CONCEPT_CLIPS          # 2
_STAGE_START_IDX = N_CONCEPT_CLIPS + 1  # 3

# Must stay in sync with _MAX_NARRATION_STAGES in ai_generator.py.
_MAX_NARRATION_STAGES = 12
_MAX_STAGE_IDX = _STAGE_START_IDX + _MAX_NARRATION_STAGES - 1    # last stage sentence index
_RESULT_IDX   = _STAGE_START_IDX + _MAX_NARRATION_STAGES         # result sentence index


def build(trace: StepTrace) -> SceneTimeline:
    if trace.algorithm_category == "tree":
        return _build_tree_timeline(trace)
    if trace.algorithm_category == "graph":
        return _build_graph_timeline(trace)
    if trace.algorithm_category == "matrix":
        return _build_matrix_timeline(trace)
    return _build_array_timeline(trace)


# ---------------------------------------------------------------------------
# Array
# ---------------------------------------------------------------------------

def _build_array_timeline(trace: StepTrace) -> SceneTimeline:
    scenes: list[Scene] = []
    frame_counter = 0
    arr_input = trace.input if isinstance(trace.input, list) else []

    # Concept frames — show the initial state while concept audio plays
    concept_frames = [
        SceneFrame(
            frame_id=frame_counter + i, type="init", narration_index=i,
            array_state=list(arr_input), highlight_indices=[],
            value_labels={str(j): str(v) for j, v in enumerate(arr_input)},
            description=f"Initial array: {arr_input}",
        )
        for i in range(N_CONCEPT_CLIPS)
    ]
    frame_counter += N_CONCEPT_CLIPS
    scenes.append(Scene(scene_id=-1, label="Concept", frames=concept_frames))

    init_frame = SceneFrame(
        frame_id=frame_counter, type="init", narration_index=_INTRO_IDX,
        array_state=list(arr_input),
        highlight_indices=[],
        value_labels={str(i): str(v) for i, v in enumerate(arr_input)},
        description=f"Initial array: {arr_input}",
    )
    frame_counter += 1
    scenes.append(Scene(scene_id=0, label="Initial State", frames=[init_frame]))

    stages: dict[int, list[TraceStep]] = {}
    for step in trace.steps:
        stages.setdefault(step.stage, []).append(step)

    sorted_stage_nums = sorted(stages.keys())
    total_stages = len(sorted_stage_nums)
    for pos, stage_num in enumerate(sorted_stage_nums):
        slot = (pos * _MAX_NARRATION_STAGES) // total_stages if total_stages else 0
        effective_idx = _STAGE_START_IDX + slot
        frames: list[SceneFrame] = []
        for step in stages[stage_num]:
            connections = [
                Connection(**{"from": src, "to": step.output_index or 0, "label": step.operation})
                for src in (step.input_indices or [])
            ]
            frames.append(SceneFrame(
                frame_id=frame_counter, type="operation", step_ref=step.step_id,
                narration_index=effective_idx, description=step.description,
                array_state=list(step.array_snapshot or []),
                highlight_indices=list(step.input_indices or []) + ([step.output_index] if step.output_index is not None else []),
                active_connections=connections,
                value_labels={str(step.output_index): str(step.output_value)} if step.output_index is not None else {},
            ))
            frame_counter += 1
        scenes.append(Scene(scene_id=stage_num, label=f"Stage {stage_num}", frames=frames))

    last_snapshot = trace.steps[-1].array_snapshot if trace.steps else arr_input
    scenes.append(Scene(scene_id=len(scenes), label="Result", frames=[SceneFrame(
        frame_id=frame_counter, type="result", narration_index=_RESULT_IDX,
        array_state=list(last_snapshot or []),
        value_labels={"result": str(trace.final_output)},
        description=f"Final result: {trace.final_output}",
    )]))
    frame_counter += 1

    return SceneTimeline(
        algorithm_name=trace.algorithm_name, algorithm_category="array",
        algorithm_subtype=trace.algorithm_subtype,
        total_frames=frame_counter, duration_hint_ms=frame_counter * FRAME_DURATION_MS,
        input=trace.input, scenes=scenes,
    )


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------

def _build_tree_timeline(trace: StepTrace) -> SceneTimeline:
    scenes: list[Scene] = []
    frame_counter = 0
    tree_input = trace.input if isinstance(trace.input, dict) else {}
    initial_node_states = _tree_input_to_node_states(tree_input)

    concept_frames = [
        SceneFrame(
            frame_id=frame_counter + i, type="init", narration_index=i,
            node_states=initial_node_states,
            highlighted_nodes=[tree_input.get("root", "")],
            description=f"Tree with {len(initial_node_states)} nodes, root: {tree_input.get('root')}",
        )
        for i in range(N_CONCEPT_CLIPS)
    ]
    frame_counter += N_CONCEPT_CLIPS
    scenes.append(Scene(scene_id=-1, label="Concept", frames=concept_frames))

    init_frame = SceneFrame(
        frame_id=frame_counter, type="init", narration_index=_INTRO_IDX,
        node_states=initial_node_states,
        highlighted_nodes=[tree_input.get("root", "")],
        description=f"Tree with {len(initial_node_states)} nodes, root: {tree_input.get('root')}",
    )
    frame_counter += 1
    scenes.append(Scene(scene_id=0, label="Initial State", frames=[init_frame]))

    stages: dict[int, list[TraceStep]] = {}
    for step in trace.steps:
        stages.setdefault(step.stage, []).append(step)

    sorted_stage_nums = sorted(stages.keys())
    total_stages = len(sorted_stage_nums)
    for pos, stage_num in enumerate(sorted_stage_nums):
        slot = (pos * _MAX_NARRATION_STAGES) // total_stages if total_stages else 0
        effective_idx = _STAGE_START_IDX + slot
        frames: list[SceneFrame] = []
        for step in stages[stage_num]:
            node_states = _snapshot_to_node_states(step.tree_snapshot) if step.tree_snapshot else initial_node_states
            highlighted = [n for n in [step.node_id] if n]
            parent_edge = []
            if step.node_id and step.parent_id:
                parent_edge = [EdgeHighlight(from_node=step.parent_id, to_node=step.node_id, label=step.operation)]

            frames.append(SceneFrame(
                frame_id=frame_counter, type="operation", step_ref=step.step_id,
                narration_index=effective_idx, description=step.description,
                highlighted_nodes=highlighted,
                highlighted_edges=parent_edge,
                node_states=node_states,
            ))
            frame_counter += 1
        scenes.append(Scene(scene_id=stage_num, label=f"Stage {stage_num}", frames=frames))

    final_snapshot = trace.steps[-1].tree_snapshot if trace.steps and trace.steps[-1].tree_snapshot else None
    final_states = _snapshot_to_node_states(final_snapshot) if final_snapshot else initial_node_states
    scenes.append(Scene(scene_id=len(scenes), label="Result", frames=[SceneFrame(
        frame_id=frame_counter, type="result", narration_index=_RESULT_IDX,
        node_states=final_states,
        value_labels={"result": str(trace.final_output)},
        description=f"Final result: {trace.final_output}",
    )]))
    frame_counter += 1

    return SceneTimeline(
        algorithm_name=trace.algorithm_name, algorithm_category="tree",
        algorithm_subtype=trace.algorithm_subtype,
        total_frames=frame_counter, duration_hint_ms=frame_counter * FRAME_DURATION_MS,
        input=trace.input, scenes=scenes,
    )


def _tree_input_to_node_states(tree_input: dict) -> dict:
    nodes = tree_input.get("nodes", {})
    return {
        node_id: {"value": info.get("value"), "parent": info.get("parent"),
                  "children": info.get("children", []), "visited": False}
        for node_id, info in nodes.items()
    }


def _snapshot_to_node_states(snapshot: dict | None) -> dict:
    if not snapshot:
        return {}
    return {
        node_id: {"value": info.get("value"), "parent": info.get("parent"),
                  "children": info.get("children", []), "visited": info.get("visited", False)}
        for node_id, info in snapshot.items()
    }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _build_graph_timeline(trace: StepTrace) -> SceneTimeline:
    scenes: list[Scene] = []
    frame_counter = 0
    graph_input = trace.input if isinstance(trace.input, dict) else {}
    all_nodes = [str(n) for n in graph_input.get("nodes", [])]
    all_edges = graph_input.get("edges", [])

    initial_graph_state = {
        "nodes": all_nodes, "edges": all_edges,
        "visited": [], "distances": {}, "parent_map": {},
    }

    concept_frames = [
        SceneFrame(
            frame_id=frame_counter + i, type="init", narration_index=i,
            graph_state=initial_graph_state,
            highlighted_nodes=[graph_input.get("start_node", "")],
            description=f"Graph with {len(all_nodes)} nodes, starting at {graph_input.get('start_node')}",
        )
        for i in range(N_CONCEPT_CLIPS)
    ]
    frame_counter += N_CONCEPT_CLIPS
    scenes.append(Scene(scene_id=-1, label="Concept", frames=concept_frames))

    init_frame = SceneFrame(
        frame_id=frame_counter, type="init", narration_index=_INTRO_IDX,
        graph_state=initial_graph_state,
        highlighted_nodes=[graph_input.get("start_node", "")],
        description=f"Graph with {len(all_nodes)} nodes, starting at {graph_input.get('start_node')}",
    )
    frame_counter += 1
    scenes.append(Scene(scene_id=0, label="Initial State", frames=[init_frame]))

    stages: dict[int, list[TraceStep]] = {}
    for step in trace.steps:
        stages.setdefault(step.stage, []).append(step)

    sorted_stage_nums = sorted(stages.keys())
    total_stages = len(sorted_stage_nums)
    for pos, stage_num in enumerate(sorted_stage_nums):
        slot = (pos * _MAX_NARRATION_STAGES) // total_stages if total_stages else 0
        effective_idx = _STAGE_START_IDX + slot
        frames: list[SceneFrame] = []
        for step in stages[stage_num]:
            graph_state = step.graph_snapshot or initial_graph_state
            highlighted_nodes = list(step.visited_nodes or [])
            highlighted_edges = []
            if step.from_node and step.to_node:
                highlighted_edges = [EdgeHighlight(
                    from_node=step.from_node, to_node=step.to_node,
                    label=str(step.edge_weight) if step.edge_weight is not None else step.operation,
                )]
            node_states = {
                n: {
                    "visited": n in (step.visited_nodes or []),
                    "distance": (step.distances or {}).get(n),
                    "active": n in [step.from_node, step.to_node],
                }
                for n in all_nodes
            }

            frames.append(SceneFrame(
                frame_id=frame_counter, type="operation", step_ref=step.step_id,
                narration_index=effective_idx, description=step.description,
                highlighted_nodes=highlighted_nodes,
                highlighted_edges=highlighted_edges,
                node_states=node_states,
                graph_state=graph_state,
            ))
            frame_counter += 1
        scenes.append(Scene(scene_id=stage_num, label=f"Stage {stage_num}", frames=frames))

    final_step = trace.steps[-1] if trace.steps else None
    final_graph_state = final_step.graph_snapshot if final_step and final_step.graph_snapshot else initial_graph_state
    scenes.append(Scene(scene_id=len(scenes), label="Result", frames=[SceneFrame(
        frame_id=frame_counter, type="result", narration_index=_RESULT_IDX,
        graph_state=final_graph_state,
        highlighted_nodes=list((final_step.visited_nodes or []) if final_step else []),
        value_labels={"result": str(trace.final_output)},
        description=f"Final result: {trace.final_output}",
    )]))
    frame_counter += 1

    return SceneTimeline(
        algorithm_name=trace.algorithm_name, algorithm_category="graph",
        algorithm_subtype=trace.algorithm_subtype,
        total_frames=frame_counter, duration_hint_ms=frame_counter * FRAME_DURATION_MS,
        input=trace.input, scenes=scenes,
    )


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

def _build_matrix_timeline(trace: StepTrace) -> SceneTimeline:
    scenes: list[Scene] = []
    frame_counter = 0
    matrix_input = trace.input if isinstance(trace.input, dict) else {}
    initial_grid = matrix_input.get("grid", [])
    start = matrix_input.get("start")
    end = matrix_input.get("end")

    init_desc = (
        f"{len(initial_grid)}×{len(initial_grid[0]) if initial_grid else 0} grid"
        + (f", start: {start}, end: {end}" if start else "")
    )

    concept_frames = [
        SceneFrame(
            frame_id=frame_counter + i, type="init", narration_index=i,
            grid_state=[list(row) for row in initial_grid],
            highlighted_cells=[start] if start else [],
            description=init_desc,
        )
        for i in range(N_CONCEPT_CLIPS)
    ]
    frame_counter += N_CONCEPT_CLIPS
    scenes.append(Scene(scene_id=-1, label="Concept", frames=concept_frames))

    init_frame = SceneFrame(
        frame_id=frame_counter, type="init", narration_index=_INTRO_IDX,
        grid_state=[list(row) for row in initial_grid],
        highlighted_cells=[start] if start else [],
        description=init_desc,
    )
    frame_counter += 1
    scenes.append(Scene(scene_id=0, label="Initial State", frames=[init_frame]))

    stages: dict[int, list[TraceStep]] = {}
    for step in trace.steps:
        stages.setdefault(step.stage, []).append(step)

    sorted_stage_nums = sorted(stages.keys())
    total_stages = len(sorted_stage_nums)
    for pos, stage_num in enumerate(sorted_stage_nums):
        slot = (pos * _MAX_NARRATION_STAGES) // total_stages if total_stages else 0
        effective_idx = _STAGE_START_IDX + slot
        frames: list[SceneFrame] = []
        for step in stages[stage_num]:
            grid_state = step.grid_snapshot if step.grid_snapshot else [list(r) for r in initial_grid]

            # Derive path cells from grid: cells with value 3 are the path
            path_cells = [
                [r, c]
                for r, row in enumerate(grid_state)
                for c, val in enumerate(row)
                if val == 3
            ]

            frames.append(SceneFrame(
                frame_id=frame_counter, type="operation", step_ref=step.step_id,
                narration_index=effective_idx, description=step.description,
                grid_state=grid_state,
                highlighted_cells=[[step.row, step.col]] if step.row is not None and step.col is not None else [],
                path_cells=path_cells if path_cells else None,
                value_labels={f"{step.row},{step.col}": str(step.cell_value)} if step.row is not None else {},
            ))
            frame_counter += 1
        scenes.append(Scene(scene_id=stage_num, label=f"Stage {stage_num}", frames=frames))

    final_step = trace.steps[-1] if trace.steps else None
    final_grid = final_step.grid_snapshot if final_step and final_step.grid_snapshot else [list(r) for r in initial_grid]
    final_path = [
        [r, c] for r, row in enumerate(final_grid) for c, val in enumerate(row) if val == 3
    ]
    scenes.append(Scene(scene_id=len(scenes), label="Result", frames=[SceneFrame(
        frame_id=frame_counter, type="result", narration_index=_RESULT_IDX,
        grid_state=final_grid,
        path_cells=final_path if final_path else None,
        value_labels={"result": str(trace.final_output)},
        description=f"Final result: {trace.final_output}",
    )]))
    frame_counter += 1

    return SceneTimeline(
        algorithm_name=trace.algorithm_name, algorithm_category="matrix",
        algorithm_subtype=trace.algorithm_subtype,
        total_frames=frame_counter, duration_hint_ms=frame_counter * FRAME_DURATION_MS,
        input=trace.input, scenes=scenes,
    )
