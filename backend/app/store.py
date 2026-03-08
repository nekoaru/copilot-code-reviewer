from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import settings
from .models import JobStatus, ProjectConfig, ProjectState, ReviewJobState

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class JsonStore:
    def __init__(self, path: Path, model_cls):
        self.path = path
        self.model_cls = model_cls
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(model_cls())

    def load(self):
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return self.model_cls.model_validate(payload)

    def save(self, state) -> None:
        self.path.write_text(
            json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ProjectConfigStore(JsonStore):
    def __init__(self, path: Path):
        super().__init__(path, ProjectState)

    def upsert(self, config: ProjectConfig) -> None:
        state = self.load()
        config.updated_at = datetime.utcnow()
        state.projects[str(config.project_id)] = config
        self.save(state)

    def get_by_project_id(self, project_id: int) -> ProjectConfig | None:
        state = self.load()
        return state.projects.get(str(project_id))


class ReviewJobStore(JsonStore):
    def __init__(self, path: Path):
        super().__init__(path, ReviewJobState)

    def upsert(self, job: JobStatus) -> None:
        state = self.load()
        job.updated_at = datetime.utcnow()
        state.jobs[job.job_id] = job
        self.save(state)

    def get_by_job_id(self, job_id: str) -> JobStatus | None:
        return self.load().jobs.get(job_id)

    def get_by_trigger_note(self, project_id: int, trigger_note_id: int | None) -> JobStatus | None:
        if trigger_note_id is None:
            return None
        for job in self.load().jobs.values():
            if job.project_id == project_id and job.trigger_note_id == trigger_note_id:
                return job
        return None


class JobLogStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        safe_job_id = job_id.replace("/", "_")
        return self.root / f"{safe_job_id}.log"

    def _timestamp(self) -> str:
        return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    def append(self, job_id: str, message: str, source: str = "system") -> None:
        line = f"[{self._timestamp()}] [{source}] {message}\n"
        self._path(job_id).open("a", encoding="utf-8").write(line)

    def append_raw(self, job_id: str, text: str) -> None:
        self._path(job_id).open("a", encoding="utf-8").write(text)

    def start_stream(self, job_id: str, source: str = "copilot-stream") -> None:
        self.append_raw(job_id, f"[{self._timestamp()}] [{source}] ")

    def end_stream(self, job_id: str) -> None:
        self.append_raw(job_id, "\n")

    def read(self, job_id: str) -> str:
        path = self._path(job_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def reset(self, job_id: str) -> None:
        self._path(job_id).write_text("", encoding="utf-8")


project_store = ProjectConfigStore(settings.config_store_path)
job_store = ReviewJobStore(settings.job_store_path)
job_log_store = JobLogStore(settings.job_logs_dir)
