/**
 * ==================================================================
 * 智法通前端 | ECharts 图表主题工具
 * ------------------------------------------------------------------
 * 职责：
 *   1. 依据当前主题（亮/暗）返回图表文本/轴线/网格/悬浮层的配色，
 *      保证两套主题下图例、坐标轴、标签均清晰可读（对比度达标）；
 *   2. 提供 makeChartRenderer：封装「销毁旧实例 -> init -> setOption」
 *      的重绘流程，供视图监听主题切换后整体重绘。
 *
 * 为什么不直接 dispose 后重跑渲染函数：
 *   各视图渲染函数依赖已加载的业务数据，重绘时需要复用；
 *   通过闭包持有渲染回调即可在主题变化时零成本重建图表。
 * ==================================================================
 */
import * as echarts from 'echarts'

/** 图表语义配色（按主题返回） */
export interface ChartPalette {
  /** 图例文字色 */
  legendText: string
  /** 饼图/玫瑰图扇区标签色 */
  labelColor: string
  /** 饼图扇区描边色（与卡片底色一致，形成分隔） */
  sliceBorder: string
  /** 坐标轴主文字色 */
  axisLabel: string
  /** 坐标轴次要文字色（大密度标签） */
  axisLabelDim: string
  /** 坐标轴线色 */
  axisLine: string
  /** 网格分割线色 */
  splitLine: string
  /** 力导向图节点标签色 */
  graphLabel: string
  /** 力导向图连线色 */
  graphLine: string
  /** 关键词云次要词色 */
  keywordDim: string
}

/** 亮色主题图表配色（深灰蓝文字，白底上对比度 >= 4.5:1） */
const LIGHT_PALETTE: ChartPalette = {
  legendText: '#334155',
  labelColor: '#334155',
  sliceBorder: '#ffffff',
  axisLabel: '#475569',
  axisLabelDim: '#64748b',
  axisLine: '#cbd5e1',
  splitLine: 'rgba(15, 23, 42, 0.08)',
  graphLabel: '#334155',
  graphLine: 'rgba(37, 99, 235, 0.35)',
  keywordDim: '#64748b',
}

/** 暗色主题图表配色（沿用原深色法律科技风） */
const DARK_PALETTE: ChartPalette = {
  legendText: '#94a3b8',
  labelColor: '#cbd5e1',
  sliceBorder: '#0b1220',
  axisLabel: '#64748b',
  axisLabelDim: '#94a3b8',
  axisLine: '#334155',
  splitLine: 'rgba(148, 163, 184, 0.1)',
  graphLabel: '#cbd5e1',
  graphLine: 'rgba(96, 165, 250, 0.35)',
  keywordDim: '#94a3b8',
}

/**
 * 读取当前 DOM 主题并返回对应图表配色。
 * 直接探测 <html> 的 dark 类：与 store 无耦合，可在任意时机调用。
 */
export function getChartPalette(): ChartPalette {
  return document.documentElement.classList.contains('dark')
    ? DARK_PALETTE
    : LIGHT_PALETTE
}

/**
 * 图表重绘器工厂。
 *
 * @param mount   图表挂载的 DOM ref getter（可能尚未渲染，需判空）
 * @param render  (el, chart) => void 业务渲染逻辑（setOption）
 * @returns rerender() 任何时候调用都会以当前主题重建图表；
 *          el 缺失或无数据时安全跳过。
 */
export function makeChartRenderer(
  mount: () => HTMLElement | undefined,
  render: (el: HTMLElement, chart: echarts.ECharts) => void,
): () => echarts.ECharts | undefined {
  return () => {
    const el = mount()
    if (!el) return undefined
    // 主题切换重绘时销毁旧实例，防止 canvas 叠加与内存泄漏
    const old = echarts.getInstanceByDom(el)
    if (old) old.dispose()
    const chart = echarts.init(el)
    render(el, chart)
    return chart
  }
}
