"""Pydantic request/response models for the Hōzō API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class WakeRequest(BaseModel):
    job_name: str


class BackupRequest(BaseModel):
    job_name: str


class ShutdownRequest(BaseModel):
    job_name: str


class JobStatusResponse(BaseModel):
    name: str
    source_dataset: str
    target_host: str
    target_dataset: str
    shutdown_after: bool
    description: str


class JobResultResponse(BaseModel):
    job_name: str
    success: bool
    started_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]
    duration_seconds: Optional[float]
    snapshot_count: int
    attempts: int


class StatusResponse(BaseModel):
    jobs: list[JobStatusResponse]
    scheduler_running: bool
