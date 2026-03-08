from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .config import settings


class GitLabClient:
    def __init__(self) -> None:
        self.base_url = settings.gitlab_base_url.rstrip("/")
        self.headers = {"PRIVATE-TOKEN": settings.gitlab_token}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/api/v4{path}"
        headers = kwargs.pop("headers", {})
        headers = {**self.headers, **headers}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            if response.content:
                return response.json()
            return None

    def parse_project_path(self, project_url: str) -> str:
        parsed = urlparse(project_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Invalid GitLab project URL")
        path = parsed.path.strip("/")
        if not path or "/" not in path:
            raise ValueError("Project URL must include namespace and project name")
        return path.removesuffix(".git")

    async def get_project(self, project_ref: str | int) -> dict[str, Any]:
        return await self._request("GET", f"/projects/{quote(str(project_ref), safe='')}")

    async def list_project_hooks(self, project_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"/projects/{project_id}/hooks")

    async def create_or_update_mr_webhook(self, project_id: int, webhook_url: str, secret: str) -> tuple[int, bool]:
        hooks = await self.list_project_hooks(project_id)
        payload = {
            "url": webhook_url,
            "enable_ssl_verification": True,
            "token": secret,
            "merge_requests_events": True,
            "note_events": True,
            "push_events": False,
            "issues_events": False,
            "confidential_issues_events": False,
            "tag_push_events": False,
            "pipeline_events": False,
            "wiki_page_events": False,
            "job_events": False,
            "releases_events": False,
            "resource_access_token_events": False,
        }
        existing = next((hook for hook in hooks if hook.get("url") == webhook_url), None)
        if existing:
            await self._request("PUT", f"/projects/{project_id}/hooks/{existing['id']}", data=payload)
            return existing["id"], False
        result = await self._request("POST", f"/projects/{project_id}/hooks", data=payload)
        return result["id"], True

    async def get_merge_request(self, project_id: int, mr_iid: int) -> dict[str, Any]:
        return await self._request("GET", f"/projects/{project_id}/merge_requests/{mr_iid}")

    async def get_merge_request_changes(self, project_id: int, mr_iid: int) -> dict[str, Any]:
        return await self._request("GET", f"/projects/{project_id}/merge_requests/{mr_iid}/changes")

    async def create_merge_request_note(self, project_id: int, mr_iid: int, body: str) -> dict[str, Any]:
        return await self._request("POST", f"/projects/{project_id}/merge_requests/{mr_iid}/notes", data={"body": body})

    async def create_merge_request_discussion(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
        *,
        base_sha: str,
        start_sha: str,
        head_sha: str,
        old_path: str,
        new_path: str,
        new_line: int,
    ) -> dict[str, Any]:
        data = {
            "body": body,
            "position[position_type]": "text",
            "position[base_sha]": base_sha,
            "position[start_sha]": start_sha,
            "position[head_sha]": head_sha,
            "position[old_path]": old_path,
            "position[new_path]": new_path,
            "position[new_line]": new_line,
        }
        return await self._request(
            "POST",
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            data=data,
        )

    def build_clone_url(self, project_http_url: str) -> str:
        parsed = urlparse(project_http_url)
        token = quote(settings.gitlab_token, safe="")
        return f"https://oauth2:{token}@{parsed.netloc}{parsed.path}"

    def generate_webhook_secret(self) -> str:
        return secrets.token_urlsafe(32)
