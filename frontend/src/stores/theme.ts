/**
 * ==================================================================
 * 智法通前端 | 主题状态管理（Pinia Store）
 * ------------------------------------------------------------------
 * 职责：
 *   1. 维护全局主题状态（'light' 亮色 / 'dark' 暗色）；
 *   2. 切换时同步增删 <html> 元素的 dark 类
 *      （Element Plus 暗色变量与自定义 CSS 变量均由该类驱动）；
 *   3. 主题选择持久化到 localStorage（键 zft_theme），
 *      页面刷新后保留用户上次选择；
 *   4. 未曾选择过时跟随系统偏好（prefers-color-scheme）。
 *
 * 设计说明：
 *   - 主题为纯展示层关注点，与认证/业务状态解耦，独立成 store，
 *     避免登出时误清除用户的主题偏好；
 *   - applyTheme() 同时在 main.ts 启动阶段同步调用一次，
 *     保证首屏渲染前主题就绪（防止暗色用户看到亮色闪屏 / FOUC）。
 * ==================================================================
 */
import { defineStore } from 'pinia'

/** 主题类型：亮色 / 暗色 */
export type ThemeMode = 'light' | 'dark'

/** localStorage 持久化键名（与其他 zft_* 键保持命名一致） */
export const THEME_STORAGE_KEY = 'zft_theme'

/**
 * 从 localStorage 恢复主题；无记录时回退系统偏好。
 * @returns 'light' | 'dark'
 */
function restoreTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* 存储不可用（隐私模式等）时忽略 */ }
  // 从未选择过：跟随系统深浅色偏好
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/**
 * 将主题落地到 DOM：
 *   - dark  -> <html class="dark">（激活 Element Plus dark css-vars + 暗色变量组）
 *   - light -> 移除 dark 类（Element Plus 默认亮色变量 + 亮色变量组）
 * 独立导出供 main.ts 在应用挂载前同步调用。
 */
export function applyThemeClass(mode: ThemeMode): void {
  const root = document.documentElement
  root.classList.toggle('dark', mode === 'dark')
  // 同步 color-scheme，让浏览器原生控件（滚动条/日期选择器等）跟随主题
  root.style.colorScheme = mode
}

export const useThemeStore = defineStore('theme', {
  state: () => ({
    /** 当前主题（初始即从 localStorage / 系统偏好恢复） */
    mode: restoreTheme() as ThemeMode,
  }),

  getters: {
    /** 是否暗色主题（图表配色、微交互的数据源） */
    isDark: (s) => s.mode === 'dark',
    /** 顶部控件 tooltip 提示文案 */
    toggleTip: (s) => (s.mode === 'dark' ? '切换到亮色主题' : '切换到暗色主题'),
  },

  actions: {
    /**
     * 应用初始化：同步 DOM 类与本地存储（main.ts 挂载前调用）。
     * 保证 <router-view> 首帧渲染时主题已生效。
     */
    init() {
      applyThemeClass(this.mode)
    },

    /**
     * 切换主题（亮 <-> 暗），并持久化用户选择。
     * 用户显式操作后不再跟随系统偏好。
     */
    toggle() {
      this.mode = this.mode === 'dark' ? 'light' : 'dark'
      this.persist()
      applyThemeClass(this.mode)
    },

    /** 显式指定主题（供未来"设置"页扩展使用） */
    set(mode: ThemeMode) {
      if (mode === this.mode) return
      this.mode = mode
      this.persist()
      applyThemeClass(mode)
    },

    /** 写入 localStorage（try/catch 兜底隐私模式写入失败场景） */
    persist() {
      try {
        localStorage.setItem(THEME_STORAGE_KEY, this.mode)
      } catch { /* 写入失败不阻断交互 */ }
    },
  },
})
