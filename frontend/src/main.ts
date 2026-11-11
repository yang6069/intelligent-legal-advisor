/**
 * ==================================================================
 * 智法通前端 | 应用入口
 * ------------------------------------------------------------------
 * 职责：
 *   1. 创建 Vue 应用并挂载根组件；
 *   2. 注册 Element Plus 组件库与全量图标；
 *   3. 安装 Pinia（状态管理）与 Router（路由守卫见 router/index.ts）；
 *   4. 引入全局样式（亮 / 暗双主题体系，默认跟随用户上次选择）。
 * ==================================================================
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { useThemeStore } from './stores/theme'
import './styles/main.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// ------------------------------------------------------------------
// 主题引导：在任何组件渲染之前，同步恢复用户主题选择
// （localStorage -> <html class="dark">），避免暗色用户看到亮色闪屏。
// 注意：必须先 use(pinia) 才能在根组件作用域外使用 store。
// ------------------------------------------------------------------
useThemeStore().init()

// 全量注册 Element Plus 图标（菜单 icon 按名称动态渲染）
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(router)
// 全局中文 locale：el-pagination "共 N 条"、el-picker/el-table 等内置文本中文化
app.use(ElementPlus, { size: 'default', locale: zhCn })

app.mount('#app')
