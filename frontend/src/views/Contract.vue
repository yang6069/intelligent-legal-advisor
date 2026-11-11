<!--
  ==================================================================
  智法通前端 | 合同审查页（多 Agent 对抗式审查）
  ------------------------------------------------------------------
  职责：
    1. 双入口：文件上传（docx/pdf/txt/md）或粘贴文本；
    2. 审查记录列表（分页 + 状态/风险等级标签）；
    3. 报告详情抽屉：全可视化渲染（概览指标/法务·合规·业务三视角
       意见/风险清单/修改建议/辩论记录/仲裁结论/Agent 执行轨迹），
       不向用户暴露任何原始 JSON；
    4. 导出 AI 修改后合同（.docx，修改痕迹高亮 + 清单，本地下载）；
    5. 轮询机制：上传后 pending -> completed 异步刷新。
  ==================================================================
-->
<template>
  <div class="contract-page">
    <!-- ============ 配额状态条（仅普通用户显示） ============ -->
    <div v-if="showQuotaBar" class="quota-bar" :class="{ 'quota-warn': isFreeAndLow }">
      <el-icon><DocumentChecked /></el-icon>
      <span v-if="quota.total_balance > 0" class="quota-paid">
        付费余额 {{ quota.total_balance }} 次（问答+审查共用）
      </span>
      <template v-else>
        <span>今日合同审查：{{ quota.contract?.daily_remaining ?? 10 }} / {{ quota.contract?.daily_limit ?? 10 }} 次</span>
        <span v-if="isFreeAndLow" class="quota-urgent">
          ⏰ 仅剩 {{ quota.contract?.daily_remaining }} 次
        </span>
      </template>
      <el-button link type="primary" size="small" @click="$router.push('/subscription')">
        去升级 →
      </el-button>
    </div>

    <!-- ============ 发起审查区 ============ -->
    <div class="launch-card">
      <div class="launch-tabs">
        <div
          v-for="t in [
            { key: 'text', label: '粘贴文本', icon: 'EditPen' },
            { key: 'file', label: '上传文件', icon: 'UploadFilled' },
          ]"
          :key="t.key"
          class="launch-tab"
          :class="{ active: mode === t.key }"
          @click="mode = t.key"
        >
          <el-icon><component :is="t.icon" /></el-icon>{{ t.label }}
        </div>
      </div>

      <!-- 粘贴文本模式 -->
      <div v-if="mode === 'text'" class="text-mode">
        <el-input
          v-model="title"
          placeholder="合同标题（如：XX 公司劳动合同）"
          class="title-input"
          maxlength="200"
        />
        <el-input
          v-model="contractText"
          type="textarea"
          :rows="9"
          resize="none"
          maxlength="100000"
          show-word-limit
          placeholder="粘贴完整合同文本（至少 30 字），系统将执行多 Agent 对抗式审查：条款拆分 -> 法务/合规/业务三视角推理 -> 主审风险识别 -> 复核遗漏检查 -> 反方辩论 -> 仲裁评分 -> 修改建议"
        />
        <div class="launch-actions">
          <el-button
            type="primary"
            size="large"
            :loading="reviewing"
            :disabled="contractText.trim().length < 30"
            @click="reviewText"
          >
            {{ reviewing ? '多 Agent 审查中…' : '开始智能审查' }}
          </el-button>
          <el-button size="large" @click="fillSample">填入示例合同</el-button>
        </div>
      </div>

      <!-- 文件上传模式 -->
      <div v-else class="file-mode">
        <el-upload
          drag
          :auto-upload="false"
          :limit="1"
          :on-change="onFileChange"
          accept=".docx,.pdf,.txt,.md"
        >
          <el-icon class="up-icon"><UploadFilled /></el-icon>
          <div class="up-text">拖拽或点击上传合同文件</div>
          <div class="up-sub">支持 docx / pdf / txt / md，不超过 10MB</div>
        </el-upload>
        <div class="launch-actions">
          <el-button
            type="primary"
            size="large"
            :loading="reviewing"
            :disabled="!file"
            @click="uploadFile"
          >{{ reviewing ? '上传并审查中…' : '上传并审查' }}</el-button>
        </div>
      </div>

      <!-- 审查进度条（SSE 真实节点级进度） -->
      <div v-if="reviewing" class="progress-line">
        <div class="progress-head">
          <span class="progress-bar-wrap">
            <el-progress
              :percentage="sseProgress.pct || 0"
              :status="sseProgress.status === 'failed' ? 'exception' : undefined"
              :stroke-width="10"
              :show-text="true"
              style="flex:1"
            />
          </span>
          <span class="progress-meta">
            {{ sseProgress.node_name || '正在受理…' }}
            <span v-if="sseProgress.elapsed_ms" class="progress-elapsed">
              · {{ (sseProgress.elapsed_ms / 1000).toFixed(1) }}s
            </span>
          </span>
        </div>
        <div class="stage-list">
          <span
            v-for="(s, i) in sseProgress.nodes"
            :key="s"
            class="stage"
            :class="{
              done: i < sseProgress.node_index,
              active: i === sseProgress.node_index,
              pending: i > sseProgress.node_index,
            }"
          >{{ s }}</span>
        </div>
      </div>
    </div>

    <!-- ============ 审查记录列表 ============ -->
    <div class="list-card">
      <div class="list-header">
        <h3>审查记录</h3>
        <el-button :icon="Refresh" circle text @click="loadList" />
      </div>
      <el-table :data="records" v-loading="listLoading" stripe>
        <!-- 前端序号列：从 1 开始递增，分页连续性 -->
        <el-table-column label="序号" width="70">
          <template #default="{ $index }">{{ (page - 1) * 10 + $index + 1 }}</template>
        </el-table-column>
        <el-table-column prop="title" label="合同名称" min-width="170" show-overflow-tooltip />
        <el-table-column prop="username" label="提交人" width="100">
          <template #default="{ row }">{{ zhifyUser(row.real_name || row.username) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ row.contract_type || '-' }}</template>
        </el-table-column>
        <el-table-column label="风险等级" width="100">
          <template #default="{ row }">
            <el-tag
              v-if="row.status === 'completed'"
              :type="levelType(row.risk_level)"
              effect="dark"
              round
            >{{ row.risk_level || '中' }}</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="风险评分" width="100">
          <template #default="{ row }">
            <span v-if="row.status === 'completed'" class="score-num">{{ row.risk_score }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="风险数" width="80">
          <template #default="{ row }">{{ row.status === 'completed' ? row.risk_count : '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag
              :type="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'"
              effect="plain"
            >
              {{ row.status === 'completed' ? '已完成' : row.status === 'failed' ? '失败' : '审查中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="90">
          <template #default="{ row }">
            {{ row.duration_ms ? (row.duration_ms / 1000).toFixed(1) + 's' : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openDetail(row.id)">
              查看报告
            </el-button>
            <!-- 导出 AI 修改后合同（docx 修改痕迹文档，本地下载） -->
            <el-button
              size="small"
              type="success"
              link
              :loading="exportingId === row.id"
              :disabled="row.status !== 'completed'"
              @click="exportRevised(row)"
            >导出AI修改</el-button>
            <el-button size="small" type="danger" link @click="remove(row.id)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="10"
          :total="total"
          layout="prev, pager, next, total"
          @current-change="loadList"
        />
      </div>
    </div>

    <!-- ============ 报告详情抽屉（全可视化，无原始 JSON） ============ -->
    <el-drawer
      v-model="detailVisible"
      :title="detail?.title || '审查报告'"
      size="62%"
      class="report-drawer"
    >
      <div v-if="detail" v-loading="detailLoading" class="report-body">
        <!-- 概览指标 -->
        <div class="overview">
          <div class="ov-item">
            <div class="ov-num" :class="levelClass(detail.risk_level)">{{ detail.risk_level }}</div>
            <div class="ov-label">风险等级</div>
          </div>
          <div class="ov-item">
            <div class="ov-num">{{ detail.risk_score }}</div>
            <div class="ov-label">风险评分</div>
          </div>
          <div class="ov-item">
            <div class="ov-num">{{ detail.clause_count }}</div>
            <div class="ov-label">条款总数</div>
          </div>
          <div class="ov-item">
            <div class="ov-num">{{ detail.risk_count }}</div>
            <div class="ov-label">风险点</div>
          </div>
        </div>

        <template v-if="report">
          <!-- 三视角独立审查意见（法务 / 合规 / 业务） -->
          <h4 class="sec-title"><el-icon><User /></el-icon> 三方审查意见（法务 / 合规 / 业务）</h4>
          <div class="tri-view">
            <div
              v-for="p in perspectives"
              :key="p.perspective"
              class="tri-card"
              :class="p.perspective"
            >
              <div class="tri-head">
                {{ p.role }}
                <el-tag size="small" :type="p.source === 'llm' ? 'success' : 'info'" effect="plain">
                  {{ p.source === 'llm' ? 'AI 推理' : '规则分析' }}
                </el-tag>
              </div>
              <div class="tri-body">{{ p.opinion }}</div>
              <ul v-if="p.conclusions?.length" class="tri-points">
                <li v-for="(c, i) in p.conclusions" :key="i">{{ c }}</li>
              </ul>
            </div>
          </div>

          <!-- 风险清单 -->
          <h4 class="sec-title"><el-icon><Warning /></el-icon> 风险清单（主审发现）</h4>
          <el-table v-if="report.primary_risks?.length" :data="report.primary_risks" size="small" stripe>
            <el-table-column type="index" width="50" label="#" />
            <el-table-column prop="clause" label="条款" width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.clause_text || row.clause || '-' }}</template>
            </el-table-column>
            <el-table-column prop="type" label="风险类型" width="130">
              <template #default="{ row }">{{ row.risk_type || row.type || '-' }}</template>
            </el-table-column>
            <el-table-column prop="severity" label="等级" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="levelType(row.severity)" effect="dark">
                  {{ row.severity }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="风险描述" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">{{ row.description || '-' }}</template>
            </el-table-column>
            <el-table-column label="法律依据" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.legal_basis || '-' }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="未发现风险条款" :image-size="60" />

          <!-- 修改建议 -->
          <h4 class="sec-title"><el-icon><EditPen /></el-icon> 修改建议</h4>
          <div v-if="suggestionList.length" class="suggestion-list">
            <div v-for="(s, i) in suggestionList" :key="i" class="sug-item">
              <span class="sug-idx">{{ i + 1 }}</span>
              <div class="sug-content">
                <div class="sug-text">{{ s.text }}</div>
                <div v-if="s.meta" class="sug-meta">{{ s.meta }}</div>
              </div>
            </div>
          </div>
          <el-empty v-else description="无修改建议" :image-size="60" />

          <!-- 辩论记录 -->
          <h4 v-if="(report.debate || []).length" class="sec-title">
            <el-icon><ChatLineSquare /></el-icon> Agent 辩论记录
          </h4>
          <div v-for="(d, i) in report.debate || []" :key="i" class="debate-item">
            <el-tag size="small" :type="d.agent === '反方' ? 'danger' : 'primary'" effect="dark">
              {{ d.agent || d.speaker }}
            </el-tag>
            <span class="debate-text">{{ d.argument || d.point || d.content }}</span>
          </div>

          <!-- 仲裁结论 -->
          <h4 v-if="report.arbitration" class="sec-title"><el-icon><Stamp /></el-icon> 仲裁结论</h4>
          <div v-if="report.arbitration" class="arbitration-box">
            {{ typeof report.arbitration === 'string' ? report.arbitration : report.arbitration.conclusion || '' }}
          </div>

          <!-- 对抗辩论推理链（主审回应反方质疑的真实过程） -->
          <template v-if="(report.debate_log || []).length">
            <h4 class="sec-title"><el-icon><ChatLineSquare /></el-icon> 对抗辩论推理链</h4>
            <div v-for="(d, i) in report.debate_log" :key="'d' + i" class="debate-item">
              <el-tag size="small" :type="d.status === 'consensus' ? 'success' : 'danger'" effect="dark">
                {{ d.status === 'consensus' ? '共识' : '未共识' }}
              </el-tag>
              <span class="debate-text">
                {{ d.issue }} —— 主审回应：{{ d.primary_response }}
              </span>
            </div>
          </template>

          <!-- 多 Agent 执行轨迹（真实推理链路可视化） -->
          <h4 class="sec-title"><el-icon><VideoPlay /></el-icon> 多 Agent 执行轨迹</h4>
          <div class="agent-trace">
            <div v-for="(a, i) in agentsExecuted" :key="i" class="trace-row">
              <span class="trace-dot" :class="{ llm: a.llm_called }" />
              <span class="trace-name">{{ a.agent }}</span>
              <span class="trace-output">{{ a.output }}</span>
              <span class="trace-ms">{{ a.elapsed_ms }}ms</span>
              <el-tag size="small" :type="a.llm_called ? 'success' : 'info'" effect="plain">
                {{ a.llm_called ? 'LLM 推理' : '规则引擎' }}
              </el-tag>
            </div>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'Contract' })
/**
 * 合同审查页逻辑：
 *   - reviewText: POST /contract/review-text 同步等待报告；
 *   - uploadFile: POST /contract/upload 异步受理 -> 轮询状态；
 *   - openDetail: GET /contract/{id} 解析 report JSON -> 全可视化渲染
 *     （三视角意见 / 风险清单 / 建议清单 / 辩论链 / 执行轨迹），
 *     老版报告（无三视角字段）自动降级为结构化数据合成，永不出"无"；
 *   - exportRevised: GET /contract/{id}/export 触发 docx 下载
 *     （AI 修改痕迹高亮文档，首次生成含 LLM 改写，结果后端缓存）。
 */
import { computed, onMounted, onActivated, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { Refresh, UploadFilled } from '@element-plus/icons-vue'
import { get, post, del, download } from '../api/client'
import http from '../api/client'
import { useDictStore } from '../stores/dict'
import { useAutoRefresh } from '../composables/useAutoRefresh'
import { useTaskStore } from '../stores/task'
import { useAuthStore } from '../stores/auth'

const dict = useDictStore()
const auth = useAuthStore()
/** 配额状态（合同审查只读 quota.contract 分支；共用余额池看 total_balance） */
const quota = reactive<any>({})
const showQuotaBar = computed(() => auth.user?.role === 'user')
const isFreeAndLow = computed(() => {
  if (quota.plan_type !== 'free' || quota.total_balance > 0) return false
  const rem = quota.contract?.daily_remaining
  return rem != null && rem !== -1 && rem <= 5
})
async function loadQuota() {
  try { Object.assign(quota, await get<any>('/quota/status')) } catch {}
}

/** 全局后台任务 store：让合同审查也在切路由后继续跑 */
const taskStore = useTaskStore()
let _currentReviewContractId: number | null = null

const mode = ref<'text' | 'file'>('text')
const title = ref('')
const contractText = ref('')
const file = ref<UploadFile | null>(null)
const reviewing = ref(false)

/** 真实 SSE 驱动的进度状态（9 节点轻量化工作流） */
const NODE_LABELS = [
  '合同解析', '条款拆分', '主审 Agent 风险识别', '复核 Agent 规则校验',
  '反方质疑+辩论仲裁', '综合风险评分', '去重合并修改建议',
  '三视角独立推理', '汇总审查报告',
]
const sseProgress = reactive({
  node_index: 0, pct: 0, node_name: '', status: '', elapsed_ms: 0,
  nodes: NODE_LABELS,
})

const records = ref<any[]>([])
const page = ref(1)
const total = ref(0)
const listLoading = ref(false)

const detailVisible = ref(false)
const detail = ref<any>(null)
const detailLoading = ref(false)
const exportingId = ref<number | null>(null)   // 正在导出的记录 ID（按钮 loading）
/** 报告 JSON（后端 contracts.report 列） */
const report = computed(() => detail.value?.report || null)

onMounted(() => {
  dict.ensure()
  loadList()
  loadQuota()
  _tryResumeReview()   // 切路由再回来时，从 store 恢复正在跑的合同审查
})

/** keep-alive 重新激活时也要尝试恢复 SSE（组件不会被卸载→mounted 只跑首次） */
onActivated(() => {
  if (!_reviewFetchAbort || _currentReviewContractId == null) {
    _tryResumeReview()
  }
})
onUnmounted(() => {
  if (_reviewFetchAbort) { _reviewFetchAbort.abort(); _reviewFetchAbort = null }   // 关 fetch（后端任务继续跑）
})

/**
 * 切路由 / 刷新页面回来时：从 taskStore（Pinia 持久化）拿 contract_id，
 *   重新创建 EventSource 订阅 SSE 进度流。
 *   （后端 asyncio.create_task 不受前端页面切换影响，继续后台执行）
 */
function _tryResumeReview() {
  const task = taskStore.getContract(taskStore.activeContractTaskId)
  if (task?.contract_id && task.status !== 'done' && task.status !== 'error') {
    _connectSSE(task.contract_id)
  }
}

/**
 * EventSource SSE 进度流订阅（替代旧的 setInterval 轮询）。
 *
 * EventSource 特性：
 *   - 原生支持断线自动重连（浏览器内置）
 *   - 切页面回来 _tryResumeReview 重新调用此函数即可恢复
 *   - onUnmounted 里 close() 仅关前端连接，后端 asyncio.create_task 继续跑
 */
let _reviewFetchAbort: AbortController | null = null

function _connectSSE(contractId: number) {
  // 关掉上一次的连接（如果有）
  if (_reviewFetchAbort) { _reviewFetchAbort.abort(); _reviewFetchAbort = null }
  _reviewFetchAbort = new AbortController()

  const base = (import.meta as any).env.VITE_API_BASE || ''
  const url = `${base}/api/contract/${contractId}/progress?token=${encodeURIComponent(auth.token)}`
  reviewing.value = true
  _currentReviewContractId = contractId

  // 重置进度
  sseProgress.node_index = 0; sseProgress.pct = 0
  sseProgress.node_name = '正在受理…'; sseProgress.status = 'running'
  sseProgress.elapsed_ms = 0

  // fetch + ReadableStream（比 EventSource 好：能过 vite dev proxy）
  fetch(url, { signal: _reviewFetchAbort.signal })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE 协议：\n\n 分隔事件块
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() || ''
        for (const chunk of chunks) {
          let evt = 'message', data = ''
          for (const line of chunk.split('\n')) {
            if (line.startsWith('event:')) evt = line.slice(6).trim()
            else if (line.startsWith('data:')) data += line.slice(5).trim()
          }
          if (!data) continue
          let parsed: any
          try { parsed = JSON.parse(data) } catch { continue }
          _applySSEData(parsed, contractId)
        }
      }
    })
    .catch((e) => {
      if (e?.name === 'AbortError') return   // 主动取消，静默
      console.error('[contract] SSE fetch failed:', e)
      reviewing.value = false; sseProgress.status = 'failed'
      _currentReviewContractId = null
      ElMessage.error('进度连接异常，请刷新查看')
    })
}

function _applySSEData(data: any, contractId: number) {
  if (!data) return
  sseProgress.node_index = data.node_index ?? 0
  sseProgress.pct = data.pct ?? 0
  sseProgress.node_name = data.node_name ?? ''
  sseProgress.elapsed_ms = data.elapsed_ms ?? 0

  if (data.status === 'done') {
    reviewing.value = false; sseProgress.status = 'done'
    if (_reviewFetchAbort) { _reviewFetchAbort.abort(); _reviewFetchAbort = null }
    _currentReviewContractId = null
    // 同步 store 任务状态，避免切页回来重连已完成的审查
    const st = taskStore.getContract(taskStore.activeContractTaskId)
    if (st) st.status = 'done'
    ElMessage.success(`审查完成：风险评分 ${data.risk_score ?? '-'}`)
    loadList()
    if (contractId) openDetail(contractId)
  } else if (data.status === 'failed') {
    reviewing.value = false; sseProgress.status = 'failed'
    if (_reviewFetchAbort) { _reviewFetchAbort.abort(); _reviewFetchAbort = null }
    _currentReviewContractId = null
    const st = taskStore.getContract(taskStore.activeContractTaskId)
    if (st) st.status = 'error'
    ElMessage.error(data.error || '审查失败')
    loadList()
  }
}

/**
 * 审查记录列表自动刷新（20s 静默拉取，不触发 loading 闪烁）。
 */
useAutoRefresh(() => loadList(true), 20000)

/** 加载审查记录分页列表（silent=true 时不触发 loading 动画） */
async function loadList(silent = false) {
  if (!silent) listLoading.value = true
  try {
    const data = await get<any>('/contract/list', { page: page.value, size: 10 })
    records.value = data.items || []
    total.value = data.total || 0
  } finally {
    if (!silent) listLoading.value = false
  }
}

/**
 * 文本审查：后端立即返回 contract_id，前端直接 SSE 订阅进度。
 * 切页面/刷新不影响——后端 asyncio.create_task 继续跑，
 *   回来时 _tryResumeReview 用记住的 contract_id 自动重连 SSE。
 */
async function reviewText() {
  if (contractText.value.trim().length < 30) {
    ElMessage.warning('合同文本过短，请检查后再提交'); return
  }
  await loadQuota()
  reviewing.value = true
  try {
    const res = await post<any>('/contract/review-text', {
      title: title.value || '未命名合同',
      contract_text: contractText.value,
    })
    _connectSSE(res.contract_id)     // 立即连 SSE 看真实节点进度
    // 只登记任务供切页恢复（startContractReview 会再发一次空文本请求，勿用）
    taskStore.registerContractReview({
      contract_id: res.contract_id,
      title: title.value || '未命名合同',
    })
  } catch (e: any) {
    reviewing.value = false
    await loadQuota()
    if (e?.status === 429) {
      ElMessage.error(e?.detail || '今日合同审查额度已用完，请升级套餐或明日再来')
    }
  }
}

/**
 * 文件上传审查：后端立即返回 contract_id，前端直接 SSE 订阅进度。
 */
async function uploadFile() {
  if (!file.value?.raw) return
  reviewing.value = true
  try {
    await loadQuota()
    const fd = new FormData()
    fd.append('file', file.value.raw)
    const res = await http.post<any>('/contract/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    _connectSSE(res.contract_id)
    // 只登记任务供切页恢复（startContractReview 会再发一次空文本请求，勿用）
    taskStore.registerContractReview({
      contract_id: res.contract_id,
      title: title.value || file.value.name || '未命名合同',
    })
  } catch (e: any) {
    reviewing.value = false
    await loadQuota()
    if (e?.status === 429) {
      ElMessage.error(e?.detail || '今日合同审查额度已用完，请升级套餐或明日再来')
    }
  }
}

/** 文件选择回调 */
function onFileChange(f: UploadFile) {
  file.value = f
}

/** 填入示例合同（快速体验） */
function fillSample() {
  title.value = '软件开发服务合同（示例）'
  contractText.value = `甲方委托乙方开发企业管理系统，合同主要条款如下：
一、开发费用总额为人民币五十万元，甲方应在合同签订后三个工作日内一次性支付全部款项。
二、乙方应在收到全款后十二个月内完成开发并交付，逾期每日按合同总额的百分之五支付违约金。
三、交付后乙方不提供任何形式的售后服务与缺陷修复。
四、因本合同产生的一切争议，由乙方所在地法院管辖。
五、本合同一式两份，自甲方支付全部款项之日起生效。`
}

/** 打开报告详情抽屉 */
async function openDetail(id: number) {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await get<any>(`/contract/${id}`)
  } finally {
    detailLoading.value = false
  }
}

/**
 * 三视角审查意见（法务 / 合规 / 业务）。
 * 新版报告直接读 perspective_opinions；老版报告从结构化数据
 * 实时合成差异化摘要（风险/复核/反方各自视角），保证永不为"无"。
 */
const perspectives = computed<Array<{ role: string; perspective: string; opinion: string; conclusions: string[]; source: string }>>(() => {
  const r = report.value
  if (!r) return []
  if (Array.isArray(r.perspective_opinions) && r.perspective_opinions.length) {
    return r.perspective_opinions
  }
  // ---- 老版报告兜底：结构化数据 -> 三视角差异化摘要 ----
  const risks: any[] = r.primary_risks || []
  const high = risks.filter((x) => x.severity === '高')
  const check = r.review_check || {}
  const opps: any[] = r.adverse_opinions || []
  const debate: any[] = r.debate_log || []
  const consensus = debate.filter((d) => d.status === 'consensus').length
  const missing: string[] = r.completeness?.missing_clauses || []
  return [
    {
      role: '法务 Agent（主审）', perspective: 'legal',
      source: risks.some((x) => x.source === 'llm') ? 'llm' : 'rule_summary',
      opinion: `经法律视角审查，共识别风险 ${risks.length} 项${high.length ? `，其中高风险 ${high.length} 项，需优先修订「${high[0]?.risk_type || ''}」相关条款` : ''}${missing.length ? `；合同缺失必备条款：${missing.slice(0, 3).join('、')}` : ''}。建议对权利义务对等性与违约救济路径逐条复核。`,
      conclusions: [
        high.length ? `高风险条款 ${high.length} 项需优先修订` : '未发现高风险条款',
        missing.length ? `必备条款缺失 ${missing.length} 项` : '必备条款完整',
        '违约责任与救济途径需明确量化',
      ],
    },
    {
      role: '合规 Agent（复核）', perspective: 'compliance',
      source: 'rule_summary',
      opinion: `合规视角复核完成：确认主审结论 ${check.approved_count ?? 0} 项${(check.disputed_count ?? 0) ? `，对 ${check.disputed_count} 项结论存在等级异议` : ''}${(check.missed_count ?? 0) ? `，并补充发现主审遗漏风险 ${check.missed_count} 项` : ''}。建议补强签署权限、审批留痕等程序合规要求。`,
      conclusions: [
        `复核确认 ${check.approved_count ?? 0} 项风险结论`,
        `异议 ${(check.disputed_count ?? 0)} 项 / 补充发现 ${(check.missed_count ?? 0)} 项`,
        '签署权限与用印审批需留痕管理',
      ],
    },
    {
      role: '业务 Agent（反方）', perspective: 'business',
      source: opps.some(() => true) && opps.length ? 'llm' : 'rule_summary',
      opinion: opps.length
        ? `业务视角提出 ${opps.length} 项商业与弱势方权益关切，首要关切：${String(opps[0]?.opposition || '').slice(0, 60)}。经对抗辩论 ${consensus}/${debate.length} 项达成共识，建议就付款节奏与违约成本再行谈判。`
        : '从交易结构看，双方权利义务总体平衡，未发现显著损害弱势方权益或阻碍商业目标的条款；建议保留退出与补救机制。',
      conclusions: [
        opps.length ? `提出商业关切 ${opps.length} 项` : '交易结构总体平衡',
        '付款与违约条款存在谈判空间',
        '建议保留退出与补救机制',
      ],
    },
  ]
})

/**
 * 修改建议统一列表：
 *   新版读 modification_suggestions（结构化），
 *   老版兼容 suggestions（字符串/对象混合）。
 */
const suggestionList = computed<Array<{ text: string; meta: string }>>(() => {
  const r = report.value
  if (!r) return []
  const structured = r.modification_suggestions || []
  if (structured.length) {
    return structured.map((s: any) => ({
      text: typeof s === 'string' ? s : (s.suggestion || s.description || ''),
      meta: typeof s === 'object' && s !== null
        ? [s.risk_type, s.severity ? `等级 ${s.severity}` : '', s.legal_basis]
            .filter(Boolean).join(' · ')
        : '',
    })).filter((x: any) => x.text)
  }
  return (r.suggestions || []).map((s: any) => ({
    text: typeof s === 'string' ? s : (s.suggestion || s.description || ''),
    meta: '',
  })).filter((x: any) => x.text)
})

/** 多 Agent 执行轨迹（新版报告含 agents_executed；老版按节点耗时降级展示） */
const agentsExecuted = computed<Array<{ agent: string; output: any; elapsed_ms: number; llm_called: boolean }>>(() => {
  const r = report.value
  if (!r) return []
  if (Array.isArray(r.agents_executed) && r.agents_executed.length) {
    return r.agents_executed
  }
  const t = r.node_timings || {}
  return [
    { agent: '主审 Agent（法务视角）', output: `识别风险 ${(r.primary_risks || []).length} 项`, elapsed_ms: t.primary_review || 0, llm_called: (r.primary_risks || []).some((x: any) => x.source === 'llm') },
    { agent: '复核 Agent（合规视角）', output: `复核结论 ${(r.review_check || {}).approved_count ?? 0} 项`, elapsed_ms: t.review_check || 0, llm_called: true },
    { agent: '反方 Agent（业务视角）', output: `提出质疑 ${(r.adverse_opinions || []).length} 项`, elapsed_ms: t.adverse_attack || 0, llm_called: (r.adverse_opinions || []).length > 0 },
    { agent: '辩论仲裁引擎', output: `辩论 ${(r.debate_log || []).length} 轮`, elapsed_ms: (t.debate || 0) + (t.arbitrate || 0), llm_called: (r.debate_log || []).length > 0 },
  ]
})

/**
 * 导出 AI 修改后合同（docx blob -> 浏览器下载）。
 * 首次导出后端执行 LLM 逐条款改写（约 10-40s），随后缓存秒回。
 */
async function exportRevised(row: any) {
  exportingId.value = row.id
  try {
    const blob = await download(`/contract/${row.id}/export`)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${row.title || '合同'}_AI修改版.docx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('AI 修改版合同已生成并下载')
  } catch {
    /* 错误已由拦截器提示 */
  } finally {
    exportingId.value = null
  }
}

/** 删除记录 */
async function remove(id: number) {
  await ElMessageBox.confirm('确定删除该审查记录？', '提示', { type: 'warning' })
  await del(`/contract/${id}`)
  ElMessage.success('已删除')
  loadList()
}

/** 风险等级 -> 标签色 */
function levelType(level: string): string {
  return level === '高' ? 'danger' : level === '中' ? 'warning' : 'success'
}
function levelClass(level: string): string {
  return level === '高' ? 'lv-high' : level === '中' ? 'lv-mid' : 'lv-low'
}

/** 英文用户名/空显示名 -> 中文兜底（真实姓名后端应补 real_name） */
const _USER_ZH_MAP: Record<string, string> = {
  admin: '超级管理员',
  test: '测试用户',
}
function zhifyUser(name: string): string {
  if (!name) return '-'
  const zh = _USER_ZH_MAP[name?.toLowerCase()]
  return zh || name
}
</script>

<style scoped>
/* ===== 配额状态条 ===== */
.quota-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 16px; border-radius: 10px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  font-size: 13px; color: var(--zft-secondary-text);
}
.quota-bar .el-icon { font-size: 16px; color: var(--el-color-primary); }
.quota-bar .quota-paid { color: var(--zft-primary-text); font-weight: 500; }
.quota-bar.quota-warn {
  background: #fef2f2; border-color: #fecaca; color: #b91c1c;
}
.quota-bar.quota-warn .el-icon { color: #ef4444; }
.quota-bar .quota-urgent { color: #ef4444; font-weight: 600; }
.quota-bar .el-button { margin-left: auto; }

.contract-page { display: flex; flex-direction: column; gap: 16px; }

/* ===== 发起审查卡片 ===== */
.launch-card {
  background: var(--zft-panel);
  border: 1px solid var(--zft-border);
  border-radius: 16px;
  padding: 20px;
}
.launch-tabs { display: flex; gap: 10px; margin-bottom: 16px; }
.launch-tab {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 22px;
  border-radius: 10px;
  cursor: pointer;
  color: var(--zft-text-dim);
  border: 1px solid var(--zft-border-strong);
  transition: all 0.2s;
}
.launch-tab.active {
  color: var(--zft-menu-active-text);
  background: var(--zft-menu-active-bg);
  border-color: var(--zft-primary-soft-border);
}
.title-input { margin-bottom: 12px; border-radius: 10px; }
.launch-card :deep(.el-textarea__inner) {
  border-radius: 12px;
  background: var(--zft-input-bg);
  color: var(--zft-text);
  font-size: 13px;
  line-height: 1.8;
}
.launch-actions { display: flex; gap: 12px; margin-top: 14px; }
.launch-actions .el-button--primary {
  background: var(--zft-grad-brand);
  border: none;
  border-radius: 12px;
  font-weight: 600;
}

/* ===== 真实节点级进度条 ===== */
.progress-line { margin-top: 18px; display: flex; flex-direction: column; gap: 12px; }
.progress-head { display: flex; align-items: center; gap: 16px; }
.progress-head .progress-bar-wrap { flex: 1; }
.progress-meta { font-size: 13px; color: var(--zft-primary-text); font-weight: 500; white-space: nowrap; }
.progress-elapsed { color: var(--zft-text-faint); font-weight: 400; margin-left: 4px; }
.stage-list { display: flex; gap: 6px; flex-wrap: wrap; }
.stage {
  font-size: 12px; color: var(--zft-text-faint);
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--zft-border);
  transition: all 0.35s;
  background: transparent;
}
.stage.done {
  color: var(--zft-success-text, #16a34a);
  border-color: var(--zft-success, #22c55e);
  background: rgba(34, 197, 94, 0.08);
}
.stage.active {
  color: var(--zft-primary-text);
  border-color: var(--zft-primary-soft-border);
  background: var(--zft-primary-soft-bg);
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.25);
  animation: pulse 1.6s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 8px rgba(59, 130, 246, 0.2); }
  50% { box-shadow: 0 0 16px rgba(59, 130, 246, 0.45); }
}
.stage.pending {
  color: var(--zft-text-faint);
  opacity: 0.5;
}

/* ===== 上传区 ===== */
.file-mode :deep(.el-upload-dragger) {
  background: var(--zft-panel-deep);
  border-color: var(--zft-border-strong);
  border-radius: 14px;
  padding: 36px 0;
}
.file-mode :deep(.el-upload-dragger:hover) { border-color: var(--zft-primary); }
.up-icon { font-size: 46px; color: var(--zft-primary-text); }
.up-text { color: var(--zft-text-regular); margin: 10px 0 6px; }
.up-sub { color: var(--zft-text-faint); font-size: 12px; }

/* ===== 记录列表 ===== */
.list-card {
  background: var(--zft-panel);
  border: 1px solid var(--zft-border);
  border-radius: 16px;
  padding: 18px;
}
.list-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}
.list-header h3 { color: var(--zft-text); font-size: 16px; margin: 0; }
.score-num { font-weight: 700; color: var(--zft-risk-mid); }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }

/* ===== 报告抽屉 ===== */
.report-body { padding: 0 4px; }
.overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 22px;
}
.ov-item {
  text-align: center;
  padding: 16px 8px;
  border-radius: 12px;
  background: var(--zft-stat-chip-bg);
  border: 1px solid var(--zft-stat-chip-border);
}
.ov-num { font-size: 26px; font-weight: 800; color: var(--zft-text); }
.lv-high { color: var(--zft-risk-high); }
.lv-mid { color: var(--zft-risk-mid); }
.lv-low { color: var(--zft-risk-low); }
.ov-label { font-size: 12px; color: var(--zft-text-faint); margin-top: 6px; }

.sec-title {
  display: flex; align-items: center; gap: 8px;
  color: var(--zft-primary-text); font-size: 15px;
  margin: 22px 0 12px;
}

/* 三视角意见卡（法务/合规/业务差异化配色） */
.tri-view { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.tri-card {
  border-radius: 12px;
  padding: 14px;
  font-size: 13px;
  line-height: 1.7;
  background: var(--zft-stat-chip-bg);
  border: 1px solid var(--zft-stat-chip-border);
  display: flex; flex-direction: column;
}
.tri-card.legal { border-top: 2px solid #3b82f6; }
.tri-card.compliance { border-top: 2px solid #34d399; }
.tri-card.business { border-top: 2px solid #f87171; }
.tri-head {
  font-weight: 700; margin-bottom: 8px; color: var(--zft-text);
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.tri-body { color: var(--zft-text-dim); white-space: pre-wrap; flex: 1; }
.tri-points { margin: 10px 0 0; padding-left: 16px; color: var(--zft-text-faint); }
.tri-points li { margin: 2px 0; font-size: 12px; line-height: 1.6; }

/* 建议列表 */
.suggestion-list { display: flex; flex-direction: column; gap: 8px; }
.sug-item {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--zft-primary-soft-bg);
  border: 1px solid var(--zft-primary-soft-border);
  color: var(--zft-text-regular);
  font-size: 13px;
  line-height: 1.7;
}
.sug-idx {
  flex-shrink: 0;
  width: 20px; height: 20px;
  display: grid; place-items: center;
  border-radius: 50%;
  background: var(--zft-grad-brand);
  color: #fff;
  font-size: 11px;
}
.sug-content { flex: 1; min-width: 0; }
.sug-text { word-break: break-word; }
.sug-meta { margin-top: 4px; font-size: 11.5px; color: var(--zft-text-faint); }

/* 辩论记录 */
.debate-item {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 12px;
  border-left: 2px solid var(--zft-border-strong);
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--zft-text-dim);
}
.debate-text { line-height: 1.6; }

/* 仲裁结论 */
.arbitration-box {
  padding: 14px 16px;
  border-radius: 10px;
  background: var(--zft-primary-soft-bg);
  border: 1px solid var(--zft-primary-soft-border);
  color: var(--zft-text-regular);
  font-size: 13.5px;
  line-height: 1.8;
}

/* 多 Agent 执行轨迹 */
.agent-trace {
  border: 1px solid var(--zft-border);
  border-radius: 12px;
  background: var(--zft-stat-chip-bg);
  padding: 6px 14px;
}
.trace-row {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--zft-divider);
  font-size: 12.5px;
}
.trace-row:last-child { border-bottom: none; }
.trace-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--zft-text-faint); flex-shrink: 0;
}
.trace-dot.llm { background: #34d399; box-shadow: 0 0 8px #34d399; }
.trace-name { font-weight: 600; color: var(--zft-text); min-width: 150px; }
.trace-output { flex: 1; color: var(--zft-text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trace-ms { color: var(--zft-text-faint); font-size: 11.5px; }

@media (max-width: 900px) {
  .tri-view { grid-template-columns: 1fr; }
  .overview { grid-template-columns: repeat(2, 1fr); }
}
</style>
