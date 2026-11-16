/** 预测区间图：预测值折线 + 置信区间带，趋势预测方案详情与监控大盘共用。 */

type IntervalPoint = {
  日期: string
  预测值: number
  下限: number
  上限: number
}

const WIDTH = 660
const HEIGHT = 180
const PAD = { left: 48, right: 12, top: 12, bottom: 24 }

export default function ForecastChart({ points }: { points: IntervalPoint[] }) {
  if (points.length === 0) {
    return <p className="muted-text">暂无预测区间数据</p>
  }
  const lows = points.map((point) => point.下限)
  const highs = points.map((point) => point.上限)
  const min = Math.min(...lows)
  const max = Math.max(...highs)
  const span = max - min || 1
  const innerW = WIDTH - PAD.left - PAD.right
  const innerH = HEIGHT - PAD.top - PAD.bottom
  const x = (index: number) => PAD.left + (points.length === 1 ? innerW / 2 : (index / (points.length - 1)) * innerW)
  const y = (value: number) => PAD.top + innerH - ((value - min) / span) * innerH

  const linePath = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.预测值).toFixed(1)}`).join(' ')
  const bandPath = [
    ...points.map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.上限).toFixed(1)}`),
    ...[...points].reverse().map((point, index) => `L${x(points.length - 1 - index).toFixed(1)},${y(point.下限).toFixed(1)}`),
    'Z',
  ].join(' ')

  return (
    <svg width={WIDTH} height={HEIGHT} role="img" aria-label="预测区间图">
      <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={HEIGHT - PAD.bottom} stroke="#d8dee6" />
      <line x1={PAD.left} y1={HEIGHT - PAD.bottom} x2={WIDTH - PAD.right} y2={HEIGHT - PAD.bottom} stroke="#d8dee6" />
      <path d={bandPath} fill="#1f6feb" opacity={0.12} />
      <path d={linePath} fill="none" stroke="#1f6feb" strokeWidth={2} />
      <text x={4} y={y(max) + 4} fontSize={11} fill="#64748b">{max.toFixed(1)}</text>
      <text x={4} y={y(min) + 4} fontSize={11} fill="#64748b">{min.toFixed(1)}</text>
      <text x={PAD.left} y={HEIGHT - 6} fontSize={11} fill="#64748b">{points[0].日期}</text>
      <text x={WIDTH - PAD.right} y={HEIGHT - 6} fontSize={11} fill="#64748b" textAnchor="end">{points[points.length - 1].日期}</text>
    </svg>
  )
}
