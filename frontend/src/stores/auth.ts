/**
 * ==================================================================
 * 智法通前端 | 认证状态管理（Pinia Store）
 * ------------------------------------------------------------------
 * 职责：
 *   1. 持久化登录态（token / 用户信息 / 角色菜单树 -> localStorage）；
 *   2. 提供登录 / 登出动作；
 *   3. 计算属性：角色等级 / 是否管理员（菜单按权限渲染的数据源）。
 * ==================================================================
 */
import { defineStore } from 'pinia'

/** 后端返回的菜单项结构（config.MENU_DEFINITIONS） */
export interface MenuItem {
  code: string
  title: string
  icon: string
  path: string
  min_level: number
  module: string
}

/** 登录用户信息 */
export interface UserInfo {
  id: number
  username: string
  real_name: string
  role: string
  email?: string
  phone?: string
}

interface AuthState {
  token: string
  user: UserInfo | null
  menus: MenuItem[]
  roleDisplay: string
}

const STORAGE_KEY = 'zft_auth'

/** 从 localStorage 恢复（页面刷新保持登录态） */
function restore(): AuthState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        token: parsed.token || '',
        user: parsed.user || null,
        menus: parsed.menus || [],
        roleDisplay: parsed.roleDisplay || '',
      }
    }
  } catch { /* 损坏数据忽略 */ }
  return { token: '', user: null, menus: [], roleDisplay: '' }
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => restore(),

  getters: {
    /** 是否已登录 */
    isLoggedIn: (s) => !!s.token && !!s.user,
    /** 角色等级（user=1 / legal_admin=2 / admin=3） */
    roleLevel: (s) => ({ user: 1, legal_admin: 2, admin: 3 }[s.user?.role || 'user'] ?? 1),
    /** 是否管理员 */
    isAdmin: (s) => s.user?.role === 'admin',
  },

  actions: {
    /**
     * 登录成功后写入状态（含后端按角色下发的菜单树）。
     * @param data 后端 /api/auth/login 返回体
     */
    setLogin(data: { token: string; user: UserInfo; menus: MenuItem[]; role_display?: string }) {
      this.token = data.token
      this.user = data.user
      this.menus = data.menus || []
      this.roleDisplay = data.role_display || ''
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        token: this.token, user: this.user,
        menus: this.menus, roleDisplay: this.roleDisplay,
      }))
    },

    /** 登出：清空状态与本地存储 */
    logout() {
      this.token = ''
      this.user = null
      this.menus = []
      this.roleDisplay = ''
      localStorage.removeItem(STORAGE_KEY)
    },
  },
})
