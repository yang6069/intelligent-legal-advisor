/**
 * ==================================================================
 * 智法通前端 | 路由配置与导航守卫
 * ------------------------------------------------------------------
 * 职责：
 *   1. 声明全部业务路由（与后端 MENU_DEFINITIONS.path 一一对应）；
 *   2. 全局前置守卫：
 *      - 未登录 -> 重定向 /login；
 *      - 已登录访问 /login -> 重定向首页；
 *      - 角色等级不足 -> 拦截并提示（RBAC 前端第二道防线）。
 *
 * 角色等级映射（与后端 config.py 一致）：
 *      user=1 / legal_admin=2 / admin=3
 * ==================================================================
 */
import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'

import MainLayout from '../layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // ---- 登录页（无布局） ----
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/Login.vue'),
      meta: { public: true, title: '登录 · 智法通' },
    },
    // ---- 主布局（左侧角色菜单 + 顶栏） ----
    {
      path: '/',
      component: MainLayout,
      redirect: '/dashboard',
      children: [
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('../views/Dashboard.vue'),
          meta: { minLevel: 1, title: '仪表盘' },
        },
        {
          path: 'chat',
          name: 'chat',
          component: () => import('../views/Chat.vue'),
          meta: { minLevel: 1, title: '智能问答' },
        },
        {
          path: 'contract',
          name: 'contract',
          component: () => import('../views/Contract.vue'),
          meta: { minLevel: 1, title: '合同审查' },
        },
        {
          path: 'drafting',
          name: 'drafting',
          component: () => import('../views/Drafting.vue'),
          meta: { minLevel: 1, title: '文书起草' },
        },
        {
          path: 'subscription',
          name: 'subscription',
          component: () => import('../views/Subscription.vue'),
          // onlyRole: 仅普通用户可访问（管理员/法务问答无限制，无购买需求）
          meta: { minLevel: 1, onlyRole: 'user', title: '我的套餐' },
        },
        {
          path: 'knowledge',
          name: 'knowledge',
          component: () => import('../views/Knowledge.vue'),
          meta: { minLevel: 2, title: '知识库管理' },
        },
        {
          path: 'analytics',
          name: 'analytics',
          component: () => import('../views/Analytics.vue'),
          meta: { minLevel: 1, title: '数据分析' },
        },
        {
          path: 'mcp',
          name: 'mcp',
          component: () => import('../views/Mcp.vue'),
          meta: { minLevel: 3, title: 'MCP 工具' },
        },
        {
          path: 'users',
          name: 'users',
          component: () => import('../views/Users.vue'),
          meta: { minLevel: 3, title: '用户管理' },
        },
        {
          path: 'audit',
          name: 'audit',
          component: () => import('../views/Audit.vue'),
          meta: { minLevel: 3, title: '审计日志' },
        },
        {
          path: 'profile',
          name: 'profile',
          component: () => import('../views/Profile.vue'),
          meta: { minLevel: 1, title: '个人中心' },
        },
      ],
    },
    // ---- 兜底 404 ----
    {
      path: '/:pathMatch(.*)*',
      redirect: '/dashboard',
    },
  ],
})

// ==================================================================
// 全局前置守卫：登录校验 + RBAC 路由级拦截
// ==================================================================
router.beforeEach((to) => {
  const raw = localStorage.getItem('zft_auth')
  let logged = false
  let level = 1
  let role = ''
  try {
    if (raw) {
      const parsed = JSON.parse(raw)
      logged = !!parsed.token && !!parsed.user
      role = parsed.user?.role || ''
      level = ({ user: 1, legal_admin: 2, admin: 3 } as Record<string, number>)[role] ?? 1
    }
  } catch { /* 存储损坏视为未登录 */ }

  // 页面标题同步（浏览器标签页展示当前位置）
  document.title = (to.meta.title ? `${to.meta.title} · ` : '') + '智法通'

  // 公开页面直接放行
  if (to.meta.public) {
    return logged ? { path: '/' } : true
  }
  // 未登录一律去登录页（记录来源，登录后回跳）
  if (!logged) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  // 角色等级不足：拦截（防止用户手输 URL 越权访问）
  const minLevel = (to.meta.minLevel as number) ?? 1
  if (level < minLevel) {
    ElMessage.warning('当前角色无权访问该页面')
    return false
  }
  // 限定角色页面（如"我的套餐"仅普通用户）：非目标角色重定向回问答页
  const onlyRole = to.meta.onlyRole as string | undefined
  if (onlyRole && role !== onlyRole) {
    ElMessage.warning('该页面仅对普通用户开放，您当前角色问答无限制')
    return { path: '/chat' }
  }
  return true
})

export default router
