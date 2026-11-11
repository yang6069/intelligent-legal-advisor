/**
 * ==================================================================
 * 智法通前端 | HTTP 客户端（Axios 封装）
 * ------------------------------------------------------------------
 * 职责：
 *   1. 统一携带 JWT（Authorization: Bearer <token>）；
 *   2. 统一错误处理：401 自动登出跳登录页；其余错误抛 ElMessage；
 *   3. 响应数据直接解包（调用方拿到业务 JSON）。
 * ==================================================================
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'

const http = axios.create({
  baseURL: '/api',
  timeout: 300000,   // 问答 / 合同审查为 LLM 长任务，5 分钟上限
})

// ---- 请求拦截：注入 JWT ----
http.interceptors.request.use((cfg) => {
  const raw = localStorage.getItem('zft_auth')
  if (raw) {
    try {
      const token = JSON.parse(raw).token
      if (token) cfg.headers.Authorization = `Bearer ${token}`
    } catch { /* 忽略损坏存储 */ }
  }
  return cfg
})

// ---- 响应拦截：错误统一处理 ----
http.interceptors.response.use(
  (resp) => resp.data,
  async (error) => {
    const status = error.response?.status
    let detail = error.response?.data?.detail
    // blob 响应（文件下载）出错时，错误体也是 Blob，需解析后取 detail
    if (error.response?.data instanceof Blob) {
      try {
        const parsed = JSON.parse(await error.response.data.text())
        detail = parsed?.detail
      } catch { /* 非 JSON 错误体，保持默认提示 */ }
    }
    if (status === 401) {
      // 令牌失效：清理登录态并引导重新登录
      localStorage.removeItem('zft_auth')
      ElMessage.warning('登录已过期，请重新登录')
      router.push('/login')
    } else {
      ElMessage.error(typeof detail === 'string' ? detail : `请求失败（${status ?? '网络错误'}）`)
    }
    return Promise.reject(error)
  },
)

/** GET 便捷方法（泛型返回业务数据） */
export function get<T = any>(url: string, params?: object): Promise<T> {
  return http.get(url, { params }) as Promise<T>
}

/** POST 便捷方法 */
export function post<T = any>(url: string, data?: object): Promise<T> {
  return http.post(url, data) as Promise<T>
}

/** PUT 便捷方法 */
export function put<T = any>(url: string, data?: object): Promise<T> {
  return http.put(url, data) as Promise<T>
}

/** DELETE 便捷方法 */
export function del<T = any>(url: string): Promise<T> {
  return http.delete(url) as Promise<T>
}

/**
 * 文件下载（blob 响应）：返回二进制 Blob 供触发浏览器保存。
 * 错误体（JSON detail）由响应拦截器解析并提示。
 */
export async function download(url: string, params?: object): Promise<Blob> {
  return (await http.get(url, {
    params,
    responseType: 'blob',
    timeout: 300000,   // 导出涉及 LLM 改写，长超时
  })) as unknown as Blob
}

/**
 * SSE 流式请求（POST + fetch ReadableStream）。
 *
 * 为什么不用 axios：流式分块读取需要原生 fetch 的 body reader。
 * 事件协议见后端 qa.py（meta/citation/token/confidence/done/session）。
 *
 * @param url    接口地址（如 /api/qa/ask/stream）
 * @param body   请求体 JSON
 * @param onEvent (eventType, data) => void 事件回调
 */
export async function postSSE(
  url: string,
  body: object,
  onEvent: (type: string, data: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const raw = localStorage.getItem('zft_auth')
  const token = raw ? (JSON.parse(raw).token || '') : ''

  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`流式请求失败（HTTP ${resp.status}）`)
  }

  // 逐块解析 SSE 文本协议（以空行分隔事件）
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() || ''          // 最后一段可能不完整，留待下一轮
    for (const chunk of chunks) {
      // 解析 event:/data: 两行
      let evt = 'message', data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) evt = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) {
        try { onEvent(evt, JSON.parse(data)) }
        catch { onEvent(evt, data) }
      }
    }
  }
}

export default http
