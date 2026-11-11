<!--
  ==================================================================
  智法通前端 | 登录页
  ------------------------------------------------------------------
  职责：
    1. 账号密码登录（JWT 认证 -> Pinia 持久化 -> 回跳来源页）；
    2. 用户注册（默认普通用户角色）；
    3. 视觉：法治科技风 + 动态光斑背景 + 玻璃拟态卡片，
       右上角格式刷风格主题切换控件（亮/暗，localStorage 持久化）。
  ==================================================================
-->
<template>
  <div class="login-page">
    <!-- 右上角：主题切换控件（登录页独立于主布局，单独挂载） -->
    <div class="theme-dock">
      <ThemeToggle />
    </div>

    <!-- 动态背景光斑（纯 CSS 动画，营造炫酷氛围） -->
    <div class="glow glow-a"></div>
    <div class="glow glow-b"></div>
    <div class="glow glow-c"></div>

    <!-- 品牌区 -->
    <div class="login-brand">
      <div class="brand-logo-big">⚖</div>
      <h1 class="brand-title">智法通</h1>
      <p class="brand-slogan">企业级智能法律顾问平台 · AI 驱动 · 专业高效</p>
      <div class="brand-features">
        <span><el-icon><ChatDotRound /></el-icon> 智能法律问答</span>
        <span><el-icon><DocumentChecked /></el-icon> 合同一键智能审查</span>
        <span><el-icon><Connection /></el-icon> 法律法规深度检索</span>
      </div>
    </div>

    <!-- 登录/注册卡片 -->
    <div class="login-card">
      <el-tabs v-model="activeTab" stretch>
        <el-tab-pane label="登 录" name="login" />
        <el-tab-pane label="注 册" name="register" />
      </el-tabs>

      <!-- 登录表单 -->
      <el-form
        v-if="activeTab === 'login'"
        ref="loginFormRef"
        :model="loginForm"
        :rules="loginRules"
        size="large"
        @keyup.enter="doLogin"
      >
        <el-form-item prop="username">
          <el-input v-model="loginForm.username" placeholder="用户名" :prefix-icon="User" clearable />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-button
          class="submit-btn"
          type="primary"
          :loading="loading"
          @click="doLogin"
        >
          {{ loading ? '正在验证身份…' : '进入智法通' }}
        </el-button>
        <p v-if="dict.loginHint" class="hint">{{ dict.loginHint }}</p>
      </el-form>

      <!-- 注册表单 -->
      <el-form
        v-else
        ref="regFormRef"
        :model="regForm"
        :rules="regRules"
        size="large"
        @keyup.enter="doRegister"
      >
        <el-form-item prop="username">
          <el-input v-model="regForm.username" placeholder="用户名（2-32位）" :prefix-icon="User" clearable />
        </el-form-item>
        <el-form-item prop="real_name">
          <el-input v-model="regForm.real_name" placeholder="真实姓名（可选）" :prefix-icon="Avatar" clearable />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="regForm.password" type="password" placeholder="密码（至少6位）" :prefix-icon="Lock" show-password />
        </el-form-item>
        <el-form-item prop="confirm">
          <el-input v-model="regForm.confirm" type="password" placeholder="确认密码" :prefix-icon="Lock" show-password />
        </el-form-item>
        <el-button class="submit-btn" type="primary" :loading="loading" @click="doRegister">
          {{ loading ? '正在创建账号…' : '注册并登录' }}
        </el-button>
        <p class="hint">注册默认为普通用户角色，可使用智能问答与合同审查功能</p>
      </el-form>    </div>

    <!-- 页脚 -->
    <div class="login-footer">© 2026 智法通 ZhiFaTong v6.0 · Powered by LLM Multi-Agent</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 登录/注册逻辑：
 *   1. doLogin: POST /api/auth/login -> setLogin 存储菜单树 -> 跳转（来源页或 /）；
 *   2. doRegister: POST /api/auth/register -> 成功后自动登录。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { User, Lock, Avatar } from '@element-plus/icons-vue'
import { post } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useDictStore } from '../stores/dict'
import ThemeToggle from '../components/ThemeToggle.vue'

const auth = useAuthStore()
const dict = useDictStore()
const route = useRoute()
const router = useRouter()

// 拉取业务字典（演示账号提示等后端下发，前端零硬编码业务数据）
onMounted(() => dict.ensure())

const activeTab = ref<'login' | 'register'>('login')
const loading = ref(false)
const loginFormRef = ref<FormInstance>()
const regFormRef = ref<FormInstance>()

// ---- 登录表单与校验规则 ----
const loginForm = reactive({ username: '', password: '' })
const loginRules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, min: 6, message: '密码至少 6 位', trigger: 'blur' }],
}

// ---- 注册表单与校验规则（含两次密码一致性） ----
const regForm = reactive({ username: '', real_name: '', password: '', confirm: '' })
const regRules: FormRules = {
  username: [
    { required: true, min: 2, max: 32, message: '用户名 2-32 位', trigger: 'blur' },
    { pattern: /^[a-zA-Z0-9_]+$/, message: '仅支持字母/数字/下划线', trigger: 'blur' },
  ],
  password: [{ required: true, min: 6, message: '密码至少 6 位', trigger: 'blur' }],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_r, v: string, cb: (ok?: Error) => void) =>
        v === regForm.password ? cb() : cb(new Error('两次输入的密码不一致')),
      trigger: 'blur',
    },
  ],
}

/** 登录：校验 -> 请求 -> 持久化登录态 -> 跳转 */
async function doLogin() {
  await loginFormRef.value?.validate().catch(() => Promise.reject())
  loading.value = true
  try {
    const data = await post<any>('/auth/login', loginForm)
    auth.setLogin(data)
    ElMessage.success(`欢迎回来，${data.user?.real_name || data.user?.username}`)
    // 回跳来源页（守卫重定向场景），否则默认首页
    router.push((route.query.redirect as string) || '/')
  } finally {
    loading.value = false
  }
}

/** 注册：成功后直接自动登录 */
async function doRegister() {
  await regFormRef.value?.validate().catch(() => Promise.reject())
  loading.value = true
  try {
    await post('/auth/register', {
      username: regForm.username,
      password: regForm.password,
      real_name: regForm.real_name,
    })
    ElMessage.success('注册成功，正在自动登录…')
    // 自动登录（复用登录链路，简化用户操作）
    const data = await post<any>('/auth/login', {
      username: regForm.username,
      password: regForm.password,
    })
    auth.setLogin(data)
    router.push('/')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
/* ===== 整页：主题化底色 + 网格纹理 ===== */
.login-page {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 50px;
  padding: 24px;
  padding-bottom: 50px;   /* 给底部版权留足位置 */
  overflow: hidden;
  background:
    radial-gradient(ellipse at 30% 0%, rgba(37, 99, 235, 0.08), transparent 55%),
    var(--zft-bg);
}
.login-page::before {
  content: '';
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(15, 23, 42, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(15, 23, 42, 0.035) 1px, transparent 1px);
  background-size: 42px 42px;
  pointer-events: none;
}
html.dark .login-page::before {
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.04) 1px, transparent 1px);
}

/* ===== 右上角主题切换坞 ===== */
.theme-dock {
  position: absolute;
  top: 20px; right: 24px;
  z-index: 10;
}

/* ===== 动态光斑（强度随主题变量 --zft-glow-opacity 调节） ===== */
.glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: var(--zft-glow-opacity);
  animation: drift 14s ease-in-out infinite alternate;
}
.glow-a { width: 420px; height: 420px; left: -100px; top: -120px; background: #2563eb; }
.glow-b { width: 360px; height: 360px; right: -80px; top: 30%; background: #7c3aed; animation-delay: -4s; }
.glow-c { width: 300px; height: 300px; left: 35%; bottom: -140px; background: #0ea5e9; animation-delay: -8s; }
@keyframes drift {
  from { transform: translate(0, 0) scale(1); }
  to   { transform: translate(40px, 30px) scale(1.15); }
}

/* ===== 品牌区 ===== */
.login-brand { position: relative; z-index: 1; max-width: 440px; }
.brand-logo-big {
  width: 68px; height: 68px;
  display: grid; place-items: center;
  font-size: 36px;
  border-radius: 20px;
  background: var(--zft-grad-brand);
  box-shadow: 0 10px 40px rgba(99, 102, 241, 0.5);
  margin-bottom: 18px;
  animation: float 5s ease-in-out infinite;
  color: #fff;
}
@keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
.brand-title {
  font-size: 42px; font-weight: 800; letter-spacing: 6px;
  background: linear-gradient(90deg, #1d4ed8, #6d28d9, #0e7490);
  background-size: 200%;
  -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 6s linear infinite;
}
html.dark .brand-title {
  background: linear-gradient(90deg, #60a5fa, #a78bfa, #38bdf8);
  background-size: 200%;
  -webkit-background-clip: text; background-clip: text;
}
@keyframes shimmer { to { background-position: 200%; } }
.brand-slogan { color: var(--zft-text-dim); margin: 8px 0 18px; letter-spacing: 2px; font-size: 14px; }
.brand-features { display: flex; flex-direction: column; gap: 8px; }
.brand-features span {
  display: inline-flex; align-items: center; gap: 10px;
  color: var(--zft-text-regular);
  font-size: 14px;
}
.brand-features .el-icon { color: var(--zft-primary-text); }

/* ===== 玻璃拟态登录卡片 ===== */
.login-card {
  position: relative; z-index: 1;
  width: 400px;
  padding: 34px 34px 26px;
  border-radius: 20px;
  background: var(--zft-card);
  backdrop-filter: blur(20px);
  border: 1px solid var(--zft-border-strong);
  box-shadow: var(--zft-shadow-float);
}
.submit-btn {
  width: 100%;
  margin-top: 6px;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  border-radius: 12px;
  background: var(--zft-grad-brand-h);
  border: none;
}
.submit-btn:hover { filter: brightness(1.12); }
.hint { margin-top: 16px; font-size: 12px; color: var(--zft-text-dim); line-height: 1.7; }

.login-footer {
  position: absolute; bottom: 18px;
  color: var(--zft-text-faint); font-size: 12px; letter-spacing: 1px;
}

/* 窄屏：隐藏品牌区，居中卡片 */
@media (max-width: 900px) {
  .login-brand { display: none; }
  .login-page { justify-content: center; }
}
</style>
