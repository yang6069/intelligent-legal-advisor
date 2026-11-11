<!--
  ==================================================================
  智法通前端 | 数据分析页（legal_admin / admin 可见）
  ------------------------------------------------------------------
  数据源：
    GET /analytics/hot-topics   咨询热点（领域饼图/日趋势/关键词）
    GET /analytics/citations    法条引用（Top20 柱图/共引关系图）
    GET /analytics/clusters     用户聚类（K-Means 画像/分群明细）
  主题适配：
    全部 ECharts 图表配色取自当前主题，切换主题后自动重绘；
    文字级点缀色按主题切换深/浅变体，保证两套主题下对比度达标。
  ==================================================================
-->
<template>
  <div v-loading="loading" class="ana-page">
    <!-- 顶部操作条（用户聚类为管理员专属系统功能，其他角色不可见） -->
    <div class="ana-header">
      <el-radio-group v-model="tab">
        <el-radio-button value="hot">咨询热点</el-radio-button>
        <el-radio-button value="cite">法条引用</el-radio-button>
      </el-radio-group>
      <el-select v-if="tab === 'hot'" v-model="days" style="width: 130px" @change="loadHot">
        <el-option v-for="d in dict.analyticsDays" :key="d" :value="d" :label="`近 ${d} 天`" />
      </el-select>
    </div>

    <!-- ================= 咨询热点 ================= -->
    <template v-if="tab === 'hot'">
      <div class="stat-line">
        统计周期内共 <b>{{ hot.total_queries }}</b> 次法律咨询
      </div>
      <div class="grid-2">
        <div class="panel">
          <h3 class="p-title">业务领域分布</h3>
          <div ref="domainChartRef" class="chart-box" />
        </div>
        <div class="panel">
          <h3 class="p-title">日咨询量趋势</h3>
          <div ref="trendChartRef" class="chart-box" />
        </div>
      </div>
      <div class="panel">
        <h3 class="p-title">热点关键词</h3>
        <div class="kw-cloud">
          <span
            v-for="(k, i) in hot.top_keywords || []"
            :key="k.keyword"
            class="kw"
            :style="kwStyle(i)"
          >{{ k.keyword }} <em>{{ k.count }}</em></span>
          <el-empty v-if="!(hot.top_keywords || []).length" description="暂无数据" :image-size="60" />
        </div>
      </div>
    </template>

    <!-- ================= 法条引用 ================= -->
    <template v-else-if="tab === 'cite'">
      <div class="stat-line">
        共 <b>{{ cite.total_answers_with_citations }}</b> 条回答携带法条引用
        <span class="cite-source">
          数据来源：智能问答回答中的真实法条引用记录；法条内容可在「知识库管理」中维护，新引用自动纳入统计
        </span>
      </div>
      <div class="grid-2">
        <div class="panel">
          <h3 class="p-title">高频引用法条 Top20</h3>
          <div ref="citeBarRef" class="chart-box" />
        </div>
        <div class="panel">
          <h3 class="p-title">法条共引关系网络</h3>
          <p class="p-sub">同一回答共同引用的两条法条连成一条边；节点 = 法条/文档，边越粗共引越多</p>
          <div ref="cociteChartRef" class="chart-box" />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 数据分析页逻辑：
 *   - tab 切换懒加载三组分析数据；
 *   - ECharts 图表按需初始化（切换/到达时渲染），
 *     主题切换时对当前已挂载的图表按新配色整体重绘；
 *   - palette 为计算属性：暗色用亮色系点缀、亮色用加深点缀，
 *     保证关键词云/聚类卡片文字在两套主题下均清晰可读。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { get } from '../api/client'
import { useThemeStore } from '../stores/theme'
import { useDictStore } from '../stores/dict'
import { useAutoRefresh } from '../composables/useAutoRefresh'
import { getChartPalette, makeChartRenderer } from '../utils/chartTheme'

const theme = useThemeStore()
const dict = useDictStore()
const loading = ref(false)
const tab = ref<'hot' | 'cite'>('hot')
const days = ref(30)

const hot = reactive<any>({ total_queries: 0, domain_distribution: {}, daily_trend: [], top_keywords: [] })
const cite = reactive<any>({ total_answers_with_citations: 0, top_cited_articles: [], co_citation_network: [] })

const domainChartRef = ref<HTMLElement>()
const trendChartRef = ref<HTMLElement>()
const citeBarRef = ref<HTMLElement>()
const cociteChartRef = ref<HTMLElement>()

/** 点缀色双主题体系：暗色用亮色系 / 亮色用加深系（文字级对比度保障） */
const paletteDark = ['#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#a78bfa', '#38bdf8']
const paletteLight = ['#2563eb', '#047857', '#b45309', '#be185d', '#6d28d9', '#0e7490']
const palette = computed(() => (theme.isDark ? paletteDark : paletteLight))

/** 图表实例表（resize / 卸载销毁统一管理） */
const instances = new Map<string, echarts.ECharts>()

/** 领域分布饼图渲染 */
function renderDomainOption(_el: HTMLElement, chart: echarts.ECharts) {
  const p = getChartPalette()
  const entries = Object.entries(hot.domain_distribution || {})
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie', radius: ['40%', '70%'], center: ['50%', '46%'],
      itemStyle: { borderRadius: 8, borderColor: p.sliceBorder, borderWidth: 3 },
      label: { color: p.labelColor },
      data: entries.map(([name, value], i) => ({
        name, value, itemStyle: { color: palette.value[i % palette.value.length] },
      })),
    }],
  })
}

/** 日趋势面积图渲染 */
function renderTrendOption(_el: HTMLElement, chart: echarts.ECharts) {
  const p = getChartPalette()
  const trend = hot.daily_trend || []
  const lineColor = theme.isDark ? '#60a5fa' : '#2563eb'
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 40, right: 20, top: 20, bottom: 30 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category', data: trend.map((d: any) => d.date),
      axisLabel: { color: p.axisLabel, rotate: 40 },
      axisLine: { lineStyle: { color: p.axisLine } },
    },
    yAxis: {
      type: 'value', axisLabel: { color: p.axisLabel },
      splitLine: { lineStyle: { color: p.splitLine } },
    },
    series: [{
      type: 'line', smooth: true, symbol: 'circle', symbolSize: 6,
      data: trend.map((d: any) => d.count),
      lineStyle: { color: lineColor, width: 2.5 },
      itemStyle: { color: lineColor },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: theme.isDark ? 'rgba(96,165,250,0.4)' : 'rgba(37,99,235,0.25)' },
          { offset: 1, color: 'rgba(96,165,250,0.02)' },
        ]),
      },
    }],
  })
}

/** Top20 引用法条横向柱图渲染 */
function renderCiteBarOption(_el: HTMLElement, chart: echarts.ECharts) {
  const p = getChartPalette()
  const items = (cite.top_cited_articles || []).slice(0, 20).reverse()
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 150, right: 30, top: 10, bottom: 30 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'value', axisLabel: { color: p.axisLabel },
      splitLine: { lineStyle: { color: p.splitLine } },
    },
    yAxis: {
      type: 'category',
      data: items.map((x: any) => x.article),
      axisLabel: { color: p.axisLabelDim, fontSize: 11 },
      axisLine: { lineStyle: { color: p.axisLine } },
    },
    series: [{
      type: 'bar', barWidth: 12,
      data: items.map((x: any) => x.count),
      itemStyle: {
        borderRadius: [0, 6, 6, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: theme.isDark ? '#3b82f6' : '#2563eb' },
          { offset: 1, color: theme.isDark ? '#a78bfa' : '#7c3aed' },
        ]),
      },
    }],
  })
}

/** 共引关系图（力导向 graph）渲染 */
function renderCoCiteOption(_el: HTMLElement, chart: echarts.ECharts) {
  const p = getChartPalette()
  // 构建 nodes/links（去重节点集合）
  const nodeSet = new Set<string>()
  const links = (cite.co_citation_network || []).slice(0, 40)
  links.forEach((l: any) => { nodeSet.add(l.source); nodeSet.add(l.target) })
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { show: false },
    series: [{
      type: 'graph', layout: 'force',
      roam: true, draggable: true,
      force: { repulsion: 220, edgeLength: [60, 140] },
      label: { show: true, color: p.graphLabel, fontSize: 10 },
      lineStyle: { color: p.graphLine, curveness: 0.15 },
      emphasis: { focus: 'adjacency' },
      data: [...nodeSet].map((name, i) => ({
        name, symbolSize: 26 + (i % 4) * 6,
        itemStyle: { color: palette.value[i % palette.value.length] },
      })),
      links: links.map((l: any) => ({
        source: l.source, target: l.target,
        lineStyle: { width: Math.min(1 + l.count * 0.4, 5) },
      })),
    }],
  })
}

// 可重复调用的重绘器（内部自动 dispose 旧实例）
const rerenderDomain = makeChartRenderer(() => domainChartRef.value, renderDomainOption)
const rerenderTrend = makeChartRenderer(() => trendChartRef.value, renderTrendOption)
const rerenderCiteBar = makeChartRenderer(() => citeBarRef.value, renderCiteBarOption)
const rerenderCoCite = makeChartRenderer(() => cociteChartRef.value, renderCoCiteOption)

/** 重绘当前已挂载的全部图表（主题切换时调用；未挂载的容器安全跳过） */
function renderAllAvailable() {
  const jobs: Array<[string, () => echarts.ECharts | undefined]> = [
    ['domain', rerenderDomain], ['trend', rerenderTrend],
    ['citeBar', rerenderCiteBar], ['cocite', rerenderCoCite],
  ]
  for (const [key, rerender] of jobs) {
    const inst = rerender()
    if (inst) instances.set(key, inst)
  }
}

onMounted(() => {
  dict.ensure()   // 时间范围/特征标签字典后端下发
  loadHot()
})

/**
 * 分析数据自动刷新（数据变化自动上屏，无需手动刷新浏览器）：
 * 定时 + 窗口聚焦时按当前 tab 静默拉取；数据未变化时跳过图表重绘
 * （避免 canvas 闪烁），有新咨询/新审查数据时自动更新。
 */
useAutoRefresh(async () => {
  if (loading.value) return
  if (tab.value === 'hot' && hot.loaded) await refreshIfChanged('hot')
  if (tab.value === 'cite' && cite.loaded) await refreshIfChanged('cite')
}, 60000)

/** 拉取并比对数据快照，变化时才重载（图表免闪烁） */
const snapshots: Record<string, string> = {}
async function refreshIfChanged(kind: 'hot' | 'cite') {
  const target = kind === 'hot' ? hot : cite
  const url = kind === 'hot' ? '/analytics/hot-topics' : '/analytics/citations'
  const params = kind === 'hot' ? { days: days.value } : undefined
  const fresh = await get<any>(url, params)
  const snap = JSON.stringify(fresh)
  if (snapshots[kind] && snapshots[kind] === snap) return   // 数据未变，跳过重绘
  snapshots[kind] = snap
  Object.assign(target, fresh, { loaded: true })
  await nextTick()
  renderAllAvailable()
}
onBeforeUnmount(() => instances.forEach((c) => c.dispose()))

/** 主题切换：重绘当前 tab 下已渲染的图表 */
watch(() => theme.mode, () => {
  renderAllAvailable()
})

/** tab 切换：首次懒加载对应数据；已加载过的 tab 切回时
 *  必须重绘图表 —— v-if 切换会销毁图表容器 DOM，
 *  旧 ECharts 实例随之失效，不重绘就会出现"切几下图消失"的现象 */
watch(tab, async (t) => {
  if (t === 'hot') {
    if (!hot.loaded) {
      await loadHot()
    } else {
      await nextTick()          // 等待 v-if 重建图表容器
      renderAllAvailable()
    }
  } else if (t === 'cite') {
    if (!cite.loaded) {
      await loadCite()
    } else {
      await nextTick()
      renderAllAvailable()
    }
  }
})

/** 咨询热点（领域饼图 + 日趋势折线 + 关键词） */
async function loadHot() {
  loading.value = true
  try {
    const d = await get<any>('/analytics/hot-topics', { days: days.value })
    Object.assign(hot, d, { loaded: true })
    snapshots.hot = JSON.stringify(d)   // 记录快照（自动刷新变更检测）
    await nextTick()
    renderAllAvailable()
  } finally { loading.value = false }
}

/** 法条引用统计（Top20 柱图 + 共引关系图） */
async function loadCite() {
  loading.value = true
  try {
    const d = await get<any>('/analytics/citations')
    Object.assign(cite, d, { loaded: true })
    snapshots.cite = JSON.stringify(d)
    await nextTick()
    renderAllAvailable()
  } finally { loading.value = false }
}

/** 关键词云样式（按热度递变字号/颜色；颜色取自当前主题点缀色） */
function kwStyle(i: number) {
  const t = Math.max(0, 1 - i / 20)
  return {
    fontSize: `${13 + t * 15}px`,
    color: palette.value[i % palette.value.length],
    opacity: 0.75 + t * 0.25,
  }
}
</script>

<style scoped>
.ana-page { display: flex; flex-direction: column; gap: 16px; }
.ana-header { display: flex; justify-content: space-between; align-items: center; }
.stat-line { color: var(--zft-text-dim); font-size: 13.5px; }
.stat-line b { color: var(--zft-primary-text); font-size: 16px; }

.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.panel {
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  border-radius: 14px;
  padding: 18px;
  box-shadow: var(--zft-shadow-card);
}
.p-title { color: var(--zft-text); font-size: 15px; margin: 0 0 12px; }
.chart-box { height: 300px; }
/* 数据来源说明（灰色小字） */
.cite-source { margin-left: 12px; font-size: 12px; color: var(--zft-text-faint); }
.p-sub { margin: -6px 0 10px; font-size: 12px; color: var(--zft-text-faint); }

/* 关键词云 */
.kw-cloud { display: flex; flex-wrap: wrap; gap: 14px 18px; padding: 10px 4px; }
.kw {
  font-weight: 600;
  cursor: default;
  transition: transform 0.2s;
}
.kw:hover { transform: scale(1.1); }
.kw em { font-style: normal; font-size: 11px; opacity: 0.75; margin-left: 3px; }

@media (max-width: 1000px) { .grid-2 { grid-template-columns: 1fr; } }
</style>
