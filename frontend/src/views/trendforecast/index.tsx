import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import ForecastChart from '@/components/ForecastChart'

const ENDPOINT = '/api/trendforecast'

const FIELD_DEFS = [
  { name: '方案编号', required: true },
  { name: '预测指标', required: true },
  { name: '算法模型', required: true },
  { name: '历史窗口天', required: false },
  { name: '预测窗口天', required: false },
  { name: '置信区间', required: false },
  { name: '季节性开关', required: false },
  { name: '外部因子', required: false },
]
const columns = [...FIELD_DEFS.map((field) => field.name), '状态', '已发布版本', '候选版本']
const STATUSES = ['草稿', '模型评估中', '已部署', '已下线']
const STAGE_NAMES = ['构建', '环境准备', '数据库迁移', '历史数据清洗', '趋势预测计算', '结果数据集生成']
// 状态只能依次评估、部署、下线：每一行只放出当前状态允许的动作。
const STATUS_ACTIONS: Record<string, string[]> = {
  草稿: ['提交模型评估'],
  模型评估中: ['重新训练', '部署方案'],
  已部署: ['重新训练', '部署方案', '下线方案'],
  已下线: [],
}
const EDITABLE_STATUSES = new Set(['草稿', '模型评估中'])

type Row = Record<string, string | number | null>
type IntervalPoint = { 日期: string; 预测值: number; 下限: number; 上限: number }
type Run = {
  id: number
  版本号: number
  状态: string
  已完成阶段: string[]
  失败原因: string
  创建时间: string
  更新时间: string
}
type Dataset = {
  id: number
  版本号: number
  状态: string
  生成时间: string
  参数快照: Record<string, string>
  季节性判定: { 启用: boolean; 周期: string | null; 强度: number; 判定依据: string }
  外部因子: string[]
  预测区间: IntervalPoint[]
  清洗报告: Record<string, string | number> | null
}
type FormState = { id: number | null; values: Record<string, string> }

const emptyForm: FormState = { id: null, values: {} }

function statusBadge(status: string) {
  if (status === '已部署') return 'badge ok'
  if (status === '模型评估中') return 'badge warn'
  if (status === '已下线') return 'badge err'
  return 'badge muted'
}

export default function TrendforecastView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [stats, setStats] = useState({ total: 0, deployed: 0, evaluating: 0 })
  const [form, setForm] = useState<FormState | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [interruptStage, setInterruptStage] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (statusFilter) query.set('status', statusFilter)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('预测方案列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '趋势预测列表读取失败')
    }
    try {
      const statsResponse = await request(`${ENDPOINT}?size=200`)
      if (statsResponse.ok) {
        const payload = await statsResponse.json()
        const all = (payload.items ?? []) as Row[]
        setStats({
          total: payload.total ?? all.length,
          deployed: all.filter((row) => row.status === '已部署').length,
          evaluating: all.filter((row) => row.status === '模型评估中').length,
        })
      }
    } catch {
      /* 统计卡片失败不阻塞列表 */
    }
  }, [keyword, statusFilter])

  const reloadDetail = useCallback(async (entryId: number) => {
    try {
      const [runsResponse, datasetsResponse] = await Promise.all([
        request(`${ENDPOINT}/${entryId}/runs`),
        request(`${ENDPOINT}/${entryId}/datasets`),
      ])
      if (runsResponse.ok) {
        const payload = await runsResponse.json()
        setRuns(payload.items ?? [])
      }
      if (datasetsResponse.ok) {
        const payload = await datasetsResponse.json()
        setDatasets(payload.items ?? [])
      }
    } catch {
      /* 详情加载失败时保留列表 */
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (detailId !== null) void reloadDetail(detailId)
  }, [detailId, reloadDetail])

  async function runAction(action: string, row: Row, extra: Record<string, string> = {}) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action, ...extra }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '趋势预测动作未生效')
      } else {
        setNotice(payload.message ?? '动作已执行')
      }
      await reload()
      if (detailId !== null) await reloadDetail(detailId)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '趋势预测操作失败')
    }
  }

  async function saveForm() {
    if (!form) return
    setErrorMessage('')
    const isEdit = form.id !== null
    const url = isEdit ? `${ENDPOINT}/${form.id}` : ENDPOINT
    try {
      const response = await request(url, {
        method: isEdit ? 'PUT' : 'POST',
        body: JSON.stringify({ values: form.values }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '预测方案保存失败')
        return
      }
      setNotice(payload.message ?? '预测方案已保存')
      setForm(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '预测方案保存失败')
    }
  }

  async function resumeRun(row: Row, run: Run) {
    setErrorMessage('')
    const response = await request(`${ENDPOINT}/${row.id}/runs/${run.id}/resume`, { method: 'POST', body: '{}' })
    const payload = await response.json()
    if (!payload.ok) setErrorMessage(payload.message ?? '续做失败')
    else setNotice(payload.message ?? '续做完成')
    await reload()
    if (detailId !== null) await reloadDetail(detailId)
  }

  async function cancelRun(row: Row, run: Run) {
    setErrorMessage('')
    const response = await request(`${ENDPOINT}/${row.id}/runs/${run.id}/cancel`, { method: 'POST', body: '{}' })
    const payload = await response.json()
    if (!payload.ok) setErrorMessage(payload.message ?? '取消失败')
    else setNotice(payload.message ?? '任务已取消')
    if (detailId !== null) await reloadDetail(detailId)
  }

  const detailRow = useMemo(() => rows.find((row) => row.id === detailId) ?? null, [rows, detailId])
  const trainAction = detailRow && detailRow.status === '草稿' ? '提交模型评估' : '重新训练'

  return (
    <section className="page" data-module="trendforecast">
      <header className="page-head">
        <div>
          <h2>趋势预测管理</h2>
          <p className="page-desc">管理指标趋势的预测方案，状态依次评估、部署、下线；结果数据集版本化，供监控大盘共享消费。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setForm({ ...emptyForm })}>
            登记预测方案
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出趋势预测清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">预测方案总数</span><strong className="stat-value">{stats.total}</strong></article>
        <article className="stat-card"><span className="stat-label">已部署方案</span><strong className="stat-value">{stats.deployed}</strong></article>
        <article className="stat-card"><span className="stat-label">评估中方案</span><strong className="stat-value">{stats.evaluating}</strong></article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>方案编号</span>
          <input value={keyword} placeholder="按方案编号检索" onChange={(event) => setKeyword(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部状态</option>
            {STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setKeyword(''); setStatusFilter('') }}>重置条件</button>
      </form>

      {form && (
        <div className="form-panel">
          <h3>{form.id === null ? '登记预测方案' : `编辑预测方案 #${form.id}`}</h3>
          <div className="form-grid">
            {FIELD_DEFS.map((field) => (
              <label key={field.name} className="form-item">
                <span>{field.name}{field.required ? '（必填）' : ''}</span>
                {field.name === '季节性开关' ? (
                  <select
                    value={form.values[field.name] ?? '关'}
                    onChange={(event) => setForm((prev) => prev && { ...prev, values: { ...prev.values, [field.name]: event.target.value } })}
                  >
                    <option value="开">开</option>
                    <option value="关">关</option>
                  </select>
                ) : field.name === '置信区间' ? (
                  <select
                    value={form.values[field.name] ?? '95%'}
                    onChange={(event) => setForm((prev) => prev && { ...prev, values: { ...prev.values, [field.name]: event.target.value } })}
                  >
                    {['80%', '90%', '95%', '99%'].map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                ) : (
                  <input
                    value={form.values[field.name] ?? ''}
                    placeholder={field.name === '外部因子' ? '多个因子用逗号分隔' : `请输入${field.name}`}
                    onChange={(event) => setForm((prev) => prev && { ...prev, values: { ...prev.values, [field.name]: event.target.value } })}
                  />
                )}
              </label>
            ))}
          </div>
          <div className="form-actions">
            <button className="btn primary" type="button" onClick={() => void saveForm()}>保存方案</button>
            <button className="btn ghost" type="button" onClick={() => setForm(null)}>取消</button>
          </div>
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => {
                if (column === '状态') return <td key={column}><span className={statusBadge(String(row.status ?? ''))}>{String(row.status ?? '—')}</span></td>
                if (column === '已发布版本') return <td key={column}>{row.已发布版本 ? `v${row.已发布版本}` : '—'}</td>
                if (column === '候选版本') return <td key={column}>{row.候选版本 ? `v${row.候选版本}` : '—'}</td>
                return <td key={column}>{row[column] ?? '—'}</td>
              })}
              <td className="row-actions">
                {(STATUS_ACTIONS[String(row.status)] ?? []).map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                {EDITABLE_STATUSES.has(String(row.status)) && (
                  <button
                    className="link"
                    type="button"
                    onClick={() => setForm({ id: Number(row.id), values: Object.fromEntries(FIELD_DEFS.map((field) => [field.name, String(row[field.name] ?? '')])) })}
                  >
                    编辑
                  </button>
                )}
                <button className="link" type="button" onClick={() => setDetailId(detailId === Number(row.id) ? null : Number(row.id))}>
                  {detailId === Number(row.id) ? '收起' : '详情'}
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无趋势预测数据，可先登记预测方案</td>
            </tr>
          )}
        </tbody>
      </table>

      {detailRow && (
        <div className="detail-panel">
          <h3>方案 {String(detailRow.方案编号)} 的运行与版本</h3>
          <div className="form-actions">
            <label className="form-item">
              <span>中断演练（可选）：让训练在指定阶段中断，验证失败续做</span>
              <select value={interruptStage} onChange={(event) => setInterruptStage(event.target.value)}>
                <option value="">不注入中断</option>
                {STAGE_NAMES.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
              </select>
            </label>
            <button
              className="btn"
              type="button"
              onClick={() => void runAction(trainAction, detailRow, interruptStage ? { 模拟中断阶段: interruptStage } : {})}
            >
              {trainAction}
            </button>
          </div>

          <h4>预测任务（按完整阶段推进，中断从最后一个完整阶段续做）</h4>
          <table className="data-table">
            <thead>
              <tr><th>运行</th><th>版本</th><th>阶段进度</th><th>状态</th><th>失败原因</th><th>更新时间</th><th>操作</th></tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>#{run.id}</td>
                  <td>v{run.版本号}</td>
                  <td>
                    <span className="stage-flow">
                      {STAGE_NAMES.map((stage) => {
                        const done = run.已完成阶段.includes(stage)
                        const failedHere = run.状态 === '失败' && run.已完成阶段.length === STAGE_NAMES.indexOf(stage)
                        return <span key={stage} className={`stage-chip${done ? ' done' : ''}${failedHere ? ' fail' : ''}`}>{stage}</span>
                      })}
                    </span>
                  </td>
                  <td>{run.状态}</td>
                  <td>{run.失败原因 || '—'}</td>
                  <td>{run.更新时间}</td>
                  <td className="row-actions">
                    {run.状态 === '失败' && (
                      <>
                        <button className="link" type="button" onClick={() => void resumeRun(detailRow, run)}>续做</button>
                        <button className="link" type="button" onClick={() => void cancelRun(detailRow, run)}>取消</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {runs.length === 0 && <tr><td className="empty-state" colSpan={7}>暂无预测任务，可先{trainAction}</td></tr>}
            </tbody>
          </table>

          <h4>结果数据集版本（快照可核对，重训不覆盖已发布）</h4>
          <table className="data-table">
            <thead>
              <tr><th>版本</th><th>状态</th><th>参数快照</th><th>季节性判定</th><th>外部因子</th><th>预测区间</th><th>生成时间</th></tr>
            </thead>
            <tbody>
              {datasets.map((dataset) => (
                <tr key={dataset.id}>
                  <td>v{dataset.版本号}</td>
                  <td><span className={dataset.状态 === '已发布' ? 'badge ok' : dataset.状态 === '候选' ? 'badge warn' : 'badge muted'}>{dataset.状态}</span></td>
                  <td>
                    历史{dataset.参数快照?.历史窗口天 ?? '—'}天 / 预测{dataset.参数快照?.预测窗口天 ?? '—'}天 / {dataset.参数快照?.置信区间 ?? '—'}
                  </td>
                  <td>{dataset.季节性判定?.启用 ? `启用（${dataset.季节性判定.周期}，强度 ${dataset.季节性判定.强度}）` : '未启用'}</td>
                  <td>{dataset.外部因子.length > 0 ? dataset.外部因子.join('、') : '—'}</td>
                  <td>
                    {dataset.预测区间.length > 0 ? `${dataset.预测区间[0].日期} ~ ${dataset.预测区间[dataset.预测区间.length - 1].日期}（${dataset.预测区间.length} 点）` : '—'}
                    <details>
                      <summary className="muted-text">查看曲线与清洗报告</summary>
                      <ForecastChart points={dataset.预测区间} />
                      {dataset.清洗报告 && (
                        <p className="muted-text">
                          清洗：窗口 {String(dataset.清洗报告.窗口起)} ~ {String(dataset.清洗报告.窗口止)}，原始 {String(dataset.清洗报告.原始点数)} 点，
                          剔除缺失 {String(dataset.清洗报告.缺失剔除)}、离群 {String(dataset.清洗报告.离群剔除)}，有效 {String(dataset.清洗报告.有效点数)} 点
                        </p>
                      )}
                    </details>
                  </td>
                  <td>{dataset.生成时间}</td>
                </tr>
              ))}
              {datasets.length === 0 && <tr><td className="empty-state" colSpan={7}>暂无结果数据集，训练完成后生成候选版本</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条趋势预测记录</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
