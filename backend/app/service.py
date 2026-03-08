from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException

from .config import settings
from .gitlab import GitLabClient
from .models import (
    JobStatus,
    MergeRequestContext,
    ProjectConfig,
    ProjectSetupRequest,
    ProjectSetupResponse,
    ReviewJobResult,
)
from .reviewer import reviewer
from .store import job_log_store, job_store, project_store

logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(self) -> None:
        self.gitlab = GitLabClient()

    def log_job(self, job_id: str, message: str, source: str = "service") -> None:
        job_log_store.append(job_id, message, source=source)

    async def setup_project(self, payload: ProjectSetupRequest, webhook_url: str) -> ProjectSetupResponse:
        project_path = self.gitlab.parse_project_path(payload.project_url)
        project = await self.gitlab.get_project(project_path)
        existing = project_store.get_by_project_id(project["id"])
        secret = existing.webhook_secret if existing else self.gitlab.generate_webhook_secret()
        hook_id, created = await self.gitlab.create_or_update_mr_webhook(project["id"], webhook_url, secret)

        trigger_keyword = payload.trigger_keyword or (existing.trigger_keyword if existing else None) or settings.review_trigger_keyword
        review_language = payload.review_language or (existing.review_language if existing else None) or settings.default_review_language
        config = ProjectConfig(
            project_id=project["id"],
            project_url=project["web_url"],
            project_path=project["path_with_namespace"],
            webhook_id=hook_id,
            webhook_url=webhook_url,
            webhook_secret=secret,
            trigger_keyword=trigger_keyword,
            review_language=review_language,
        )
        project_store.upsert(config)
        return ProjectSetupResponse(
            project_id=config.project_id,
            project_path=config.project_path,
            webhook_id=config.webhook_id,
            webhook_url=config.webhook_url,
            trigger_keyword=config.trigger_keyword,
            review_language=config.review_language,
            created=created,
        )

    def verify_webhook_token(self, project_id: int, provided_token: str | None) -> ProjectConfig:
        config = project_store.get_by_project_id(project_id)
        if not config:
            raise HTTPException(status_code=404, detail="Project is not configured")
        if not provided_token or provided_token != config.webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook token")
        return config

    def maybe_schedule_review(self, background_tasks: BackgroundTasks, body: dict, project_config: ProjectConfig) -> dict:
        if body.get("object_kind") != "note":
            return {"scheduled": False, "reason": "unsupported_event"}
        attrs = body.get("object_attributes") or {}
        merge_request = body.get("merge_request") or {}
        if not merge_request:
            return {"scheduled": False, "reason": "not_merge_request_note"}
        note = attrs.get("note") or ""
        if project_config.trigger_keyword not in note:
            return {"scheduled": False, "reason": "keyword_not_found"}
        if attrs.get("system") is True:
            return {"scheduled": False, "reason": "system_note"}

        trigger_note_id = attrs.get("id")
        existing_job = job_store.get_by_trigger_note(project_config.project_id, trigger_note_id)
        if existing_job and existing_job.status in {"queued", "running", "completed"}:
            self.log_job(existing_job.job_id, f"Duplicate webhook received for trigger_note_id={trigger_note_id}; ignored", source="webhook")
            return {
                "scheduled": False,
                "reason": "duplicate_trigger",
                "job_id": existing_job.job_id,
                "job_status": existing_job.status,
            }

        job_id = f"{project_config.project_id}-{merge_request['iid']}-{trigger_note_id or int(datetime.utcnow().timestamp())}"
        job = JobStatus(
            job_id=job_id,
            project_id=project_config.project_id,
            project_path=project_config.project_path,
            mr_iid=merge_request["iid"],
            trigger_note_id=trigger_note_id,
            trigger_comment=note,
            status="queued",
            message="Review request queued",
        )
        job_store.upsert(job)
        job_log_store.reset(job_id)
        self.log_job(job_id, f"Queued review for project={project_config.project_path} mr=!{merge_request['iid']}")
        self.log_job(job_id, f"Trigger note id={trigger_note_id}, language={project_config.review_language}, keyword={project_config.trigger_keyword}")
        background_tasks.add_task(self._run_review_job, body, project_config, job_id)
        return {"scheduled": True, "reason": "queued", "job_id": job_id, "job_status": "queued"}

    async def _run_review_job(self, body: dict, project_config: ProjectConfig, job_id: str) -> None:
        project_id = project_config.project_id
        mr_iid = body["merge_request"]["iid"]
        job = job_store.get_by_job_id(job_id)
        if not job:
            return
        job.status = "running"
        job.message = f"Fetching MR data and running Copilot review in {project_config.review_language}"
        job_store.upsert(job)
        self.log_job(job_id, job.message)
        try:
            result = await self.review_merge_request(project_id, mr_iid, job.trigger_comment, job, project_config.review_language)
            job.status = "completed"
            job.message = result.summary
            job.summary_note_id = result.note_id
            job_store.upsert(job)
            self.log_job(job_id, f"Job completed successfully. summary_note_id={result.note_id}")
            logger.info("Posted review result for project=%s mr=%s posted=%s", project_id, mr_iid, result.posted)
        except Exception as exc:
            logger.exception("Failed review job for project=%s mr=%s", project_id, mr_iid)
            job.status = "failed"
            job.message = str(exc)
            job_store.upsert(job)
            self.log_job(job_id, f"Job failed: {exc}", source="error")
            try:
                note = await self.gitlab.create_merge_request_note(
                    project_id,
                    mr_iid,
                    "🤖 Copilot review failed. Please check the reviewer service logs and retry.",
                )
                job.summary_note_id = note.get("id")
                job_store.upsert(job)
                self.log_job(job_id, f"Failure note posted to MR, note_id={note.get('id')}")
            except Exception as note_exc:
                logger.exception("Failed to post failure note for project=%s mr=%s", project_id, mr_iid)
                self.log_job(job_id, f"Failed to post failure note: {note_exc}", source="error")

    async def review_merge_request(
        self,
        project_id: int,
        mr_iid: int,
        trigger_comment: str,
        job: JobStatus,
        review_language: str,
    ) -> ReviewJobResult:
        self.log_job(job.job_id, f"Fetching merge request details for !{mr_iid}")
        mr = await self.gitlab.get_merge_request(project_id, mr_iid)
        changes = await self.gitlab.get_merge_request_changes(project_id, mr_iid)
        source_project = await self.gitlab.get_project(mr["source_project_id"])
        target_project = await self.gitlab.get_project(mr["target_project_id"])
        self.log_job(job.job_id, f"Loaded MR title={mr['title']!r}, source={mr['source_branch']}, target={mr['target_branch']}")
        self.log_job(job.job_id, f"Changed files reported by GitLab: {len(changes.get('changes', []))}")

        self.log_job(job.job_id, "Cloning/updating repository snapshot for review")
        repo_path = await asyncio.to_thread(
            reviewer.clone_or_update_repo,
            self.gitlab.build_clone_url(source_project["http_url_to_repo"]),
            self.gitlab.build_clone_url(target_project["http_url_to_repo"]),
            source_project["path_with_namespace"],
            mr["source_branch"],
            mr["target_branch"],
        )
        self.log_job(job.job_id, f"Repository ready at {Path(repo_path).resolve()}")
        unified_diff = await asyncio.to_thread(
            reviewer.build_diff_from_repo,
            repo_path,
            mr["target_branch"],
            mr["source_branch"],
        )
        self.log_job(job.job_id, f"Unified diff generated, length={len(unified_diff)} chars")

        diff_refs = mr.get("diff_refs") or {}
        context = MergeRequestContext(
            project_id=project_id,
            project_path=source_project["path_with_namespace"],
            mr_iid=mr_iid,
            mr_title=mr["title"],
            mr_description=mr.get("description"),
            source_branch=mr["source_branch"],
            target_branch=mr["target_branch"],
            source_sha=diff_refs.get("head_sha") or mr.get("sha"),
            target_sha=diff_refs.get("base_sha"),
            start_sha=diff_refs.get("start_sha"),
            author=(mr.get("author") or {}).get("username"),
            web_url=mr.get("web_url"),
            changed_files=changes.get("changes", []),
            unified_diff=unified_diff,
            repo_path=str(Path(repo_path).resolve()),
            trigger_comment=trigger_comment,
            review_language=review_language,
        )

        job.message = f"Copilot is analyzing the merge request in {review_language}"
        job_store.upsert(job)
        self.log_job(job.job_id, job.message)
        structured_review = await reviewer.review(
            context,
            log_callback=lambda source, message: self.log_job(job.job_id, message, source=source),
            stream_start_callback=lambda source: job_log_store.start_stream(job.job_id, source=source),
            stream_chunk_callback=lambda chunk: job_log_store.append_raw(job.job_id, chunk),
            stream_end_callback=lambda: job_log_store.end_stream(job.job_id),
        )
        self.log_job(job.job_id, f"Structured review parsed, findings={len(structured_review.findings)}")
        inline_findings = reviewer.filter_findings_by_min_severity(
            structured_review.findings,
            settings.inline_min_severity,
        )
        self.log_job(job.job_id, f"Eligible inline findings at severity >= {settings.inline_min_severity.title()}: {len(inline_findings)}")
        discussion_ids = await self._post_inline_discussions(project_id, mr_iid, context, inline_findings, job.job_id)
        summary_body = f"## 🤖 Copilot Review\n\n{reviewer.render_summary_markdown(structured_review, settings.inline_min_severity, review_language)}"
        self.log_job(job.job_id, "Posting summary note to GitLab MR")
        summary_note = await self.gitlab.create_merge_request_note(project_id, mr_iid, summary_body)

        job.inline_discussion_ids = discussion_ids
        job.findings_count = len(structured_review.findings)
        job.inline_findings_count = len(inline_findings)
        job.summary_note_id = summary_note.get("id")
        job.message = (
            f"Posted {review_language} summary and {len(discussion_ids)} inline discussion(s) "
            f"with severity >= {settings.inline_min_severity.title()}"
        )
        job_store.upsert(job)
        self.log_job(job.job_id, f"Summary note posted, note_id={summary_note.get('id')}")
        return ReviewJobResult(
            posted=True,
            note_id=summary_note.get("id"),
            summary=(
                f"Review posted in {review_language} with {len(discussion_ids)} inline discussion(s) "
                f"at severity >= {settings.inline_min_severity.title()}"
            ),
        )

    async def _post_inline_discussions(
        self,
        project_id: int,
        mr_iid: int,
        context: MergeRequestContext,
        findings,
        job_id: str,
    ) -> list[str]:
        discussion_ids: list[str] = []
        for index, finding in enumerate(findings, start=1):
            position = reviewer.resolve_position(context.changed_files, finding.file_path, finding.line)
            if not position or not context.target_sha or not context.source_sha or not context.start_sha:
                self.log_job(job_id, f"Skipping inline finding #{index}: cannot resolve position for {finding.file_path}:{finding.line}")
                continue
            try:
                self.log_job(job_id, f"Posting inline finding #{index} to {position.new_path}:{position.new_line}")
                discussion = await self.gitlab.create_merge_request_discussion(
                    project_id,
                    mr_iid,
                    reviewer.render_inline_comment(finding, context.review_language),
                    base_sha=context.target_sha,
                    start_sha=context.start_sha,
                    head_sha=context.source_sha,
                    old_path=position.old_path,
                    new_path=position.new_path,
                    new_line=position.new_line,
                )
                discussion_ids.append(str(discussion.get("id")))
                self.log_job(job_id, f"Inline discussion posted, discussion_id={discussion.get('id')}")
            except Exception as exc:
                logger.exception(
                    "Failed to create inline discussion for project=%s mr=%s file=%s line=%s",
                    project_id,
                    mr_iid,
                    finding.file_path,
                    finding.line,
                )
                self.log_job(job_id, f"Failed to create inline discussion: {exc}", source="error")
        return discussion_ids


service = ReviewService()
