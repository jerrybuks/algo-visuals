from typing import Any
from app.schemas.response import StepTrace, TraceStep


def build_trace(
    algorithm_name: str,
    algorithm_category: str,
    algorithm_subtype: str,
    input_data: Any,
    final_output: Any,
    raw_steps: list[dict],
) -> StepTrace:
    typed_steps: list[TraceStep] = []
    stages_seen: set[int] = set()

    for idx, raw in enumerate(raw_steps):
        stage = int(raw.get("stage", 1))
        stages_seen.add(stage)
        typed_steps.append(_normalize_step(idx, stage, raw, algorithm_category, raw_steps))

    return StepTrace(
        algorithm_name=algorithm_name,
        algorithm_category=algorithm_category,
        algorithm_subtype=algorithm_subtype,
        total_steps=len(typed_steps),
        stages=len(stages_seen) if stages_seen else 1,
        input=input_data,
        final_output=final_output,
        steps=typed_steps,
    )


def _normalize_step(
    idx: int,
    stage: int,
    raw: dict,
    category: str,
    all_steps: list[dict],
) -> TraceStep:
    base = dict(
        step_id=idx,
        stage=stage,
        operation=str(raw.get("operation", "op")),
        description=str(raw.get("description", "")),
        parallel_group=_compute_parallel_group(idx, stage, all_steps),
    )

    if category == "array":
        base.update(
            input_indices=[int(i) for i in raw.get("input_indices", [])],
            output_index=int(raw.get("output_index", 0)) if raw.get("output_index") is not None else None,
            input_values=raw.get("input_values", []),
            output_value=raw.get("output_value"),
            array_snapshot=list(raw.get("array_snapshot", [])),
        )

    elif category == "tree":
        base.update(
            node_id=str(raw["node_id"]) if raw.get("node_id") is not None else None,
            node_value=raw.get("node_value"),
            parent_id=str(raw["parent_id"]) if raw.get("parent_id") is not None else None,
            children=[str(c) for c in raw.get("children", [])],
            tree_snapshot=raw.get("tree_snapshot"),
        )

    elif category == "graph":
        base.update(
            from_node=str(raw["from_node"]) if raw.get("from_node") is not None else None,
            to_node=str(raw["to_node"]) if raw.get("to_node") is not None else None,
            edge_weight=raw.get("edge_weight"),
            visited_nodes=[str(n) for n in raw.get("visited_nodes", [])],
            distances={str(k): v for k, v in raw.get("distances", {}).items()},
            graph_snapshot=raw.get("graph_snapshot"),
        )

    elif category == "matrix":
        base.update(
            row=int(raw["row"]) if raw.get("row") is not None else None,
            col=int(raw["col"]) if raw.get("col") is not None else None,
            cell_value=raw.get("cell_value"),
            grid_snapshot=raw.get("grid_snapshot"),
        )

    return TraceStep(**base)


def _compute_parallel_group(step_idx: int, stage: int, raw_steps: list[dict]) -> int:
    group = 0
    for i in range(step_idx):
        if int(raw_steps[i].get("stage", 1)) == stage:
            group += 1
    return group
