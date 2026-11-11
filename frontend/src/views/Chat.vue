<!--
  ==================================================================
  智法通前端 | 智能问答页（SSE 流式对话）
  ------------------------------------------------------------------
  职责：
    1. 会话列表（创建/切换/删除，多轮上下文）；
    2. 流式问答：postSSE 消费 meta/citation/token/confidence/
       done/session 事件，逐 token 渲染（打字机效果）；
    3. 引用溯源卡片：法条名称/条文号/相关度；
    4. 置信度徽标 + 响应时延 + 子查询展示（RAG 过程可视化）。
  ==================================================================
-->
<template>
  <div class="chat-page">
    <!-- ============ 左侧会话列表 ============ -->
    <aside class="session-panel">
      <el-button
        class="new-session-btn"
        type="primary"
        :icon="Plus"
        @click="newSession"
      >新建会话</el-button>
      <el-scrollbar class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === activeSessionId }"
          @click="switchSession(s.id)"
        >
          <el-icon><ChatDotRound /></el-icon>
          <div class="session-meta">
            <div class="session-title">{{ s.title }}</div>
            <div class="session-sub">{{ s.msg_count }} 条消息</div>
          </div>
          <el-icon class="del-icon" @click.stop="removeSession(s.id)"><Delete /></el-icon>
        </div>
        <el-empty v-if="!sessions.length" description="暂无会话" :image-size="70" />
      </el-scrollbar>
    </aside>

    <!-- ============ 右侧对话区 ============ -->
    <section class="chat-panel">
      <!-- 消息流 -->
      <el-scrollbar ref="scrollRef" class="msg-scroll">
        <div class="msg-list">
          <!-- 欢迎页（无消息时） -->
          <div v-if="!messages.length" class="welcome">
            <div class="welcome-logo">⚖</div>
            <h2>您好，我是智法通 AI 法律顾问</h2>
            <p>基于 RAG 检索增强生成 · 引用真实法条 · 置信度自评估</p>
            <div class="quick-asks">
              <div
                v-for="q in dict.quickQuestions"
                :key="q"
                class="quick-chip"
                @click="ask(q)"
              >{{ q }}</div>
            </div>
          </div>

          <!-- 消息气泡 -->
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="msg-row"
            :class="m.role"
          >
            <div class="avatar">{{ m.role === 'user' ? '我' : '⚖' }}</div>
            <div class="bubble">
              <div class="bubble-text"><!-- 流式末条回复：检索期显示提示文案，token 到达后逐字渲染 --><template
                  v-if="isStreamingTail(m, i) && !m.content"
                >正在检索法律资料并组织回答</template>{{ m.content }}<span
                  v-if="isStreamingTail(m, i)"
                  class="cursor-blink"
                >▍</span>
              </div>

              <!-- 引用溯源（仅 assistant 且有引用） -->
              <div v-if="m.citations?.length" class="citations">
                <div class="cite-title">
                  <el-icon><Collection /></el-icon> 引用法条（{{ m.citations.length }}）
                </div>
                <div v-for="(c, j) in m.citations" :key="j" class="cite-card">
                  <el-tag size="small" :type="citeTagType(j)" effect="dark">
                    {{ c.doc_type_label || c.doc_type || '法条' }}
                  </el-tag>
                  <span class="cite-name">{{ c.title || c.law_name }}</span>
                  <span v-if="c.score" class="cite-score">
                    相关度 {{ (Number(c.score) * 100).toFixed(0) }}%
                  </span>
                </div>
              </div>

              <!-- 置信度/时延/子查询元信息 -->
              <div v-if="m.role === 'assistant' && m.confidence != null" class="msg-meta">
                <el-tag
                  size="small"
                  :type="m.confidence >= 0.8 ? 'success' : m.confidence >= 0.5 ? 'warning' : 'danger'"
                  effect="plain"
                >置信度 {{ (m.confidence * 100).toFixed(0) }}%</el-tag>
                <span v-if="m.latency_ms" class="meta-item">耗时 {{ (m.latency_ms / 1000).toFixed(1) }}s</span>
                <span v-if="m.sub_queries?.length" class="meta-item">
                  子查询：{{ m.sub_queries.join(' / ') }}
                </span>
              </div>
            </div>
          </div>

          <!-- 说明：流式等待/生成中的指示统一由上方消息气泡内的
               提示文案 + 打字光标承担（isStreamingTail），
               不再渲染独立占位气泡，避免出现多个空气泡 -->
        </div>
      </el-scrollbar>

      <!-- 配额状态条（仅普通用户显示；管理员/法务无限制，无需展示） -->
      <div
        v-if="showQuotaBar"
        class="quota-bar"
        :class="{ 'quota-warn': isFreeAndLow }"
      >
        <el-icon><Wallet /></el-icon>
        <span v-if="quota.total_balance > 0" class="quota-paid">
          付费余额 {{ quota.total_balance }} 次（问答+审查共用）
          <span v-if="quota.plan_expire_at">· 至 {{ formatExpire(quota.plan_expire_at) }}</span>
        </span>
        <template v-else>
          <span>今日问答：{{ quota.qa?.daily_remaining ?? 20 }} / {{ quota.qa?.daily_limit ?? 20 }} 次</span>
          <span v-if="isFreeAndLow" class="quota-urgent">
            ⏰ 仅剩 {{ quota.qa?.daily_remaining }} 次，用完今日无法再提问
          </span>
        </template>
        <el-button link type="primary" size="small" @click="$router.push('/subscription')">
          去升级套餐 →
        </el-button>
      </div>

      <!-- 输入区 -->
      <div class="input-bar">
        <el-switch
          v-model="enableGraph"
          active-text="图谱检索"
          inactive-text=""
          class="graph-switch"
        />
        <el-input
          v-model="question"
          type="textarea"
          :rows="2"
          resize="none"
          maxlength="2000"
          show-word-limit
          placeholder="请输入您的法律问题，例如：劳动合同中约定的违约金上限是多少？"
          @keydown.enter.exact.prevent="ask(question)"
        />
        <el-button
          class="send-btn"
          type="primary"
          :icon="Promotion"
          :loading="streaming"
          @click="ask(question)"
        >发送</el-button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'Chat' })
/**
 * 问答页逻辑：
 *   - 加载会话列表 -> 切换会话时拉取历史消息；
 *   - ask(): postSSE 流式调用，token 事件增量拼接，
 *     done 后挂载引用/置信度元数据；session 事件更新会话 ID。
 */
import { nextTick, onMounted, onActivated, onUnmounted, reactive, ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { ElScrollbar } from 'element-plus'
import { Plus, Promotion, Delete, Wallet } from '@element-plus/icons-vue'
import { get, post, del } from '../api/client'
import { useDictStore } from '../stores/dict'
import { useAutoRefresh } from '../composables/useAutoRefresh'
import { useAuthStore } from '../stores/auth'
import { useTaskStore } from '../stores/task'

/** 全局后台任务 store（让问答 SSE 在组件切走后继续跑） */
const taskStore = useTaskStore()

/**
 * 纯文本兜底清洗：剥离历史消息中可能残留的 Markdown 符号
 * （新回答由后端流式清洗保证纯文本；老数据在渲染层兜底）。
 */
function stripMarkdown(text: string): string {
  if (!text) return ''
  return String(text)
    .replace(/```[a-zA-Z0-9_+-]*\n?/g, '')
    .replace(/^[ \t]{0,3}#{1,6}[ \t]*/gm, '')
    .replace(/\*{1,3}([^*\n]+?)\*{1,3}/g, '$1')
    .replace(/`([^`\n]+?)`/g, '$1')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^[ \t]{0,3}[-*+][ \t]+/gm, '· ')
    .replace(/\n{3,}/g, '\n\n')
}

/** 消息结构（user / assistant 双角色） */interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  citations?: any[]
  confidence?: number | null
  latency_ms?: number
  sub_queries?: string[]
}

const sessions = ref<any[]>([])
const activeSessionId = ref<number | null>(null)
const messages = reactive<ChatMsg[]>([])
const question = ref('')
const streaming = ref(false)
const enableGraph = ref(true)   // GraphRAG 图谱关联检索开关
const scrollRef = ref<InstanceType<typeof ElScrollbar>>()

/** 当前问答后台任务 ID（用于切路由再回来时恢复订阅） */
const currentQATaskId = ref<string | null>(null)
/** 订阅取消句柄（切走页面时 unsubscribe） */
let _qaUnsubscribe: (() => void) | null = null

/** 配额状态（用于底部展示 + 配额耗尽处理） */
const auth = useAuthStore()
const quota = reactive<any>({})
/** 配额条仅普通用户可见（管理员/法务无限制，没有升级入口） */
const showQuotaBar = computed(() => auth.user?.role === 'user')
/** 免费版且今日剩余 <= 10 次 -> 红色警示，制造升级紧迫感 */
const isFreeAndLow = computed(() => {
  if (quota.plan_type !== 'free' || quota.total_balance > 0) return false
  const rem = quota.qa?.daily_remaining
  return rem != null && rem !== -1 && rem <= 10
})

async function loadQuota() {
  try {
    const data = await get<any>('/quota/status')
    Object.assign(quota, data)
  } catch { /* 配额接口不可用时静默降级 */ }
}

function formatExpire(t: any) {
  if (!t) return ''
  const d = new Date(t)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 业务字典（快捷提问等后端下发，前端零硬编码业务数据） */
const dict = useDictStore()

/**
 * 会话列表自动刷新（数据变化自动上屏，无需手动刷新浏览器）：
 * 定时 + 窗口聚焦/切回可见时静默拉取；流式回答进行中跳过，
 * 避免与消息流渲染互相干扰。
 */
useAutoRefresh(async () => {
  if (streaming.value) return
  const data = await get<any>('/qa/sessions')
  sessions.value = data.items || []
}, 20000)

onMounted(() => {
  dict.ensure()   // 快捷提问等字典后端下发
  loadSessions()
  loadQuota()     // 加载配额状态
  _tryResumeQATask()   // 切路由再回来时，恢复正在后台跑的问答任务
})

/** keep-alive 重新激活时也要恢复（组件不会被卸载→mounted 只跑首次） */
onActivated(() => {
  _tryResumeQATask()
})

onUnmounted(() => {
  // 切走页面时 unsubscribe（任务仍在 store 后台跑，继续累积 tokens）
  if (_qaUnsubscribe) {
    _qaUnsubscribe()
    _qaUnsubscribe = null
  }
})

/**
 * 页面恢复时：如果 store 里有正在跑的问答任务，把它接回 UI。
 *  - 恢复 messages 列表（user + reply）；
 *  - 重新订阅 store 的 token 事件，继续实时增量渲染。
 */
function _tryResumeQATask() {
  // 从 store 读全局激活任务 ID（Pinia 单例，切路由不丢）
  const taskId = currentQATaskId.value || taskStore.activeQATaskId
  if (!taskId) return
  currentQATaskId.value = taskId     // 组件自己记一份，后面 ask() 的 done 回调清它也方便
  const task = taskStore.getQA(taskId)
  if (!task) { currentQATaskId.value = null; taskStore.clearActiveQA(taskId); return }

  streaming.value = task.status === 'running'

  // 确保 messages 里有对应的 user + assistant 两条
  let userMsg = messages.find(m => m.role === 'user' && m.content === task.question)
  let reply: ChatMsg | null = null
  if (!userMsg) {
    messages.push({ role: 'user', content: task.question })
    reply = reactive({
      role: 'assistant' as const, content: task.content,
      citations: [...task.citations], confidence: task.confidence,
      latency_ms: task.latency_ms ?? undefined, sub_queries: [...task.sub_queries],
    })
    messages.push(reply)
  } else {
    // 已有 userMsg，找最后一条 assistant
    const lastIdx = messages.lastIndexOf(userMsg) + 1
    if (messages[lastIdx]?.role === 'assistant') {
      reply = messages[lastIdx] as ChatMsg
      reply.content = task.content
      reply.citations = [...task.citations]
      reply.sub_queries = [...task.sub_queries]
      reply.confidence = task.confidence
      reply.latency_ms = task.latency_ms ?? undefined
    }
  }
  if (task.session_id != null) activeSessionId.value = task.session_id

  // 订阅事件（_tryResumeQATask 每次 mount 都要重新订阅，因为 unmount 时 unsubscribe 了）
  _qaUnsubscribe = taskStore.subscribeQA(task.taskId, (evt) => {
    if (!reply) return
    switch (evt.type) {
      case '__reply__':
        // 首次订阅的回放事件：把已有进度一次性灌进 UI
        reply.content = evt.data.content
        reply.citations = [...evt.data.citations]
        reply.sub_queries = [...evt.data.sub_queries]
        reply.confidence = evt.data.confidence
        reply.latency_ms = evt.data.latency_ms
        break
      case 'meta':
        reply.sub_queries = evt.data.sub_queries || []
        break
      case 'citation':
        reply.citations!.push(evt.data)
        break
      case 'token':
        reply.content += (evt.data.content || '')
        scrollToBottom()
        break
      case 'confidence':
        reply.confidence = Number(evt.data.value ?? 0)
        break
      case 'done':
        reply.latency_ms = evt.data.latency_ms || 0
        streaming.value = false
        loadQuota()
        break
      case 'session':
        activeSessionId.value = evt.data.session_id
        break
      case 'error':
        streaming.value = false
        break
    }
  })
  scrollToBottom()
}

/** 加载会话列表（本人） */
async function loadSessions() {
  const data = await get<any>('/qa/sessions')
  sessions.value = data.items || []
  if (sessions.value.length && activeSessionId.value == null) {
    switchSession(sessions.value[0].id)
  }
}

/** 新建会话（清空消息区） */
function newSession() {
  activeSessionId.value = null
  messages.length = 0
}

/** 切换会话：拉取历史消息（含引用/置信度；文本做纯文本兜底清洗） */
async function switchSession(id: number) {
  // 1. 先终止正在进行的流式回答（keep-alive 下组件不卸载，旧 fetch 还在跑）
  if (streaming.value && currentQATaskId.value) {
    taskStore.cancelQA(currentQATaskId.value)
    currentQATaskId.value = null
    streaming.value = false
    if (_qaUnsubscribe) { _qaUnsubscribe(); _qaUnsubscribe = null }
  }
  // 2. 强制关 streaming，防止历史消息末尾误闪光标
  streaming.value = false
  activeSessionId.value = id
  const data = await get<any>(`/qa/sessions/${id}/messages`)
  messages.length = 0
  for (const row of data.items || []) {
    messages.push({
      role: row.role,
      content: row.role === 'assistant' ? stripMarkdown(row.content) : row.content,
      citations: safeParse(row.citations),
      confidence: row.confidence,
      latency_ms: row.latency_ms,
      sub_queries: safeParse(row.sub_queries),
    })
  }
  scrollToBottom()
}

/** 删除会话（确认后删除并刷新列表） */
async function removeSession(id: number) {
  await ElMessageBox.confirm('删除该会话及全部消息？', '提示', { type: 'warning' })
  await del(`/qa/sessions/${id}`)
  ElMessage.success('会话已删除')
  if (activeSessionId.value === id) newSession()
  loadSessions()
}

/**
 * 发送问题（SSE 流式）。
 *
 * 事件处理：
 *   - meta: 子查询分解结果（展示 RAG 过程）
 *   - citation: 法条引用（流前置推送，先看依据再看答案）
 *   - token: 增量文本 -> 实时拼接渲染
 *   - confidence / done: 元数据补充
 *   - session: 会话 ID（首次提问自动创建会话）
 */
async function ask(q: string) {
  const text = (q || '').trim()
  if (!text || streaming.value) return
  question.value = ''
  messages.push({ role: 'user', content: text })

  const reply: ChatMsg = reactive({
    role: 'assistant', content: '', citations: [],
    confidence: null, sub_queries: [],
  })
  messages.push(reply)
  streaming.value = true
  scrollToBottom()

  // 关键改动（2026-09-24）：把 SSE 流式任务从组件内搬到 Pinia store 后台。
  //   - 任务在 store 里继续跑 fetch，组件切走再回来 = 从 store 恢复订阅；
  //   - 流式进度（content / citations / sub_queries）全程在 store 累积；
  //   - 原组件内 postSSE() 被彻底移除，不再受组件生命周期约束。
  const task = taskStore.startQAStream({
    question: text,
    session_id: activeSessionId.value,
    enable_graph: enableGraph.value,
  })
  currentQATaskId.value = task.taskId

  _qaUnsubscribe = taskStore.subscribeQA(task.taskId, (evt) => {
    switch (evt.type) {
      case '__reply__':
        reply.content = evt.data.content
        reply.citations = [...evt.data.citations]
        reply.sub_queries = [...evt.data.sub_queries]
        reply.confidence = evt.data.confidence
        reply.latency_ms = evt.data.latency_ms ?? undefined
        break
      case 'meta':
        reply.sub_queries = evt.data.sub_queries || []
        break
      case 'citation':
        reply.citations!.push(evt.data)
        break
      case 'token':
        reply.content += (evt.data.content || '')
        scrollToBottom()
        break
      case 'confidence':
        reply.confidence = Number(evt.data.value ?? 0)
        break
      case 'done':
        reply.latency_ms = evt.data.latency_ms || 0
        streaming.value = false
        loadQuota()
        loadSessions()
        // 任务结束 -> 清 store 里的激活标记（下次 mount 不会误恢复）
        if (currentQATaskId.value) taskStore.clearActiveQA(currentQATaskId.value)
        break
      case 'session':
        activeSessionId.value = evt.data.session_id
        break
      case 'error':
        if (evt.data.message === 'QUOTA_EXHAUSTED') {
          // 配额耗尽 -> 友好弹窗引导去购买
          streaming.value = false
          messages.splice(messages.length - 2, 2)
          ElMessageBox.confirm(
            '问答次数已用完，免费版每天 50 次。\n是否前往「我的套餐」购买更多次数？',
            '问答次数已用完',
            {
              confirmButtonText: '去购买',
              cancelButtonText: '稍后',
              type: 'warning',
            }
          ).then(() => { auth.$router?.push('/subscription') }).catch(() => {})
          loadQuota()
          if (currentQATaskId.value) taskStore.clearActiveQA(currentQATaskId.value)
          return
        }
        if (evt.data.message === 'ABORTED') {
          // 主动取消（切会话/切页）-> 静默忽略，不显示错误气泡
          streaming.value = false
          if (currentQATaskId.value) taskStore.clearActiveQA(currentQATaskId.value)
          return
        }
        reply.content = `回答生成失败：${evt.data.message || '网络异常'}`
        streaming.value = false
        if (currentQATaskId.value) taskStore.clearActiveQA(currentQATaskId.value)
        break
    }
  })
}

/** 滚动到底部（流式渲染跟随） */
function scrollToBottom() {
  nextTick(() => {
    scrollRef.value?.setScrollTop(999999)
  })
}

/**
 * 是否为流式中的末条 AI 回复（气泡内提示文案与打字光标的渲染依据）。
 * 仅末条 assistant 消息在生成期间显示，历史消息不受影响。
 */
function isStreamingTail(m: ChatMsg, i: number): boolean {
  return streaming.value && m.role === 'assistant' && i === messages.length - 1
}

/** DB 中 JSON 字符串安全反序列化 */
function safeParse(v: any): any[] {
  if (!v) return []
  if (Array.isArray(v)) return v
  try { return JSON.parse(v) } catch { return [] }
}

/** 引用卡片标签色轮换（视觉层次） */
function citeTagType(i: number): string {
  return (['primary', 'success', 'warning', 'danger'] as const)[i % 4]
}
</script>

<style scoped>
/* ===== 双栏：会话列表 + 对话区（双主题变量驱动） ===== */
.chat-page {
  display: flex;
  gap: 16px;
  height: calc(100vh - 100px);
  min-height: 520px;
}

/* ===== 会话面板 ===== */
.session-panel {
  width: 250px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--zft-panel);
  border: 1px solid var(--zft-border);
  border-radius: 16px;
  padding: 14px;
}
.new-session-btn { border-radius: 10px; }
.session-list { flex: 1; }
.session-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  color: var(--zft-text-dim);
  margin-bottom: 6px;
  transition: all 0.2s;
}
.session-item:hover { background: var(--zft-primary-soft-bg); color: var(--zft-text); }
.session-item.active {
  background: var(--zft-menu-active-bg);
  color: var(--zft-menu-active-text);
}
.session-meta { flex: 1; min-width: 0; }
.session-title {
  font-size: 13px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.session-sub { font-size: 11px; opacity: 0.75; }
.del-icon { opacity: 0; transition: opacity 0.2s; }
.session-item:hover .del-icon { opacity: 1; }
.del-icon:hover { color: #ef4444; }

/* ===== 对话面板 ===== */
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--zft-panel);
  border: 1px solid var(--zft-border);
  border-radius: 16px;
  overflow: hidden;
}
.msg-scroll { flex: 1; }
.msg-list { padding: 22px; }

/* ===== 欢迎引导 ===== */
.welcome { text-align: center; padding: 60px 20px; }
.welcome-logo {
  width: 72px; height: 72px; margin: 0 auto 18px;
  display: grid; place-items: center;
  font-size: 36px; border-radius: 20px;
  background: var(--zft-grad-brand);
  box-shadow: 0 8px 30px rgba(99, 102, 241, 0.4);
  color: #fff;
}
.welcome h2 { color: var(--zft-text); margin-bottom: 8px; }
.welcome p { color: var(--zft-text-faint); font-size: 13px; margin-bottom: 26px; }
.quick-asks { display: flex; flex-direction: column; gap: 10px; max-width: 460px; margin: 0 auto; }
.quick-chip {
  padding: 12px 18px;
  border-radius: 12px;
  border: 1px solid var(--zft-primary-soft-border);
  background: var(--zft-primary-soft-bg);
  color: var(--zft-primary-text);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.quick-chip:hover {
  background: rgba(59, 130, 246, 0.18);
  transform: translateY(-1px);
}

/* ===== 消息气泡 ===== */
.msg-row {
  display: flex; gap: 12px;
  margin-bottom: 20px;
  max-width: 86%;
}
.msg-row.user { margin-left: auto; flex-direction: row-reverse; }
.avatar {
  width: 36px; height: 36px; flex-shrink: 0;
  display: grid; place-items: center;
  border-radius: 10px;
  font-size: 16px;
}
.msg-row.user .avatar {
  background: var(--zft-stat-chip-bg);
  color: var(--zft-text);
  border: 1px solid var(--zft-border-strong);
}
.msg-row.assistant .avatar {
  background: var(--zft-grad-brand);
  color: #fff;
}
.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  color: var(--zft-text-regular);
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
/* 用户气泡：品牌渐变实底（白字在两套主题下对比度均最高） */
.msg-row.user .bubble {
  background: var(--zft-grad-brand);
  border-color: transparent;
  color: #fff;
}
.bubble-text { font-size: 14px; }

/* 光标闪烁（流式生成中） */
.cursor-blink { animation: blink 0.9s infinite; color: var(--zft-primary-text); font-size: 16px; }
@keyframes blink { 50% { opacity: 0; } }

/* ===== 引用卡片 ===== */
.citations { margin-top: 12px; border-top: 1px dashed var(--zft-divider); padding-top: 10px; }
.cite-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--zft-primary-text); margin-bottom: 8px;
}
.cite-card {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--zft-cite-bg);
  border: 1px solid var(--zft-border);
  margin-bottom: 6px;
  font-size: 12.5px;
}
.cite-name { color: var(--zft-text-regular); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cite-score { color: var(--zft-text-faint); font-size: 11px; }

/* ===== 元信息 ===== */
.msg-meta {
  margin-top: 10px;
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  font-size: 11.5px;
}
.meta-item { color: var(--zft-text-faint); }

/* ===== 配额状态条 ===== */
.quota-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 18px; border-top: 1px solid var(--zft-border);
  background: linear-gradient(90deg, var(--zft-panel-deep), transparent);
  font-size: 12px; color: var(--zft-text-faint);
}
.quota-bar .quota-paid { color: var(--zft-primary-text); font-weight: 500; }
.quota-bar .el-button { margin-left: auto; }
/* 低额度警示：红底闪烁呼吸效果，刺激升级 */
.quota-bar.quota-warn {
  background: linear-gradient(90deg, rgba(239, 68, 68, 0.15), transparent);
  border-top-color: rgba(239, 68, 68, 0.35);
  color: #ef4444;
  animation: quota-pulse 2s ease-in-out infinite;
}
.quota-bar .quota-urgent { color: #ef4444; font-weight: 600; }
@keyframes quota-pulse {
  0%, 100% { background: linear-gradient(90deg, rgba(239, 68, 68, 0.18), transparent); }
  50% { background: linear-gradient(90deg, rgba(239, 68, 68, 0.32), transparent); }
}

/* ===== 输入区 ===== */
.input-bar {
  display: flex; align-items: flex-end; gap: 12px;
  padding: 14px 18px;
  border-top: 1px solid var(--zft-border);
  background: var(--zft-panel-deep);
}
.graph-switch { margin-bottom: 14px; }
.input-bar :deep(.el-textarea__inner) {
  border-radius: 12px;
  background: var(--zft-input-bg);
  color: var(--zft-text);
}
.send-btn {
  height: 54px;
  border-radius: 12px;
  padding: 0 26px;
  font-weight: 600;
  background: var(--zft-grad-brand);
  border: none;
}
.send-btn:hover { filter: brightness(1.12); }

@media (max-width: 768px) {
  .session-panel { display: none; }
  .msg-row { max-width: 100%; }
}
</style>
