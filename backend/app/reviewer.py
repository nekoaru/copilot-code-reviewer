from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

from copilot import CopilotClient, PermissionHandler
from git import Repo
from pydantic import ValidationError

from .config import settings
from .models import DiffLinePosition, MergeRequestContext, ReviewFinding, Severity, StructuredReview


MAX_FILE_PATCH_CHARS = 18000
MAX_TOTAL_DIFF_CHARS = 60000
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
SEVERITY_ORDER: dict[Severity, int] = {"low": 1, "medium": 2, "high": 3}
LogCallback = Callable[[str, str], None]
RawStreamStartCallback = Callable[[str], None]
RawStreamChunkCallback = Callable[[str], None]
RawStreamEndCallback = Callable[[], None]


class CopilotReviewer:
    async def review(
        self,
        context: MergeRequestContext,
        log_callback: LogCallback | None = None,
        stream_start_callback: RawStreamStartCallback | None = None,
        stream_chunk_callback: RawStreamChunkCallback | None = None,
        stream_end_callback: RawStreamEndCallback | None = None,
    ) -> StructuredReview:
        prompt = self._build_prompt(context)
        if log_callback:
            log_callback("reviewer", f"Starting Copilot session with model={settings.copilot_model}, language={context.review_language}, timeout={settings.copilot_timeout_seconds}s")
            log_callback("reviewer", f"Prompt prepared, length={len(prompt)} chars")
        client = CopilotClient({"cwd": context.repo_path, "log_level": "error"})
        await client.start()
        if log_callback:
            log_callback("reviewer", f"Copilot client started in cwd={context.repo_path}")
        stream_state = {"open": False, "last_chunk": None, "source": None}
        try:
            async with await client.create_session(
                {
                    "model": settings.copilot_model,
                    "on_permission_request": PermissionHandler.approve_all,
                    "streaming": True,
                }
            ) as session:
                if log_callback:
                    log_callback("reviewer", "Copilot session created")

                def close_stream_if_needed() -> None:
                    if stream_state["open"] and stream_end_callback:
                        stream_end_callback()
                    stream_state["open"] = False
                    stream_state["last_chunk"] = None
                    stream_state["source"] = None

                def append_stream_text(source: str, text: str) -> None:
                    if text == stream_state["last_chunk"] and source == stream_state["source"]:
                        return
                    if stream_state["open"] and stream_state["source"] != source:
                        close_stream_if_needed()
                    if not stream_state["open"]:
                        if stream_start_callback:
                            stream_start_callback(source)
                        stream_state["open"] = True
                        stream_state["source"] = source
                    if stream_chunk_callback:
                        stream_chunk_callback(text)
                    stream_state["last_chunk"] = text
                    stream_state["source"] = source

                def handle_event(event: Any) -> None:
                    event_type = getattr(getattr(event, "type", None), "value", getattr(event, "type", "unknown"))
                    data = getattr(event, "data", None)
                    delta_text = self._extract_delta_text(data)

                    if event_type == "assistant.message_delta" and delta_text:
                        append_stream_text("copilot-stream", delta_text)
                        return

                    if event_type in {"assistant.reasoning_delta", "assistant.streaming_delta"}:
                        return

                    if stream_state["open"] and event_type not in {"assistant.message_delta", "assistant.reasoning_delta", "assistant.streaming_delta"}:
                        close_stream_if_needed()

                    extra = []
                    for attr in ("content", "message", "tool_name", "reasoning", "progress_message"):
                        value = getattr(data, attr, None) if data else None
                        if value:
                            text = str(value).replace("\n", " ")
                            extra.append(f"{attr}={text[:180]}")
                    suffix = f" | {'; '.join(extra)}" if extra else ""
                    if log_callback:
                        log_callback("copilot", f"event={event_type}{suffix}")

                session.on(handle_event)

                response = await session.send_and_wait({"prompt": prompt}, timeout=settings.copilot_timeout_seconds)
                close_stream_if_needed()
                content = getattr(response.data, "content", None) if response else None
                if not content:
                    raise RuntimeError("Copilot returned an empty review")
                if log_callback:
                    log_callback("reviewer", f"Copilot completed, response length={len(content)} chars")
                return self._parse_structured_review(content)
        finally:
            if stream_state["open"] and stream_end_callback:
                stream_end_callback()
            await client.stop()
            if log_callback:
                log_callback("reviewer", "Copilot client stopped")

    def clone_or_update_repo(
        self,
        source_clone_url: str,
        target_clone_url: str,
        project_path: str,
        source_branch: str,
        target_branch: str,
    ) -> Path:
        repo_dir = settings.clone_root / project_path.replace("/", "__")
        if repo_dir.exists():
            repo = Repo(repo_dir)
            origin = repo.remotes.origin
            origin.set_url(source_clone_url)
            origin.fetch(prune=True)
        else:
            repo = Repo.clone_from(source_clone_url, repo_dir)
            repo.remotes.origin.fetch(prune=True)

        if target_clone_url != source_clone_url:
            if "upstream" in [remote.name for remote in repo.remotes]:
                upstream = repo.remotes.upstream
                upstream.set_url(target_clone_url)
            else:
                upstream = repo.create_remote("upstream", target_clone_url)
            upstream.fetch(prune=True)

        for branch in {source_branch, target_branch}:
            try:
                repo.git.fetch("origin", branch)
            except Exception:
                pass
            try:
                repo.git.fetch("upstream", branch)
            except Exception:
                pass
        repo.git.checkout(source_branch)
        return repo_dir

    def build_diff_from_repo(self, repo_path: Path, target_branch: str, source_branch: str) -> str:
        repo = Repo(repo_path)
        remote_names = {remote.name for remote in repo.remotes}
        base_remote = "upstream" if "upstream" in remote_names else "origin"
        base_ref = f"{base_remote}/{target_branch}"
        head_ref = f"origin/{source_branch}"
        return repo.git.diff(f"{base_ref}...{head_ref}", unified=3)

    def build_position_index(self, changed_files: list[dict]) -> dict[str, set[int]]:
        index: dict[str, set[int]] = {}
        for item in changed_files:
            new_path = item.get("new_path")
            diff = item.get("diff") or ""
            if not new_path or not diff:
                continue
            new_lines = index.setdefault(new_path, set())
            old_line = 0
            new_line = 0
            for line in diff.splitlines():
                if line.startswith("@@"):
                    match = re.search(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                    if not match:
                        continue
                    old_line = int(match.group(1))
                    new_line = int(match.group(2))
                    continue
                if line.startswith("+++") or line.startswith("---"):
                    continue
                if line.startswith("+"):
                    new_lines.add(new_line)
                    new_line += 1
                    continue
                if line.startswith("-"):
                    old_line += 1
                    continue
                if line.startswith("\\ No newline"):
                    continue
                old_line += 1
                new_line += 1
        return index

    def resolve_position(self, changed_files: list[dict], file_path: str | None, line: int | None) -> DiffLinePosition | None:
        if not file_path or line is None:
            return None
        position_index = self.build_position_index(changed_files)
        if line not in position_index.get(file_path, set()):
            return None
        file_change = next((item for item in changed_files if item.get("new_path") == file_path), None)
        if not file_change:
            return None
        return DiffLinePosition(
            old_path=file_change.get("old_path") or file_path,
            new_path=file_change.get("new_path") or file_path,
            new_line=line,
        )

    def filter_findings_by_min_severity(self, findings: list[ReviewFinding], min_severity: Severity | str) -> list[ReviewFinding]:
        threshold = SEVERITY_ORDER[min_severity] if min_severity in SEVERITY_ORDER else SEVERITY_ORDER["medium"]
        return [finding for finding in findings if SEVERITY_ORDER[finding.severity] >= threshold]

    def render_summary_markdown(self, review: StructuredReview, inline_min_severity: Severity | str, language: str) -> str:
        labels = self._labels(language)
        findings_md = []
        filtered = self.filter_findings_by_min_severity(review.findings, inline_min_severity)
        if filtered:
            findings_md.append(labels["inline_threshold"].format(severity=str(inline_min_severity).title()))
        if review.findings:
            for finding in review.findings:
                location = finding.file_path or labels["general"]
                if finding.line is not None:
                    location = f"{location}:{finding.line}"
                block = (
                    f"- **{finding.severity.title()}** — `{location}` — **{finding.title}**\n"
                    f"  - {labels['why_it_matters']}: {finding.details}\n"
                    f"  - {labels['recommendation']}: {finding.recommendation}"
                )
                if finding.suggested_code:
                    language_name = finding.code_language or "text"
                    block += f"\n  - {labels['suggested_code']}:\n\n```{language_name}\n{finding.suggested_code.strip()}\n```"
                findings_md.append(block)
        else:
            findings_md.append(f"- {labels['no_issues']}")

        follow_ups_md = "\n".join(f"- {item}" for item in review.suggested_follow_ups) or f"- {labels['none']}"
        return (
            f"## {labels['overall_assessment_header']}\n"
            f"{review.overall_assessment}\n\n"
            f"## {labels['findings_header']}\n"
            f"{'\n'.join(findings_md)}\n\n"
            f"## {labels['follow_ups_header']}\n"
            f"{follow_ups_md}"
        )

    def render_inline_comment(self, finding: ReviewFinding, language: str) -> str:
        labels = self._labels(language)
        content = (
            f"**{finding.severity.title()} — {finding.title}**\n\n"
            f"{labels['why_it_matters']}: {finding.details}\n\n"
            f"{labels['recommendation']}: {finding.recommendation}"
        )
        if finding.suggested_code:
            language_name = finding.code_language or "text"
            content += f"\n\n{labels['suggested_fix']}:\n```{language_name}\n{finding.suggested_code.strip()}\n```"
        return content

    def _build_prompt(self, context: MergeRequestContext) -> str:
        files = []
        total = 0
        for item in context.changed_files:
            diff = (item.get("diff") or "")[:MAX_FILE_PATCH_CHARS]
            block = (
                f"File: {item.get('new_path')}\n"
                f"Old path: {item.get('old_path')}\n"
                f"New file: {item.get('new_file')}\n"
                f"Renamed: {item.get('renamed_file')}\n"
                f"Deleted: {item.get('deleted_file')}\n"
                f"Patch:\n{diff}"
            )
            if total + len(block) > MAX_TOTAL_DIFF_CHARS:
                break
            files.append(block)
            total += len(block)

        per_file_patches = "\n\n".join(files) or "(not available)"
        unified_diff = context.unified_diff[:MAX_TOTAL_DIFF_CHARS] or "(not available)"

        return textwrap.dedent(
            f"""
            You are an expert GitLab merge request reviewer.
            Write all natural-language output fields in {context.review_language}. Keep code snippets and identifiers unchanged unless needed.
            Review the merge request comprehensively for correctness, bugs, security, maintainability, performance, edge cases, and testing gaps.
            Focus on actionable findings only. Do not praise. Do not invent issues unsupported by the code.

            Return ONLY valid JSON. No markdown fences. No commentary.
            Schema:
            {{
              "overall_assessment": "short paragraph",
              "findings": [
                {{
                  "title": "short finding title",
                  "severity": "high|medium|low",
                  "file_path": "path/to/file or null",
                  "line": 123,
                  "details": "why it matters",
                  "recommendation": "concrete recommendation",
                  "suggested_code": "optional replacement snippet only, max 12 lines, omit if low confidence",
                  "code_language": "optional language like python|javascript|typescript"
                }}
              ],
              "suggested_follow_ups": ["item 1", "item 2"]
            }}

            Important constraints:
            - Prefer findings anchored to changed lines in the new file.
            - Use `file_path` and `line` only when you are confident and the line is part of the changed patch.
            - If there are no material issues, return an empty `findings` array.
            - Keep `suggested_follow_ups` concise.
            - Include `suggested_code` only when a small, concrete fix can be shown safely.
            - Prioritize high and medium severity issues; include low severity only if genuinely useful.

            Merge Request metadata:
            - Project: {context.project_path}
            - MR IID: {context.mr_iid}
            - Title: {context.mr_title}
            - Author: {context.author}
            - Source branch: {context.source_branch}
            - Target branch: {context.target_branch}
            - URL: {context.web_url}
            - Trigger comment: {context.trigger_comment}

            Description:
            {context.mr_description or '(none)'}

            Unified diff:
            {unified_diff}

            Per-file patches:
            {per_file_patches}
            """
        ).strip()

    def _parse_structured_review(self, content: str) -> StructuredReview:
        text = content.strip()
        candidate = text
        match = JSON_BLOCK_RE.search(text)
        if match:
            candidate = match.group(1).strip()
        try:
            return StructuredReview.model_validate(json.loads(candidate))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RuntimeError(f"Unable to parse Copilot review JSON: {exc}") from exc

    def _extract_delta_text(self, data: Any) -> str | None:
        if data is None:
            return None
        if isinstance(data, str):
            return data or None
        if isinstance(data, dict):
            for key in ("delta_content", "deltaContent", "delta", "text", "content", "message", "chunk", "value", "reasoning_text", "reasoningText"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
            return None
        for attr in ("delta_content", "deltaContent", "delta", "text", "content", "message", "chunk", "value", "reasoning_text", "reasoningText"):
            value = getattr(data, attr, None)
            if isinstance(value, str) and value:
                return value
        return None

    def _labels(self, language: str) -> dict[str, str]:
        normalized = (language or "").strip().lower()
        if normalized.startswith("zh") or "chinese" in normalized or "中文" in normalized:
            return {
                "overall_assessment_header": "总体评估",
                "findings_header": "问题清单",
                "follow_ups_header": "后续建议",
                "inline_threshold": "_仅对严重级别 >= {severity} 的问题发布行级评论。_",
                "general": "通用",
                "why_it_matters": "为什么重要",
                "recommendation": "建议",
                "suggested_code": "建议代码",
                "suggested_fix": "建议修复",
                "no_issues": "未发现需要指出的实质性问题。",
                "none": "无",
            }
        if normalized.startswith("ja") or "japanese" in normalized or "日本語" in normalized:
            return {
                "overall_assessment_header": "全体評価",
                "findings_header": "指摘事項",
                "follow_ups_header": "次のアクション",
                "inline_threshold": "_重大度が {severity} 以上の指摘にのみインラインコメントを投稿します。_",
                "general": "全般",
                "why_it_matters": "重要な理由",
                "recommendation": "推奨対応",
                "suggested_code": "修正コード例",
                "suggested_fix": "修正案",
                "no_issues": "重大な問題は見つかりませんでした。",
                "none": "なし",
            }
        return {
            "overall_assessment_header": "Overall Assessment",
            "findings_header": "Findings",
            "follow_ups_header": "Suggested Follow-ups",
            "inline_threshold": "_Inline comments are posted only for findings with severity >= {severity}._",
            "general": "General",
            "why_it_matters": "Why it matters",
            "recommendation": "Recommendation",
            "suggested_code": "Suggested code",
            "suggested_fix": "Suggested fix",
            "no_issues": "No material issues found.",
            "none": "None",
        }


reviewer = CopilotReviewer()
