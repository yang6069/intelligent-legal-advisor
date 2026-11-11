<!--
  ==================================================================
  智法通前端 | 仪表盘（角色感知数据范围，全部角色可见）
  ------------------------------------------------------------------
  数据源：GET /api/analytics/dashboard（后端按角色区分数据范围）
  数据范围：
    - 管理员：业务指标 = 全平台全部角色汇总
      （合同审查记录总数 / 咨询总数），满足全局运营视角；
    - 法务 / 普通用户：业务指标 = 仅本人数据（数据隔离）；
    - 知识库/图谱为平台共享资源，注册用户数为系统指标。
  面板：
    1. 六宫格核心指标；
    2. 合同风险分布饼图（ECharts，随主题自动重绘）；
    3. 最近合同审查记录表（分页）；
    4. 知识库文档类型环形图。
  ==================================================================
-->
<template>
  <div v-loading="loading" class="dash-page">
    <!-- ============ 核心指标六宫格 ============ -->
    <div class="kpi-grid">
      <div v-for="k in kpis" :key="k.label" class="kpi-card" :style="{ '--accent': k.color }">
        <el-icon class="kpi-icon"><component :is="k.icon" /></el-icon>
        <div class="kpi-val">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
        <div v-if="k.sub" class="kpi-sub">{{ k.sub }}</div>
      </div>
    </div>

    <!-- ============ 图表双栏 ============ -->
    <div class="chart-row">
      <div class="chart-card">
        <h3 class="card-title">合同风险等级分布</h3>
        <p class="card-sub">
          数据来源：已完成合同审查的风险等级统计，随新审查自动更新；
          当前已完成 <b>{{ data?.contract?.completed ?? 0 }}</b> 条 / 全部 {{ data?.contract?.total ?? 0 }} 条
        </p>
        <div ref="riskChartRef" class="chart-box" />
      </div>
      <div class="chart-card">
        <h3 class="card-title">知识库文档构成</h3>
        <p class="card-sub">
          数据来源：知识库文档类型统计（可在「知识库管理」中增删文档）；
          当前共 <b>{{ data?.knowledge?.total_docs ?? 0 }}</b> 篇
        </p>
        <div ref="kbChartRef" class="chart-box" />
      </div>
    </div>

    <!-- ============ 最近审查记录（分页） ============ -->
    <div class="table-card">
      <h3 class="card-title">最近合同审查记录</h3>
      <el-table :data="recent.items" v-loading="recentLoading" size="small" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="title" label="合同名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="contract_type" label="类型" width="110" />
        <el-table-column prop="risk_level" label="风险等级" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="dark" :type="row.risk_level === '高' ? 'danger' : row.risk_level === '中' ? 'warning' : 'success'">
              {{ row.risk_level }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="risk_score" label="评分" width="90" />
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ (row.duration_ms / 1000).toFixed(1) }}s</template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="170" />
      </el-table>
      <!-- 分页组件（最近审查记录翻页） -->
      <div class="table-pager">
        <el-pagination
          v-model:current-page="recentPage"
          :page-size="recentSize"
          :total="recentTotal"
          layout="prev, pager, next, total"
          @current-change="loadRecent"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 仪表盘逻辑：
 *   - 拉取 dashboard 聚合数据；
 *   - KPI 指标派生（computed）；
 *   - ECharts 饼图/环形图初始化（配色取自当前主题，
 *     监听主题切换 -> 销毁旧实例并按新配色重绘，保证双主题可读）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { get } from '../api/client'
import { useThemeStore } from '../stores/theme'
import { useDictStore } from '../stores/dict'
import { useAuthStore } from '../stores/auth'
import { useAutoRefresh } from '../composables/useAutoRefresh'
import { getChartPalette, makeChartRenderer } from '../utils/chartTheme'

const theme = useThemeStore()
const dictStore = useDictStore()
const auth = useAuthStore()
const loading = ref(false)
const data = ref<any>(null)
const riskChartRef = ref<HTMLElement>()
const kbChartRef = ref<HTMLElement>()
/** 图表实例表（resize / 卸载销毁统一管理，防内存泄漏） */
const instances = new Map<string, echarts.ECharts>()

// ---- 最近审查记录分页状态 ----
const recent = ref<{ items: any[] }>({ items: [] })
const recentPage = ref(1)
const recentSize = 5
const recentTotal = ref(0)
const recentLoading = ref(false)

/** 加载最近审查记录（专用分页接口，翻页不重绘图表；silent 静默） */
async function loadRecent(silent = false) {
  if (!silent) recentLoading.value = true
  try {
    const res = await get<any>('/analytics/recent-contracts', {
      page: recentPage.value, size: recentSize,
    })
    recent.value = res
    recentTotal.value = res.total || 0
  } finally {
    if (!silent) recentLoading.value = false
  }
}

/**
 * 仪表盘数据自动刷新（数据变化自动上屏，无需手动刷新浏览器）：
 * 定时 + 窗口聚焦时静默拉取；KPI/图表数据带快照比对，
 * 有新咨询/新审查时才重绘图表（避免 canvas 闪烁），
 * 最近审查记录列表始终静默刷新。
 */
let dashSnapshot = ''
useAutoRefresh(async () => {
  if (loading.value) return
  const fresh = await get<any>('/analytics/dashboard')
  const snap = JSON.stringify(fresh)
  if (snap !== dashSnapshot) {
    dashSnapshot = snap
    data.value = fresh
    renderAllCharts()
  }
  loadRecent(true)
}, 30000)

/** 风险分布饼图渲染（配色由当前主题派生） */
function renderRiskOption(_el: HTMLElement, chart: echarts.ECharts) {
  const p = getChartPalette()
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', formatter: '{b}: {c} 份 ({d}%)' },
    legend: { bottom: 0, textStyle: { color: p.legendText } },
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      center: ['50%', '44%'],
      itemStyle: { borderRadius: 8, borderColor: p.sliceBorder, borderWidth: 3 },
      label: { color: p.labelColor },
      data: (data.value?.contract?.risk_distribution || []).map((d: any) => ({
        ...d,
        itemStyle: {
          color: d.name === '高风险' ? '#f87171' : d.name === '中风险' ? '#fbbf24' : '#34d399',
        },
      })),
    }],
  })
}

/** 知识库构成环形图渲染（类型名称取自后端字典，数值为真实文档统计） */
function renderKbOption(_el: HTMLElement, chart: echarts.ECharts) {
  const p = getChartPalette()
  const k = data.value?.knowledge || {}
  // 扇区分类名来自业务字典（/meta/dictionaries doc_types），前端零硬编码；
  // 字典 value(law) 与统计字段(laws) 按复数规则映射
  const docTypes = dictStore.docTypes
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', formatter: '{b}: {c} 篇 ({d}%)' },
    legend: { bottom: 0, textStyle: { color: p.legendText } },
    series: [{
      type: 'pie',
      radius: '66%',
      center: ['50%', '44%'],
      roseType: 'radius',       // 玫瑰图（视觉更炫酷）
      itemStyle: { borderRadius: 6, borderColor: p.sliceBorder, borderWidth: 2 },
      label: { color: p.labelColor },
      data: docTypes.map((t: any, i: number) => ({
        name: t.label || t.value,
        value: k[`${t.value}s`] ?? 0,
        itemStyle: { color: CHART_SERIES_COLORS[i % CHART_SERIES_COLORS.length] },
      })),
    }],
  })
}

/** 图表系列色板（视觉样式而非业务数据） */
const CHART_SERIES_COLORS = ['#60a5fa', '#34d399', '#a78bfa', '#fbbf24', '#f472b6', '#38bdf8']

// 可重复调用的重绘器（内部自动 dispose 旧实例）
const rerenderRisk = makeChartRenderer(() => riskChartRef.value, renderRiskOption)
const rerenderKb = makeChartRenderer(() => kbChartRef.value, renderKbOption)

/** 执行全部图表重绘并登记实例 */
function renderAllCharts() {
  const risk = rerenderRisk()
  if (risk) instances.set('risk', risk)
  const kb = rerenderKb()
  if (kb) instances.set('kb', kb)
}

onMounted(async () => {
  dictStore.ensure()   // 业务字典（构成图分类名后端下发）
  loading.value = true
  try {
    const fresh = await get<any>('/analytics/dashboard')
    dashSnapshot = JSON.stringify(fresh)
    data.value = fresh
    renderAllCharts()
  } finally {
    loading.value = false
  }
  // 最近审查记录（分页表格，独立加载）
  loadRecent()
  // 窗口缩放自适应
  window.addEventListener('resize', onResize)
})

// 主题切换：以新主题配色整体重绘图表
watch(() => theme.mode, () => {
  if (data.value) renderAllCharts()
})

// 业务字典异步到达后重绘（知识库构成图的分类名来自字典）
watch(() => dictStore.loaded, (loaded) => {
  if (loaded && data.value) renderAllCharts()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  instances.forEach((c) => c.dispose())
})

/** KPI 六宫格（数据到达后派生；标签与数值口径随角色切换：
 *  管理员 = 全平台汇总（合同审查记录总数/咨询总数），
 *  法务 / 普通用户 = 仅本人数据（我的咨询/我的合同审查）） */
const kpis = computed(() => {
  const d = data.value || {}
  const isAdmin = auth.isAdmin
  return [
    { label: '注册用户', value: d.users?.total ?? 0,
      sub: `法务 ${d.users?.legal_admins ?? 0} · 管理员 ${d.users?.admins ?? 0}`,
      icon: 'UserFilled', color: '#60a5fa' },
    { label: isAdmin ? '咨询总数' : '我的咨询',
      value: isAdmin ? (d.qa?.total_consultations ?? 0) : (d.qa?.today_queries ?? 0),
      sub: isAdmin
        ? `今日 ${d.qa?.today_queries ?? 0} 条 · 累计消息 ${d.qa?.total_messages ?? 0}`
        : `累计消息 ${d.qa?.total_messages ?? 0}`,
      icon: 'ChatDotRound', color: '#34d399' },
    { label: '平均置信度', value: ((d.qa?.avg_confidence ?? 0) * 100).toFixed(0) + '%',
      sub: `平均耗时 ${((d.qa?.avg_latency_ms ?? 0) / 1000).toFixed(1)}s`,
      icon: 'CircleCheck', color: '#a78bfa' },
    { label: isAdmin ? '合同审查记录总数' : '我的合同审查',
      value: d.contract?.total ?? 0,
      sub: `已完成 ${d.contract?.completed ?? 0} · 平均风险分 ${d.contract?.avg_risk_score ?? 0}`,
      icon: 'DocumentChecked', color: '#fbbf24' },
    { label: '知识库文档', value: d.knowledge?.total_docs ?? 0,
      sub: `共 ${((d.knowledge?.total_chars ?? 0) / 10000).toFixed(1)} 万字`,
      icon: 'Collection', color: '#38bdf8' },
    { label: '图谱节点', value: d.graph?.nodes ?? (d.graph?.available ? 0 : '-'),
      sub: d.graph?.available ? `关系边 ${d.graph?.edges ?? 0}` : '图谱服务未连接',
      icon: 'Connection', color: '#f472b6' },
  ]
})

function onResize() {
  instances.forEach((c) => c.resize())
}
</script>

<style scoped>
.dash-page { display: flex; flex-direction: column; gap: 16px; }

/* ===== KPI 六宫格 ===== */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 14px;
}
.kpi-card {
  position: relative;
  padding: 18px 16px 14px;
  border-radius: 14px;
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  overflow: hidden;
  box-shadow: var(--zft-shadow-card);
}
/* 顶部渐变强调条（CSS 变量按卡片换色） */
.kpi-card::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, var(--accent), transparent);
}
.kpi-icon { font-size: 22px; color: var(--accent); }
.kpi-val { font-size: 30px; font-weight: 800; color: var(--zft-text); margin: 8px 0 2px; }
.kpi-label { font-size: 13px; color: var(--zft-text-dim); }
.kpi-sub { font-size: 11.5px; color: var(--zft-text-faint); margin-top: 6px; }

/* ===== 图表卡 ===== */
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chart-card, .table-card {
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  border-radius: 14px;
  padding: 18px;
  box-shadow: var(--zft-shadow-card);
}
.card-title { color: var(--zft-text); font-size: 15px; margin: 0 0 6px; }
.card-sub { margin: 0 0 10px; font-size: 12px; color: var(--zft-text-faint); }
.card-sub b { color: var(--zft-primary-text); }
.chart-box { height: 300px; }
.table-pager { display: flex; justify-content: flex-end; margin-top: 12px; }

@media (max-width: 1000px) {
  .chart-row { grid-template-columns: 1fr; }
}
</style>
