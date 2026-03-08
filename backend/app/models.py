from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


Severity = Literal["high", "medium", "low"]


class ProjectSetupRequest(BaseModel):
    project_url: str
    trigger_keyword: str | None = None
    review_language: str | None = None


class ProjectConfig(BaseModel):
    project_id: int
    project_url: str
    project_path: str
    webhook_id: int
    webhook_url: str
    webhook_secret: str
    trigger_keyword: str
    review_language: str = "Chinese"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectSetupResponse(BaseModel):
    project_id: int
    project_path: str
    webhook_id: int
    webhook_url: str
    trigger_keyword: str
    review_language: str
    created: bool


class ReviewJobResult(BaseModel):
    posted: bool
    note_id: int | None = None
    summary: str


class ProjectState(BaseModel):
    projects: dict[str, ProjectConfig] = Field(default_factory=dict)


class MergeRequestContext(BaseModel):
    project_id: int
    project_path: str
    mr_iid: int
    mr_title: str
    mr_description: str | None = None
    source_branch: str
    target_branch: str
    source_sha: str | None = None
    target_sha: str | None = None
    start_sha: str | None = None
    author: str | None = None
    web_url: str | None = None
    changed_files: list[dict[str, Any]] = Field(default_factory=list)
    unified_diff: str = ""
    repo_path: str
    trigger_comment: str
    review_language: str = "Chinese"


class ReviewFinding(BaseModel):
    title: str
    severity: Severity
    file_path: str | None = None
    line: int | None = None
    details: str
    recommendation: str
    suggested_code: str | None = None
    code_language: str | None = None


class StructuredReview(BaseModel):
    overall_assessment: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    suggested_follow_ups: list[str] = Field(default_factory=list)


class DiffLinePosition(BaseModel):
    old_path: str
    new_path: str
    new_line: int


class JobStatus(BaseModel):
    job_id: str
    project_id: int
    project_path: str
    mr_iid: int
    trigger_note_id: int | None = None
    trigger_comment: str
    status: Literal["queued", "running", "completed", "failed", "skipped"]
    message: str = ""
    summary_note_id: int | None = None
    inline_discussion_ids: list[str] = Field(default_factory=list)
    findings_count: int = 0
    inline_findings_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewJobState(BaseModel):
    jobs: dict[str, JobStatus] = Field(default_factory=dict)
