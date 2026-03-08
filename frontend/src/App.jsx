import { useEffect, useMemo, useState } from 'react'

const apiBase = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')
const defaultProjectUrl = 'https://gitlab.com/agentic-devops/demo-app-02'
const defaultKeyword = '/copilot-review'
const defaultReviewLanguage = 'Chinese'
const uiLocaleStorageKey = 'ui-language'

const translations = {
  zh: {
    pageTitle: 'Merge Request Reviewer Agent',
    pageDescription:
      '输入 GitLab 项目地址后，服务会通过 GitLab API 自动创建 MR/Note webhook；当 MR 评论中出现触发词时，后端会拉取代码、调用 Copilot SDK 分析，并将 summary 与行级 comment 回写到 MR。',
    uiLanguage: '界面语言',
    uiLanguageOptions: {
      zh: '🇨🇳 中文',
      en: '🇺🇸 English',
    },
    setupTitle: '项目接入',
    projectUrl: 'GitLab Project URL',
    triggerKeyword: '触发关键字',
    reviewLanguage: 'Review 语言',
    reviewLanguagePlaceholder: 'Chinese / English / Japanese',
    setupSubmitting: '配置中...',
    setupSubmit: '配置 Webhook',
    setupSuccess: (result) => `已配置项目 ${result.project_path}，Webhook ID 为 ${result.webhook_id}，Review 语言为 ${result.review_language}。`,
    serviceStatus: '当前服务状态',
    sameOriginApi: '同源 (/api)',
    loading: '加载中...',
    defaultTriggerKeyword: '默认触发词',
    defaultLanguage: '默认语言',
    displayTimezone: '显示时区',
    configuredProjects: '已配置项目数',
    totalJobs: '任务总数',
    inlineThreshold: 'Inline 阈值',
    copilotTimeout: 'Copilot 超时',
    timeoutSeconds: (seconds) => `${seconds} 秒`,
    timezoneHint: '页面中的时间统一按东八区上海时间显示；运行中的任务可以点击右侧按钮进入实时日志页。',
    projectsTitle: '已配置项目',
    noProjects: '还没有项目，先在上面填一个 GitLab 项目地址。',
    projectColumn: 'Project',
    webhookColumn: 'Webhook',
    triggerColumn: 'Trigger',
    languageColumn: 'Language',
    jobsTitle: 'Review 任务状态',
    noJobs: '还没有 review 任务。去 MR 评论里发送触发词试试。',
    jobColumn: 'Job',
    statusColumn: 'Status',
    mrColumn: 'MR',
    findingsColumn: 'Findings',
    updatedAtColumn: '更新时间',
    actionsColumn: '操作',
    viewLogs: '查看日志',
    findingsSummary: (job) => `${job.findings_count} total / ${job.inline_findings_count ?? 0} eligible / ${job.inline_discussion_ids?.length ?? 0} posted`,
    timezoneLabel: '上海',
    statusLabels: {
      queued: '排队中',
      running: '运行中',
      completed: '已完成',
      failed: '失败',
      skipped: '已跳过',
    },
  },
  en: {
    pageTitle: 'Merge Request Reviewer Agent',
    pageDescription:
      'Enter a GitLab project URL and the service will automatically create the MR/Note webhook via the GitLab API. When a merge request comment contains the trigger keyword, the backend clones the code, runs Copilot SDK analysis, and writes both the summary and inline comments back to the MR.',
    uiLanguage: 'UI Language',
    uiLanguageOptions: {
      zh: '🇨🇳 中文',
      en: '🇺🇸 English',
    },
    setupTitle: 'Project Setup',
    projectUrl: 'GitLab Project URL',
    triggerKeyword: 'Trigger Keyword',
    reviewLanguage: 'Review Language',
    reviewLanguagePlaceholder: 'Chinese / English / Japanese',
    setupSubmitting: 'Configuring...',
    setupSubmit: 'Configure Webhook',
    setupSuccess: (result) => `Project ${result.project_path} is configured. Webhook ID: ${result.webhook_id}. Review language: ${result.review_language}.`,
    serviceStatus: 'Service Status',
    sameOriginApi: 'Same origin (/api)',
    loading: 'Loading...',
    defaultTriggerKeyword: 'Default Trigger Keyword',
    defaultLanguage: 'Default Language',
    displayTimezone: 'Display Timezone',
    configuredProjects: 'Configured Projects',
    totalJobs: 'Total Jobs',
    inlineThreshold: 'Inline Threshold',
    copilotTimeout: 'Copilot Timeout',
    timeoutSeconds: (seconds) => `${seconds} seconds`,
    timezoneHint: 'All timestamps are shown in Asia/Shanghai. For running jobs, use the log viewer button on the right to inspect the live Copilot stream.',
    projectsTitle: 'Configured Projects',
    noProjects: 'No projects yet. Add a GitLab project URL above to get started.',
    projectColumn: 'Project',
    webhookColumn: 'Webhook',
    triggerColumn: 'Trigger',
    languageColumn: 'Language',
    jobsTitle: 'Review Jobs',
    noJobs: 'No review jobs yet. Post the trigger keyword in an MR comment to test it.',
    jobColumn: 'Job',
    statusColumn: 'Status',
    mrColumn: 'MR',
    findingsColumn: 'Findings',
    updatedAtColumn: 'Updated At',
    actionsColumn: 'Actions',
    viewLogs: 'View Logs',
    findingsSummary: (job) => `${job.findings_count} total / ${job.inline_findings_count ?? 0} eligible / ${job.inline_discussion_ids?.length ?? 0} posted`,
    timezoneLabel: 'Shanghai',
    statusLabels: {
      queued: 'Queued',
      running: 'Running',
      completed: 'Completed',
      failed: 'Failed',
      skipped: 'Skipped',
    },
  },
}

function resolveInitialUiLanguage() {
  const saved = window.localStorage.getItem(uiLocaleStorageKey)
  if (saved === 'zh' || saved === 'en') return saved
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

function apiUrl(path) {
  return `${apiBase}${path}`
}

function logViewerUrl(jobId, uiLanguage) {
  const langParam = uiLanguage === 'zh' ? 'zh' : 'en'
  return apiUrl(`/api/review-jobs/${jobId}/logs/view?lang=${langParam}`)
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options,
  })
  const text = await response.text()
  const data = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || `Request failed: ${response.status}`)
  }
  return data
}

function formatDate(value, uiLanguage, timezoneLabel) {
  if (!value) return '-'
  const formatter = new Intl.DateTimeFormat(uiLanguage === 'zh' ? 'zh-CN' : 'en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
  return `${formatter.format(new Date(value))} (${timezoneLabel})`
}

function App() {
  const [uiLanguage, setUiLanguage] = useState(resolveInitialUiLanguage)
  const [projectUrl, setProjectUrl] = useState(defaultProjectUrl)
  const [triggerKeyword, setTriggerKeyword] = useState(defaultKeyword)
  const [reviewLanguage, setReviewLanguage] = useState(defaultReviewLanguage)
  const [health, setHealth] = useState(null)
  const [projects, setProjects] = useState([])
  const [jobs, setJobs] = useState([])
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const copy = translations[uiLanguage]

  const webhookEndpoint = useMemo(() => {
    if (!health?.public_base_url) return ''
    return `${health.public_base_url.replace(/\/$/, '')}/api/webhooks/gitlab`
  }, [health])

  async function loadDashboard() {
    const [healthData, projectsData, jobsData] = await Promise.all([
      fetchJson(apiUrl('/api/health')),
      fetchJson(apiUrl('/api/projects')),
      fetchJson(apiUrl('/api/review-jobs')),
    ])
    setHealth(healthData)
    setProjects(Object.values(projectsData.projects ?? {}))
    setJobs(jobsData.jobs ?? [])
    if (!result) {
      setReviewLanguage(healthData.default_review_language ?? defaultReviewLanguage)
    }
  }

  useEffect(() => {
    loadDashboard().catch((loadError) => setError(loadError.message))
    const timer = window.setInterval(() => {
      loadDashboard().catch(() => {})
    }, 5000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    window.localStorage.setItem(uiLocaleStorageKey, uiLanguage)
    document.documentElement.lang = uiLanguage === 'zh' ? 'zh-CN' : 'en'
  }, [uiLanguage])

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setResult(null)
    setIsSubmitting(true)
    try {
      const data = await fetchJson(apiUrl('/api/projects/setup'), {
        method: 'POST',
        body: JSON.stringify({
          project_url: projectUrl,
          trigger_keyword: triggerKeyword,
          review_language: reviewLanguage,
        }),
      })
      setResult(data)
      setReviewLanguage(data.review_language)
      await loadDashboard()
    } catch (submitError) {
      setError(submitError.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="page">
      <div className="panel hero">
        <div className="hero-header">
          <div>
            <p className="eyebrow">GitLab × GitHub Copilot SDK</p>
            <h1>{copy.pageTitle}</h1>
          </div>
          <label className="lang-dropdown" aria-label={copy.uiLanguage}>
            <select value={uiLanguage} onChange={(event) => setUiLanguage(event.target.value)}>
              <option value="zh">{copy.uiLanguageOptions.zh}</option>
              <option value="en">{copy.uiLanguageOptions.en}</option>
            </select>
          </label>
        </div>
        <p className="subtle">{copy.pageDescription}</p>
      </div>

      <div className="grid">
        <form className="panel" onSubmit={handleSubmit}>
          <h2>{copy.setupTitle}</h2>
          <label>
            <span>{copy.projectUrl}</span>
            <input value={projectUrl} onChange={(event) => setProjectUrl(event.target.value)} placeholder="https://gitlab.com/group/project" />
          </label>
          <label>
            <span>{copy.triggerKeyword}</span>
            <input value={triggerKeyword} onChange={(event) => setTriggerKeyword(event.target.value)} placeholder="/copilot-review" />
          </label>
          <label>
            <span>{copy.reviewLanguage}</span>
            <input value={reviewLanguage} onChange={(event) => setReviewLanguage(event.target.value)} placeholder={copy.reviewLanguagePlaceholder} />
          </label>
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? copy.setupSubmitting : copy.setupSubmit}
          </button>
          {error ? <div className="alert error">{error}</div> : null}
          {result ? <div className="alert success">{copy.setupSuccess(result)}</div> : null}
        </form>

        <div className="panel">
          <h2>{copy.serviceStatus}</h2>
          <ul className="meta-list">
            <li><strong>API</strong><span>{apiBase || copy.sameOriginApi}</span></li>
            <li><strong>{copy.webhookColumn}</strong><span>{webhookEndpoint || copy.loading}</span></li>
            <li><strong>{copy.defaultTriggerKeyword}</strong><span>{health?.trigger_keyword ?? copy.loading}</span></li>
            <li><strong>{copy.defaultLanguage}</strong><span>{health?.default_review_language ?? copy.loading}</span></li>
            <li><strong>{copy.displayTimezone}</strong><span>{health?.display_timezone ?? 'Asia/Shanghai'}</span></li>
            <li><strong>{copy.configuredProjects}</strong><span>{health?.projects ?? 0}</span></li>
            <li><strong>{copy.totalJobs}</strong><span>{health?.jobs ?? 0}</span></li>
            <li><strong>{copy.inlineThreshold}</strong><span>{health?.inline_min_severity ?? copy.loading}</span></li>
            <li><strong>{copy.copilotTimeout}</strong><span>{copy.timeoutSeconds(health?.copilot_timeout_seconds ?? 3600)}</span></li>
          </ul>
          <div className="hint">{copy.timezoneHint}</div>
        </div>
      </div>

      <div className="panel table-wrap">
        <h2>{copy.projectsTitle}</h2>
        {projects.length === 0 ? (
          <p className="subtle">{copy.noProjects}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{copy.projectColumn}</th>
                <th>{copy.webhookColumn}</th>
                <th>{copy.triggerColumn}</th>
                <th>{copy.languageColumn}</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((project) => (
                <tr key={project.project_id}>
                  <td>
                    <div>{project.project_path}</div>
                    <a href={project.project_url} target="_blank" rel="noreferrer">{project.project_url}</a>
                  </td>
                  <td>{project.webhook_url}</td>
                  <td><code>{project.trigger_keyword}</code></td>
                  <td>{project.review_language}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel table-wrap">
        <h2>{copy.jobsTitle}</h2>
        {jobs.length === 0 ? (
          <p className="subtle">{copy.noJobs}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{copy.jobColumn}</th>
                <th>{copy.statusColumn}</th>
                <th>{copy.mrColumn}</th>
                <th>{copy.findingsColumn}</th>
                <th>{copy.updatedAtColumn}</th>
                <th>{copy.actionsColumn}</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.job_id}>
                  <td>
                    <div>{job.project_path}</div>
                    <div className="subtle small">{job.job_id}</div>
                    <div className="subtle small">{job.message}</div>
                  </td>
                  <td><span className={`status status-${job.status}`}>{copy.statusLabels[job.status] ?? job.status}</span></td>
                  <td>!{job.mr_iid}</td>
                  <td>{copy.findingsSummary(job)}</td>
                  <td>{formatDate(job.updated_at, uiLanguage, copy.timezoneLabel)}</td>
                  <td>
                    <a className="secondary-btn link-btn" href={logViewerUrl(job.job_id, uiLanguage)} target="_blank" rel="noopener">
                      {copy.viewLogs}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

export default App
