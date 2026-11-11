/**
 * ==================================================================
 * 智法通前端 | 后台长任务管理（Pinia Store）
 * ------------------------------------------------------------------
 * 为什么需要这个 store：
 *   Vue 组件内的 async 任务（fetch / postSSE / await post）都挂在组件实例上。
 *   一旦用户切路由，组件 unmount，里面正在跑的 SSE fetch / 同步 await 就跟着丢了。
 *
 *   本 store 把"切路由不能丢"的长任务从组件搬到全局单例里：
 *     1. Chat 的问答 SSE 流 -> startQAStream()，后台 fetch 继续跑；
 *     2. Contract 的合同审查 -> startContractReview()，后台 post 继续等；
 *     3. 页面切走再回来 -> 组件从 store 里读取当前任务状态，实时恢复渲染。
 *
 *   切路由不再中断用户体验：问答流式输出、合同审查进度都能在后台"静默"
 *   继续，用户想逛哪里逛哪里，结果一回来就能看。
 *
 * 任务生命周期：
 *   pending（刚创建） -> running（fetch 中） -> done（成功） | error（失败）
 *   组件可以通过 subscribe() 订阅某个任务的实时状态变化。
 * ==================================================================
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// ==================================================================
// 类型定义
// ==================================================================

/** 单条问答事件（Chat.vue 回显用） */
export interface QAReplyEvent {
  type: 'meta' | 'citation' | 'token' | 'confidence' | 'done' | 'session' | 'error'
  data: any
}

/** 问答流式任务（对应一次 /api/qa/ask/stream） */
export interface QAStreamTask {
  taskId: string                    // 任务唯一 ID（时间戳+随机数）
  status: 'pending' | 'running' | 'done' | 'error'
  question: string                  // 原始问题
  session_id: number | null         // 会话 ID（后端返回的）
  enable_graph: boolean
  // ---- 实时增量 ----
  sub_queries: string[]
  citations: any[]
  content: string                   // 累计文本
  confidence: number | null
  latency_ms: number | null
  error_msg: string | null
  // ---- 内部 ----
  _cancel?: () => void              // 取消 fetch 的 handle（AbortController.abort）
  _resolveHandlers?: Array<(evt: QAReplyEvent) => void>  // 外部（Chat.vue）的事件订阅
}

/** 合同审查任务（对应一次 /api/contract/review-text） */
export interface ContractReviewTask {
  taskId: string
  status: 'pending' | 'running' | 'done' | 'error'
  contract_id: number | null        // 后端受理后返回的 ID
  title: string
  error_msg: string | null
}

export type AnyTask = QAStreamTask | ContractReviewTask

// ==================================================================
// Store 主体
// ==================================================================
export const useTaskStore = defineStore('task', () => {
  /** 所有进行中 / 刚完成的任务（用 ref 数组，Pinia 全局单例） */
  const qaTasks = ref<Map<string, QAStreamTask>>(new Map())
  const contractTasks = ref<Map<string, ContractReviewTask>>(new Map())

  /**
   * 当前"激活中"的问答任务 ID（用于 Chat.vue 切路由回来时恢复）。
   * 为什么存 store 不存组件：组件 unmount 后 ref 值会被 GC，而 Pinia store 是全局单例，
   * 页面来来回回 store 里的这条引用永远在，只要浏览器没关就行。
   */
  const activeQATaskId = ref<string | null>(null)
  const activeContractTaskId = ref<string | null>(null)

  /** 多少任务还在跑（给 MainLayout 右上角胶囊提示用） */
  const runningCount = computed(() => {
    let n = 0
    qaTasks.value.forEach(t => { if (t.status === 'running') n++ })
    contractTasks.value.forEach(t => { if (t.status === 'running') n++ })
    return n
  })

  /** 清理已完成/失败的老任务（避免 Map 无限制堆积） */
  const MAX_KEEP = 20
  function pruneFinished() {
    const trimMap = <T extends AnyTask>(m: Map<string, T>) => {
      const finished = Array.from(m.values())
        .filter(t => t.status !== 'running')
        .sort((a, b) => String(b.taskId).localeCompare(String(a.taskId)))
        .slice(MAX_KEEP)
      const keepIds = new Set(finished.map(t => t.taskId))
      m.forEach((_, k) => { if (!keepIds.has(k) && m.get(k)?.status !== 'running') m.delete(k) })
    }
    trimMap(qaTasks.value as any)
    trimMap(contractTasks.value as any)
  }

  // ==================================================================
  // 问答 SSE 流
  // ==================================================================

  /**
   * 启动一次问答流式任务（丢到 store 后台，与组件解耦）。
   *
   * @returns 任务对象（reactive），组件可拿到 taskId 然后随时来读它
   */
  function startQAStream(params: {
    question: string
    session_id: number | null
    enable_graph: boolean
  }): QAStreamTask {
    const taskId = 'qa_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
    const task: QAStreamTask = {
      taskId,
      status: 'pending',
      question: params.question,
      session_id: params.session_id,
      enable_graph: params.enable_graph,
      sub_queries: [],
      citations: [],
      content: '',
      confidence: null,
      latency_ms: null,
      error_msg: null,
      _resolveHandlers: [],
    }
    qaTasks.value.set(taskId, task)
    activeQATaskId.value = taskId    // 标记为当前激活任务（跨路由保留）

    // 异步启动 fetch（后台执行，不阻塞 store 返回）
    const ac = new AbortController()
    task._cancel = () => ac.abort()
    task.status = 'running'
    _runQAStream(task, params.question, params.session_id, params.enable_graph, ac.signal)

    // 顺手清理老任务
    if (qaTasks.value.size > MAX_KEEP * 2) pruneFinished()
    return task
  }

  /** 订阅某个任务的实时事件（Chat.vue mount 时调用，切走时 unsubscribe） */
  function subscribeQA(taskId: string, handler: (evt: QAReplyEvent) => void): () => void {
    const task = qaTasks.value.get(taskId)
    if (!task) return () => {}
    task._resolveHandlers = task._resolveHandlers || []
    task._resolveHandlers.push(handler)
    // 立刻把已累计的历史事件回放一次（让新挂载的组件秒见内容）
    if (task.content || task.citations.length || task.sub_queries.length) {
      try {
        handler({ type: '__reply__', data: {
          content: task.content,
          citations: task.citations,
          sub_queries: task.sub_queries,
          confidence: task.confidence,
          latency_ms: task.latency_ms,
        }})
      } catch {}
    }
    return () => {
      task._resolveHandlers = (task._resolveHandlers || []).filter(h => h !== handler)
    }
  }

  /** 获取某个 QA 任务 */
  function getQA(taskId: string | null | undefined): QAStreamTask | undefined {
    if (!taskId) return undefined
    return qaTasks.value.get(taskId)
  }

  /** 内部：真正发起 fetch + 解析 SSE */
  async function _runQAStream(
    task: QAStreamTask,
    question: string,
    session_id: number | null,
    enable_graph: boolean,
    signal: AbortSignal,
  ) {
    const raw = localStorage.getItem('zft_auth')
    const token = raw ? (JSON.parse(raw).token || '') : ''
    const emit = (evt: QAReplyEvent) => {
      try { task._resolveHandlers?.forEach(h => h(evt)) } catch {}
    }
    try {
      const resp = await fetch('/api/qa/ask/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ question, session_id, enable_graph }),
        signal,
      })
      if (!resp.ok || !resp.body) {
        // 429 配额耗尽 —— 带特殊标识给组件弹升级弹窗
        if (resp.status === 429) {
          task.status = 'error'
          task.error_msg = 'QUOTA_EXHAUSTED'
          emit({ type: 'error', data: { message: 'QUOTA_EXHAUSTED' } })
          return
        }
        throw new Error(`HTTP ${resp.status}`)
      }
      // 流式解析（和 api/client.ts 的 postSSE 逻辑完全一致）
      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      for (;;) {
        if (signal.aborted) break
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
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
          try { parsed = JSON.parse(data) } catch { parsed = data }
          switch (evt) {
            case 'meta':
              task.sub_queries = parsed.sub_queries || []
              emit({ type: 'meta', data: parsed })
              break
            case 'citation':
              task.citations.push(parsed)
              emit({ type: 'citation', data: parsed })
              break
            case 'token':
              task.content += (parsed.content || '')
              emit({ type: 'token', data: parsed })
              break
            case 'confidence':
              task.confidence = Number(parsed.value ?? 0)
              emit({ type: 'confidence', data: parsed })
              break
            case 'done':
              task.latency_ms = parsed.latency_ms || 0
              emit({ type: 'done', data: parsed })
              break
            case 'session':
              task.session_id = parsed.session_id
              emit({ type: 'session', data: parsed })
              break
            case 'error':
              task.error_msg = parsed.message || '流式异常'
              emit({ type: 'error', data: parsed })
              break
          }
        }
      }
      if (signal.aborted) {
        task.status = 'error'
        task.error_msg = 'ABORTED'
        emit({ type: 'error', data: { message: 'ABORTED' } })
      } else {
        task.status = 'done'
      }
    } catch (e: any) {
      if (e?.name === 'AbortError' || signal.aborted) {
        task.status = 'error'
        task.error_msg = 'ABORTED'
      } else {
        task.status = 'error'
        task.error_msg = e?.message || '网络异常'
        emit({ type: 'error', data: { message: task.error_msg } })
      }
    }
  }

  // ==================================================================
  // 合同审查（同步长请求搬到后台）
  // ==================================================================

  function startContractReview(params: {
    title: string
    contract_text: string
  }): ContractReviewTask {
    const taskId = 'review_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
    const task: ContractReviewTask = {
      taskId, status: 'pending',
      contract_id: null, title: params.title,
      error_msg: null,
    }
    contractTasks.value.set(taskId, task)
    task.status = 'running'
    activeContractTaskId.value = taskId
    _runContractReview(task, params.title, params.contract_text)
    if (contractTasks.value.size > MAX_KEEP * 2) pruneFinished()
    return task
  }

  function getContract(taskId: string | null | undefined): ContractReviewTask | undefined {
    if (!taskId) return undefined
    return contractTasks.value.get(taskId)
  }

  /**
   * 登记一个"后端已在跑"的审查任务（组件已自己发过 review-text / upload 请求）。
   * 与 startContractReview 的区别：不发请求，只记录 contract_id 供切页恢复 SSE。
   * 若误用 startContractReview，会对后端再发一次空文本请求（400）。
   */
  function registerContractReview(params: {
    contract_id: number
    title: string
  }): ContractReviewTask {
    const taskId = 'review_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
    const task: ContractReviewTask = {
      taskId, status: 'running',
      contract_id: params.contract_id, title: params.title,
      error_msg: null,
    }
    contractTasks.value.set(taskId, task)
    activeContractTaskId.value = taskId
    if (contractTasks.value.size > MAX_KEEP * 2) pruneFinished()
    return task
  }

  function removeContract(taskId: string) {
    contractTasks.value.delete(taskId)
  }

  async function _runContractReview(
    task: ContractReviewTask,
    title: string,
    contract_text: string,
  ) {
    const raw = localStorage.getItem('zft_auth')
    const token = raw ? (JSON.parse(raw).token || '') : ''
    try {
      const resp = await fetch('/api/contract/review-text', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ title, contract_text }),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const json = await resp.json()
      task.contract_id = json.contract_id
      task.status = 'done'
    } catch (e: any) {
      task.status = 'error'
      task.error_msg = e?.message || '审查请求失败'
    }
  }

  /** 清除已完成的激活标记（组件收到 done / error 事件后调用） */
  function clearActiveQA(taskId: string) {
    if (activeQATaskId.value === taskId) activeQATaskId.value = null
  }
  function clearActiveContract(taskId: string) {
    if (activeContractTaskId.value === taskId) activeContractTaskId.value = null
  }

  /** 取消某个 QA 任务（keep-alive 切页/切会话时中止后台 fetch） */
  function cancelQA(taskId: string) {
    const task = qaTasks.value.get(taskId)
    if (task && task.status === 'running' && task._cancel) {
      task._cancel()
    }
  }

  return {
    qaTasks, contractTasks, runningCount,
    activeQATaskId, activeContractTaskId,
    startQAStream, subscribeQA, getQA, cancelQA,
    startContractReview, registerContractReview, getContract, removeContract,
    clearActiveQA, clearActiveContract,
    pruneFinished,
  }
})
