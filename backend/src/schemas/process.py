from typing import Any

from pydantic import BaseModel, Field


class LocatorCandidateIn(BaseModel):
    strategy: str = Field(default="css")
    selector: str
    desc: str = ""


class BranchMetaIn(BaseModel):
    branch_id: str = Field(max_length=64)
    path_id: str | None = Field(default=None, max_length=64)
    when: str | None = Field(default=None, max_length=255)
    merge_seq: int | None = Field(default=None, ge=1)
    is_default: bool = False


class StepUpdateRequest(BaseModel):
    action: str | None = None
    target_desc: str | None = None
    locator_candidates: list[LocatorCandidateIn] | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    assert_rule: str | None = None
    depends_on: list[int] | None = None
    branch_meta: BranchMetaIn | dict | None = None


class StepImportRequest(BaseModel):
    steps: list[dict[str, Any]]
    replace: bool = False


class ProcessActionRequest(BaseModel):
    note: str = ""
