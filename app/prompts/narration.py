SYSTEM_PROMPT = """You write short narration for algorithm animations. Explain like you're talking to a curious 15-year-old — simple, clear, friendly.

Structure (in order):
1. What is this algorithm? (1 sentence, no input values)
2. How does it work? (1 sentence, no input values)
3. Introduce the input (1 sentence, e.g. "Let's watch it sort 5, 3, 8, 1.")
4. One sentence per stage — describe the PHASE of work happening, not a single micro-operation. Each sentence should cover what the algorithm is doing broadly at that point.
5. State the final result (1 sentence)

Rules:
- Max 20 words per sentence
- No jargon (no "subarray", "partition", "index", "node", "pointer", "iteration")
- Use plain words: "split", "compare", "swap", "put X before Y", "combine"
- Stage sentences describe phases, not individual steps — avoid repeating similar sentences
- Vary sentence structure so narration sounds like a story, not a list

Return ONLY valid JSON: { "sentences": [...], "stage_step_indices": [...] }
stage_step_indices: one integer per STAGE sentence, 0-based index into algorithm_steps."""


def build_user_prompt(
    algorithm_name: str,
    category: str,
    input_data: object,
    final_output: object,
    stages_with_steps: list[dict],
    algorithm_steps: list[str],
) -> str:
    stage_lines = []
    for stage in stages_with_steps:
        stage_num = stage["stage"]
        ops = stage["ops"][:4]
        descs = []
        for op in ops:
            desc = op.get("description", "")
            if category == "array":
                snap = op.get("array_snapshot")
                in_vals = op.get("input_values", [])
                out_val = op.get("output_value")
                extras = []
                if in_vals: extras.append(f"inputs={in_vals}")
                if out_val is not None: extras.append(f"result={out_val}")
                if snap: extras.append(f"array={snap}")
                if extras: desc += " [" + ", ".join(extras) + "]"
            elif category == "tree":
                node, val = op.get("node_id"), op.get("node_value")
                if node is not None: desc += f" [node={node}, value={val}]"
            elif category == "graph":
                frm, to = op.get("from_node"), op.get("to_node")
                visited, dists = op.get("visited_nodes", []), op.get("distances", {})
                if frm and to: desc += f" [edge {frm}→{to}]"
                if visited: desc += f" [visited={visited}]"
                if dists: desc += f" [distances={dists}]"
            elif category == "matrix":
                r, c, val = op.get("row"), op.get("col"), op.get("cell_value")
                if r is not None: desc += f" [cell ({r},{c})={val}]"
            descs.append(desc)
        extra = len(stage["ops"]) - 4
        if extra > 0: descs.append(f"...+{extra} more")
        stage_lines.append(f"Stage {stage_num}: " + " | ".join(descs))

    n = len(stages_with_steps)
    steps_list = ", ".join(f"[{i}] {s}" for i, s in enumerate(algorithm_steps)) if algorithm_steps else "none"

    return f"""Algorithm: {algorithm_name}
Input: {input_data}  →  Output: {final_output}
Algorithm steps: {steps_list}

Stages:
{chr(10).join(stage_lines)}

Write {n + 4} sentences total ({n} stage sentences).
Return: {{ "sentences": [...], "stage_step_indices": [{n} integers] }}"""
