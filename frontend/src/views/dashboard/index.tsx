import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import ForecastChart from '@/components/ForecastChart'

const ENDPOINT = '/api/dashboard'
const columns = [
  "大盘编号",
  "大盘名称",
  "包含指标组",
  "布局模板",
  "刷新频率秒",
  "对比周期",
  "分享范围",
  "负责人"
]
const actions = [
  "提交大盘评审",
  "发布大盘",
  "下线大盘"
]
const statuses = [
  "草稿",
  "大盘评审中",
  "已发布",
  "已下线"
]

type Row = Record<string, string | number | null>
type IntervalPoint = { 日期: string; 预测值: number; 下限: number; 上限: number }
type ForecastOverlay = {
  指标编号: string
  已发布: boolean
  方案编号?: string
  版本号?: number
  季节性判定?: { 启用: boolean; 周期: string | null; 强度: number; 判定依据: string }
  外部因子?: string[]
  预测区间?: IntervalPoint[]
  发布时间?: string
}

export default function DashboardView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const [overlayFor, setOverlayFor] = useState<number | null>(null)
  const [overlays, setOverlays] = useState<ForecastOverlay[]>([])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('大盘配置列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控大盘列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('监控大盘动作未生效，请稍后重试')
      }
      await reload()
      if (overlayFor !== null) await loadOverlay(overlayFor)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控大盘操作失败')
    }
  }

  async function loadOverlay(entryId: number) {
    // 共享链路：大盘详情直接消费趋势预测结果数据集，与方案详情同一版本。
    try {
      const response = await request(`${ENDPOINT}/${entryId}`)
      if (!response.ok) throw new Error('大盘详情读取失败')
      const payload = await response.json()
      setOverlays(payload.预测覆盖 ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '大盘预测覆盖读取失败')
    }
  }

  async function toggleOverlay(entryId: number) {
    if (overlayFor === entryId) {
      setOverlayFor(null)
      setOverlays([])
      return
    }
    setOverlayFor(entryId)
    await loadOverlay(entryId)
  }

  return (
    <section className="page" data-module="dashboard">
      <header className="page-head">
        <div>
          <h2>监控大盘管理</h2>
          <p className="page-desc">配置实时监控大盘，组合指标卡片和图表形成业务全景视图；预测区间来自趋势预测已发布数据集。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('大盘配置登记入口尚未接入审批流')}>
            登记大盘配置
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出监控大盘清单
          </button>
        </div>
      </header>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

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
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void toggleOverlay(Number(row.id))}>
                  {overlayFor === Number(row.id) ? '收起预测' : '预测覆盖'}
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无监控大盘数据，可先登记大盘配置</td>
            </tr>
          )}
        </tbody>
      </table>

      {overlayFor !== null && (
        <div className="detail-panel">
          <h3>大盘预测覆盖（消费趋势预测结果数据集，随预测状态同步更新）</h3>
          {overlays.length === 0 && <p className="muted-text">该大盘的指标组暂无预测数据</p>}
          {overlays.map((overlay) => (
            <div key={overlay.指标编号} className="overlay-block">
              {!overlay.已发布 ? (
                <p className="muted-text">指标 {overlay.指标编号} 暂无已发布的预测版本</p>
              ) : (
                <>
                  <div className="overlay-meta">
                    <span>指标 {overlay.指标编号}</span>
                    <span>方案 {overlay.方案编号}</span>
                    <span>版本 v{overlay.版本号}</span>
                    <span>
                      季节性：{overlay.季节性判定?.启用 ? `启用（${overlay.季节性判定.周期}，强度 ${overlay.季节性判定.强度}）` : '未启用'}
                    </span>
                    <span>外部因子：{overlay.外部因子 && overlay.外部因子.length > 0 ? overlay.外部因子.join('、') : '无'}</span>
                    <span>发布时间 {overlay.发布时间 ?? '—'}</span>
                  </div>
                  <ForecastChart points={overlay.预测区间 ?? []} />
                </>
              )}
            </div>
          ))}
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条监控大盘记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
