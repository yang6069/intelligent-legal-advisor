<!--
  ==================================================================
  智法通前端 | 主布局（左侧角色菜单 + 顶栏用户区）
  ------------------------------------------------------------------
  职责：
    1. 左侧菜单按登录角色动态渲染（数据源：后端下发 menus，
       已按 min_level 过滤，普通用户/法务/管理员各见其位）；
    2. 顶栏：面包屑 + 格式刷风格主题切换控件 + 用户名/角色徽标 + 退出登录；
    3. 内容区 <router-view> 承载各业务页面；
    4. 炫酷视觉：玻璃拟态侧栏 + 品牌渐变 Logo + 悬浮高亮，
       亮 / 暗双主题由 CSS 变量驱动（stores/theme.ts 统一管理）。
  ==================================================================
-->
<template>
  <div class="layout-root">
    <!-- ============ 左侧导航（角色菜单） ============ -->
    <aside class="layout-aside" :class="{ collapsed: isCollapse }">
      <!-- 品牌 Logo 区 -->
      <div class="brand">
        <div class="brand-logo">⚖</div>
        <transition name="fade">
          <div v-if="!isCollapse" class="brand-text">
            <span class="brand-name">智法通</span>
            <span class="brand-sub">企业级智能法律顾问</span>
          </div>
        </transition>
      </div>

      <!-- 角色菜单（el-menu 动态 icon 渲染） -->
      <el-scrollbar class="menu-scroll">
        <el-menu
          :default-active="route.path"
          :collapse="isCollapse"
          :collapse-transition="false"
          router
          class="zft-menu"
        >
          <el-menu-item
            v-for="m in auth.menus"
            :key="m.code"
            :index="m.path"
          >
            <el-icon><component :is="m.icon" /></el-icon>
            <template #title>{{ m.title }}</template>
          </el-menu-item>
        </el-menu>
      </el-scrollbar>

      <!-- 折叠按钮 -->
      <div class="collapse-btn" @click="isCollapse = !isCollapse">
        <el-icon>
          <Expand v-if="isCollapse" />
          <Fold v-else />
        </el-icon>
      </div>
    </aside>

    <!-- ============ 右侧主体（顶栏 + 内容区） ============ -->
    <div class="layout-main">
      <!-- 顶栏 -->
      <header class="layout-header">
        <div class="header-left">
          <span class="page-title">{{ route.meta.title || '' }}</span>
        </div>
        <div class="header-right">
          <!-- 格式刷风格主题切换控件（亮 / 暗，持久化 localStorage） -->
          <ThemeToggle />
          <!-- 角色徽标（颜色按身份区分） -->
          <el-tag
            :type="auth.user?.role === 'admin' ? 'danger'
              : auth.user?.role === 'legal_admin' ? 'warning' : 'info'"
            effect="dark"
            round
          >
            {{ auth.roleDisplay || '用户' }}
          </el-tag>
          <el-dropdown @command="onCommand">
            <span class="user-chip">
              <el-avatar :size="30" class="user-avatar">
                {{ (auth.user?.real_name || auth.user?.username || '?').slice(0, 1) }}
              </el-avatar>
              <span class="user-name">{{ auth.user?.real_name || auth.user?.username }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><Avatar /></el-icon>个人中心
                </el-dropdown-item>
                <el-dropdown-item command="logout" divided>
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <!-- 内容区：全站 keep-alive 保留所有页面组件，切路由不卸载 → SSE/fetch 不中断 -->
      <main class="layout-content">
        <router-view v-slot="{ Component }">
          <keep-alive>
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 主布局逻辑：
 *   - 菜单数据来自 auth store（登录时后端按角色下发）；
 *   - 侧栏可折叠（小屏优化）；
 *   - 顶栏内嵌 ThemeToggle：单击切换亮/暗主题（localStorage 持久化）；
 *   - 退出登录清理本地态并跳转登录页。
 */
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import ThemeToggle from '../components/ThemeToggle.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const isCollapse = ref(false)   // 侧栏折叠状态

/** 顶栏下拉命令分发 */
function onCommand(cmd: string) {
  if (cmd === 'profile') {
    router.push('/profile')
  } else if (cmd === 'logout') {
    ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning',
    }).then(() => {
      auth.logout()
      router.push('/login')
    }).catch(() => { /* 取消 */ })
  }
}
</script>

<style scoped>
/* ===== 布局骨架：左侧固定 + 右侧自适应 ===== */
.layout-root {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* ===== 侧栏（双主题：亮=白雾蓝 / 暗=深海军蓝渐变） ===== */
.layout-aside {
  width: 240px;
  display: flex;
  flex-direction: column;
  background: var(--zft-aside-bg);
  border-right: 1px solid var(--zft-border);
  transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}
.layout-aside.collapsed { width: 64px; }

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 16px;
  border-bottom: 1px solid var(--zft-divider);
  overflow: hidden;
}
.brand-logo {
  width: 40px; height: 40px;
  flex-shrink: 0;
  display: grid; place-items: center;
  font-size: 22px;
  border-radius: 12px;
  background: var(--zft-grad-brand);
  box-shadow: 0 4px 18px rgba(99, 102, 241, 0.45);
  color: #fff;
}
.brand-text { display: flex; flex-direction: column; white-space: nowrap; }
.brand-name {
  font-size: 18px; font-weight: 700;
  background: linear-gradient(90deg, #2563eb, #7c3aed);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: 2px;
}
html.dark .brand-name {
  background: linear-gradient(90deg, #60a5fa, #a78bfa);
  -webkit-background-clip: text;
  background-clip: text;
}
.brand-sub { font-size: 11px; color: var(--zft-text-faint); letter-spacing: 1px; }

.menu-scroll { flex: 1; }

/* el-menu 双主题定制（去边框 + 悬浮渐变高亮） */
.zft-menu {
  border-right: none;
  background: transparent;
  padding: 8px;
}
.zft-menu :deep(.el-menu-item) {
  border-radius: 10px;
  margin: 4px 0;
  color: var(--zft-menu-text);
  height: 44px;
}
.zft-menu :deep(.el-menu-item:hover) {
  background: var(--zft-primary-soft-bg);
  color: var(--zft-menu-text-hover);
}
.zft-menu :deep(.el-menu-item.is-active) {
  background: var(--zft-menu-active-bg);
  color: var(--zft-menu-active-text);
  box-shadow: inset 2px 0 0 var(--zft-primary);
}

.collapse-btn {
  padding: 14px;
  text-align: center;
  cursor: pointer;
  color: var(--zft-text-faint);
  border-top: 1px solid var(--zft-divider);
  transition: color 0.2s;
}
.collapse-btn:hover { color: var(--zft-primary); }

/* ===== 右侧主体 ===== */
.layout-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.layout-header {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 22px;
  background: var(--zft-header-bg);
  backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--zft-border);
}
.page-title { font-size: 16px; font-weight: 600; color: var(--zft-text); margin: 0; }
.header-right { display: flex; align-items: center; gap: 16px; }

.user-chip {
  display: flex; align-items: center; gap: 8px;
  cursor: pointer;
  color: var(--zft-text-regular);
  padding: 4px 10px;
  border-radius: 999px;
  transition: background 0.2s;
}
.user-chip:hover { background: var(--zft-hover-bg); }
.user-avatar { background: var(--zft-grad-brand); }
.user-name { font-size: 14px; }

.layout-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* 折叠时品牌文字淡出 */
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* 小屏自适应 */
@media (max-width: 768px) {
  .layout-aside { position: fixed; z-index: 100; height: 100vh; }
  .layout-aside:not(.collapsed) { box-shadow: 8px 0 24px rgba(0,0,0,0.25); }
}
</style>
