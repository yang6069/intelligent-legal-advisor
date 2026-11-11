/**
 * ==================================================================
 * 智法通前端 | 页面数据自动刷新组合式函数
 * ------------------------------------------------------------------
 * 职责（需求：数据新增/修改/删除后页面自动更新，无需手动刷新浏览器）：
 *   1. 定时轮询：每 intervalMs 静默刷新一次页面数据（可配 0 关闭）；
 *   2. 窗口聚焦 / 标签页切回可见时立即刷新（跨页操作后回来自动同步）；
 *   3. 节流保护：距上次刷新不足 3s 时不重复触发；
 *   4. 页面隐藏时暂停轮询（省流；回前台由 visibilitychange 补一次）；
 *   5. 组件卸载自动清理定时器与监听。
 *
 * 使用约定：
 *   - refresh 回调应做"静默刷新"（不触发全屏 loading），
 *     由各页面的 silent 参数控制；
 *   - 回调内部自行跳过不该刷新的时机（如问答流式进行中）。
 * ==================================================================
 */
import { onBeforeUnmount, onMounted } from 'vue'

export function useAutoRefresh(
  refresh: () => void | Promise<void>,
  intervalMs: number = 20000,
) {
  let timer: number | null = null
  let lastRun = 0

  /** 带节流的刷新执行（3s 内不重复） */
  async function run(throttled = true) {
    if (throttled && Date.now() - lastRun < 3000) return
    lastRun = Date.now()
    try {
      await refresh()
    } catch { /* 刷新失败静默（下一轮重试），不打扰用户 */ }
  }

  /** 标签页可见性变化：切回可见立即补刷 */
  function onVisibility() {
    if (!document.hidden) run()
  }
  /** 窗口聚焦：立即补刷 */
  function onFocus() {
    run()
  }

  onMounted(() => {
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisibility)
    if (intervalMs > 0) {
      timer = window.setInterval(() => {
        if (!document.hidden) run()
      }, intervalMs)
    }
  })

  onBeforeUnmount(() => {
    if (timer !== null) clearInterval(timer)
    timer = null
    window.removeEventListener('focus', onFocus)
    document.removeEventListener('visibilitychange', onVisibility)
  })
}
