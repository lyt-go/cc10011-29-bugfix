import { useEffect, useState } from 'react'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

type ForecastSync = {
  已发布预测数: number
  覆盖指标数: number
  覆盖大盘数: number
  items: {
    方案编号: string
    预测指标: string
    已发布版本: number
    预测区间起: string | null
    预测区间止: string | null
    季节性启用: boolean
    覆盖大盘: string[]
  }[]
}

const fallbackCards = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
const fallbackModules = [{"name": "实时指标", "created": 0, "pending": 0, "abnormal": 0}, {"name": "监控大盘", "created": 0, "pending": 0, "abnormal": 0}, {"name": "多维下钻", "created": 0, "pending": 0, "abnormal": 0}, {"name": "阈值管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "异常检测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据服务", "created": 0, "pending": 0, "abnormal": 0}, {"name": "告警历史", "created": 0, "pending": 0, "abnormal": 0}, {"name": "同比环比", "created": 0, "pending": 0, "abnormal": 0}, {"name": "聚合配置", "created": 0, "pending": 0, "abnormal": 0}, {"name": "筛选预设", "created": 0, "pending": 0, "abnormal": 0}, {"name": "用户洞察", "created": 0, "pending": 0, "abnormal": 0}, {"name": "事件分析", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分群画像", "created": 0, "pending": 0, "abnormal": 0}, {"name": "趋势预测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "可视化模板", "created": 0, "pending": 0, "abnormal": 0}, {"name": "订阅推送", "created": 0, "pending": 0, "abnormal": 0}, {"name": "维度映射", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据质量分", "created": 0, "pending": 0, "abnormal": 0}]

export default function Overview() {
  const [cards, setCards] = useState<Overview['cards']>(fallbackCards)
  const [moduleRows, setModuleRows] = useState<Overview['modules']>(fallbackModules)
  const [forecastSync, setForecastSync] = useState<ForecastSync | null>(null)

  useEffect(() => {
    fetchJson<Overview>('/api/overview')
      .then((payload) => {
        setCards(payload.cards)
        setModuleRows(payload.modules)
      })
      .catch(() => undefined)
    // 别的页面同步消费趋势预测结果数据集：大盘区间随预测状态更新。
    fetchJson<ForecastSync>('/api/trendforecast/sync-summary')
      .then((payload) => setForecastSync(payload))
      .catch(() => undefined)
  }, [])

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
      </header>
      <div className="stat-row">
        {cards.map((card) => (
          <article key={card.label} className="stat-card">
            <span className="stat-label">{card.label}</span>
            <strong className="stat-value">{card.value}</strong>
          </article>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          {moduleRows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.created}</td>
              <td>{row.pending}</td>
              <td>{row.abnormal}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <section className="detail-panel">
        <h3>趋势预测发布同步（与监控大盘消费同一份结果数据集）</h3>
        {forecastSync && (
          <>
            <div className="stat-row">
              <article className="stat-card"><span className="stat-label">已发布预测</span><strong className="stat-value">{forecastSync.已发布预测数}</strong></article>
              <article className="stat-card"><span className="stat-label">覆盖指标</span><strong className="stat-value">{forecastSync.覆盖指标数}</strong></article>
              <article className="stat-card"><span className="stat-label">覆盖大盘</span><strong className="stat-value">{forecastSync.覆盖大盘数}</strong></article>
            </div>
            <table className="data-table">
              <thead>
                <tr><th>方案编号</th><th>预测指标</th><th>已发布版本</th><th>预测区间</th><th>季节性</th><th>覆盖大盘</th></tr>
              </thead>
              <tbody>
                {forecastSync.items.map((item) => (
                  <tr key={item.方案编号}>
                    <td>{item.方案编号}</td>
                    <td>{item.预测指标}</td>
                    <td>v{item.已发布版本}</td>
                    <td>{item.预测区间起 && item.预测区间止 ? `${item.预测区间起} ~ ${item.预测区间止}` : '—'}</td>
                    <td>{item.季节性启用 ? '启用' : '未启用'}</td>
                    <td>{item.覆盖大盘.length > 0 ? item.覆盖大盘.join('、') : '—'}</td>
                  </tr>
                ))}
                {forecastSync.items.length === 0 && (
                  <tr><td className="empty-state" colSpan={6}>暂无已发布的趋势预测，部署方案后此处与监控大盘同步更新</td></tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </section>
    </section>
  )
}
