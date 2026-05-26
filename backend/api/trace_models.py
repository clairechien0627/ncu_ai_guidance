"""Request/response models shared by trace, evaluation, experiment, and dataset APIs."""

from pydantic import BaseModel


class TraceFeedbackRequest(BaseModel):
    quality_score: float | None = None
    user_feedback: str | None = None


class TraceBookmarkRequest(BaseModel):
    bookmarked: bool


class BatchDeleteTracesRequest(BaseModel):
    ids: list[str]  # trace_ids to delete


class BatchScoreResponse(BaseModel):
    queued: int
    message: str


class DatasetCreateRequest(BaseModel):
    name: str
    description: str | None = None
    source: str | None = None
    metadata: dict | None = None
    input_schema: dict | None = None
    expected_output_schema: dict | None = None


class DatasetManualItemRequest(BaseModel):
    input: dict | None = None
    output: dict | str | None = None
    expected_output: dict | str | None = None
    context: dict | None = None
    tags: list[str] | None = None
    metadata: dict | None = None


class DatasetTraceItemRequest(BaseModel):
    trace_id: str
    tags: list[str] | None = None


class DatasetLowQualityRequest(BaseModel):
    max_quality: float = 3.0
    limit: int = 50


class DatasetEvalRunRequest(BaseModel):
    name: str | None = None
    metadata: dict | None = None


class ExperimentReplayRequest(BaseModel):
    dataset_id: str
    name: str | None = None
    target_agent: str | None = None
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    runtime_config: dict | None = None
    metadata: dict | None = None


class ExperimentEvalRequest(BaseModel):
    name: str | None = None
    metadata: dict | None = None
