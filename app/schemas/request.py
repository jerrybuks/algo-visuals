from typing import Any
from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=5, max_length=500, description="Natural language algorithm description")
    input_data: Any | None = Field(
        None,
        description=(
            "Optional custom input. "
            "Array algorithms: list e.g. [1,2,3,4,5,6,7,8]. "
            "Tree algorithms: dict e.g. {\"nodes\": {\"1\": {\"value\": 10, \"parent\": null, \"children\": [\"2\",\"3\"]}}, \"root\": \"1\"}. "
            "Graph algorithms: dict e.g. {\"nodes\": [\"A\",\"B\",\"C\"], \"edges\": [{\"from\":\"A\",\"to\":\"B\",\"weight\":4}], \"directed\": false, \"start_node\": \"A\"}."
        ),
    )
