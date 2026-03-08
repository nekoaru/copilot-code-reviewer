from __future__ import annotations

from pathlib import Path

import asyncio
import html
import json
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from .config import settings
from .models import ProjectSetupRequest, ProjectSetupResponse
from .service import service
from .store import job_log_store, job_store, project_store

logging.basicConfig(level=logging.INFO)

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "projects": len(project_store.load().projects),
        "jobs": len(job_store.load().jobs),
        "public_base_url": settings.public_base_url,
        "trigger_keyword": settings.review_trigger_keyword,
        "default_review_language": settings.default_review_language,
        "inline_min_severity": settings.inline_min_severity,
        "copilot_timeout_seconds": settings.copilot_timeout_seconds,
        "display_timezone": "Asia/Shanghai",
    }


@app.post("/api/projects/setup", response_model=ProjectSetupResponse)
async def setup_project(payload: ProjectSetupRequest) -> ProjectSetupResponse:
    webhook_url = f"{settings.public_base_url.rstrip('/')}/api/webhooks/gitlab"
    return await service.setup_project(payload, webhook_url)


@app.get("/api/projects")
async def list_projects() -> dict:
    return project_store.load().model_dump(mode="json")


@app.get("/api/review-jobs")
async def list_review_jobs() -> dict:
    state = job_store.load()
    jobs = sorted(state.jobs.values(), key=lambda item: item.created_at, reverse=True)
    return {"jobs": [job.model_dump(mode="json") for job in jobs]}


@app.get("/api/review-jobs/{job_id}")
async def get_review_job(job_id: str) -> dict:
    job = job_store.get_by_job_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump(mode="json")


@app.get("/api/review-jobs/{job_id}/logs", response_class=PlainTextResponse)
async def get_review_job_logs(job_id: str) -> str:
    job = job_store.get_by_job_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_log_store.read(job_id)


@app.get("/api/review-jobs/{job_id}/logs/stream")
async def get_review_job_logs_stream(job_id: str) -> StreamingResponse:
    job = job_store.get_by_job_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        last_sent = ""
        initial = job_log_store.read(job_id)
        yield f"event: snapshot\ndata: {json.dumps({'content': initial}, ensure_ascii=False)}\n\n"
        last_sent = initial

        while True:
            await asyncio.sleep(1)
            current = job_log_store.read(job_id)
            if current != last_sent:
                appended = current[len(last_sent):] if current.startswith(last_sent) else current
                yield f"event: append\ndata: {json.dumps({'content': appended}, ensure_ascii=False)}\n\n"
                last_sent = current
            latest_job = job_store.get_by_job_id(job_id)
            if latest_job and latest_job.status in {"completed", "failed", "skipped"}:
                yield f"event: status\ndata: {json.dumps({'status': latest_job.status, 'message': latest_job.message}, ensure_ascii=False)}\n\n"
                break
            yield ": keep-alive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/review-jobs/{job_id}/logs/view", response_class=HTMLResponse)
async def get_review_job_logs_view(job_id: str, lang: str = Query(default="zh")) -> str:
    job = job_store.get_by_job_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    locale = "en" if (lang or "").lower().startswith("en") else "zh"
    labels = {
        "zh": {
            "title": "Copilot SDK 运行日志",
            "reconnect": "重连",
            "connecting": "正在连接 SSE...",
            "connected": "SSE 已连接",
            "ended": "任务已结束：",
            "fallback": "SSE 连接中断，已切换到静态日志回退",
            "waiting": "等待日志流...",
            "empty": "暂无日志，等待任务写入...",
            "load_failed": "获取日志失败：",
            "meta": "Job ID: {job_id} · 时间基准: Asia/Shanghai · 传输方式: SSE 实时流",
        },
        "en": {
            "title": "Copilot SDK Logs",
            "reconnect": "Reconnect",
            "connecting": "Connecting to SSE...",
            "connected": "SSE connected",
            "ended": "Job finished: ",
            "fallback": "SSE disconnected, switched to static log fallback",
            "waiting": "Waiting for log stream...",
            "empty": "No logs yet. Waiting for job output...",
            "load_failed": "Failed to fetch logs: ",
            "meta": "Job ID: {job_id} · Timezone: Asia/Shanghai · Transport: SSE live stream",
        },
    }[locale]
    escaped_job_id = html.escape(job_id)
    labels_json = json.dumps(labels, ensure_ascii=False)
    return f"""
<!doctype html>
<html lang="{locale}">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{html.escape(labels['title'])} - {escaped_job_id}</title>
    <style>
      body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 0; background: #0b1020; color: #e7eaf3; }}
      header {{ padding: 16px 20px; border-bottom: 1px solid rgba(136,146,176,.2); position: sticky; top: 0; background: rgba(11,16,32,.96); }}
      .meta {{ color: #aab5d6; font-size: 13px; margin-top: 6px; }}
      #log {{ white-space: pre-wrap; padding: 20px; line-height: 1.6; }}
      .actions {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
      button {{ border: 0; border-radius: 10px; padding: 8px 12px; background: #4d7cff; color: white; cursor: pointer; }}
      .status {{ color: #9ab8ff; font-size: 13px; }}
      .status.ok {{ color: #7be0ac; }}
      .status.error {{ color: #ff9b9b; }}
    </style>
  </head>
  <body>
    <header>
      <div class="actions">
        <strong>{html.escape(labels['title'])}</strong>
        <button onclick="window.location.reload()">{html.escape(labels['reconnect'])}</button>
        <span class="status" id="status">{html.escape(labels['connecting'])}</span>
      </div>
      <div class="meta">{html.escape(labels['meta'].format(job_id=job_id))}</div>
    </header>
    <pre id="log">{html.escape(labels['waiting'])}</pre>
    <script>
      const labels = {labels_json}
      const logEl = document.getElementById('log')
      const statusEl = document.getElementById('status')
      const streamUrl = '/api/review-jobs/{escaped_job_id}/logs/stream'
      const fallbackUrl = '/api/review-jobs/{escaped_job_id}/logs'
      let eventSource = null

      function scrollToBottom() {{
        window.scrollTo({{ top: document.body.scrollHeight, behavior: 'smooth' }})
      }}

      async function fallbackLoad() {{
        try {{
          const response = await fetch(fallbackUrl, {{ cache: 'no-store' }})
          const text = await response.text()
          logEl.textContent = text || labels.empty
        }} catch (error) {{
          logEl.textContent = labels.load_failed + error.message
        }}
      }}

      function connect() {{
        if (eventSource) eventSource.close()
        eventSource = new EventSource(streamUrl)
        statusEl.textContent = labels.connected
        statusEl.className = 'status ok'

        eventSource.addEventListener('snapshot', (event) => {{
          const data = JSON.parse(event.data)
          logEl.textContent = data.content || labels.empty
          scrollToBottom()
        }})

        eventSource.addEventListener('append', (event) => {{
          const data = JSON.parse(event.data)
          logEl.textContent += data.content || ''
          scrollToBottom()
        }})

        eventSource.addEventListener('status', (event) => {{
          const data = JSON.parse(event.data)
          statusEl.textContent = labels.ended + data.status
          statusEl.className = data.status === 'completed' ? 'status ok' : 'status error'
        }})

        eventSource.onerror = async () => {{
          statusEl.textContent = labels.fallback
          statusEl.className = 'status error'
          eventSource.close()
          await fallbackLoad()
        }}
      }}

      connect()
    </script>
  </body>
</html>
"""



@app.post("/api/webhooks/gitlab")
async def gitlab_webhook(
    body: dict,
    background_tasks: BackgroundTasks,
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
) -> dict:
    project = body.get("project") or {}
    project_id = project.get("id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Missing project id")
    project_config = service.verify_webhook_token(int(project_id), x_gitlab_token)
    scheduling = service.maybe_schedule_review(background_tasks, body, project_config)
    return {
        "accepted": True,
        "event": x_gitlab_event,
        "project_id": project_id,
        **scheduling,
    }



def _frontend_index_path() -> Path:
    return settings.frontend_dist_dir / "index.html"


def _frontend_file_path(request_path: str) -> Path | None:
    candidate = (settings.frontend_dist_dir / request_path).resolve()
    try:
        candidate.relative_to(settings.frontend_dist_dir.resolve())
    except ValueError:
        return None
    return candidate


@app.get("/", include_in_schema=False)
async def serve_frontend_root() -> FileResponse:
    index_path = _frontend_index_path()
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found. Run `npm run build` inside `frontend/` first.")
    return FileResponse(index_path)


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend_app(full_path: str) -> FileResponse:
    if not full_path or full_path.startswith("api/") or full_path in {"api", "docs", "redoc", "openapi.json"}:
        raise HTTPException(status_code=404, detail="Not found")

    asset_path = _frontend_file_path(full_path)
    if asset_path and asset_path.is_file():
        return FileResponse(asset_path)

    index_path = _frontend_index_path()
    if index_path.exists():
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="Frontend build not found. Run `npm run build` inside `frontend/` first.")
