<!--
  ==================================================================
  智法通前端 | 个人中心页（全部角色可见）
  ------------------------------------------------------------------
  数据源：
    GET /auth/profile     个人信息
    PUT /auth/profile     更新资料（姓名/邮箱/手机）
    PUT /auth/password    修改密码（旧密码校验）
  ==================================================================
-->
<template>
  <div class="profile-page">
    <div class="profile-grid">
      <!-- ============ 左：资料卡 ============ -->
      <div class="profile-card">
        <div class="avatar-zone">
          <el-avatar :size="80" class="big-avatar">
            {{ (auth.user?.real_name || auth.user?.username || '?').slice(0, 1) }}
          </el-avatar>
          <div class="who">
            <div class="who-name">{{ auth.user?.real_name || auth.user?.username }}</div>
            <el-tag :type="roleTag" effect="dark" round>{{ auth.roleDisplay || '用户' }}</el-tag>
          </div>
        </div>
        <el-divider />
        <el-form :model="infoForm" label-width="80px">
          <el-form-item label="用户名">
            <el-input :model-value="auth.user?.username" disabled />
          </el-form-item>
          <el-form-item label="姓名">
            <el-input v-model="infoForm.real_name" maxlength="64" />
          </el-form-item>
          <el-form-item label="邮箱">
            <el-input v-model="infoForm.email" maxlength="128" placeholder="选填" />
          </el-form-item>
          <el-form-item label="手机">
            <el-input v-model="infoForm.phone" maxlength="32" placeholder="选填" />
          </el-form-item>
          <el-button type="primary" :loading="saving" @click="saveProfile">保存资料</el-button>
        </el-form>
      </div>

      <!-- ============ 右：安全卡（修改密码） ============ -->
      <div class="profile-card">
        <h3 class="card-t"><el-icon><Lock /></el-icon> 修改密码</h3>
        <el-form :model="pwdForm" :rules="pwdRules" ref="pwdRef" label-width="90px">
          <el-form-item label="旧密码" prop="old_password">
            <el-input v-model="pwdForm.old_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码" prop="new_password">
            <el-input v-model="pwdForm.new_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="确认密码" prop="confirm">
            <el-input v-model="pwdForm.confirm" type="password" show-password />
          </el-form-item>
          <el-button type="primary" :loading="changing" @click="changePwd">更新密码</el-button>
        </el-form>

        <el-divider />

        <h3 class="card-t"><el-icon><InfoFilled /></el-icon> 权限说明</h3>
        <div class="perm-list">
          <div v-for="m in auth.menus" :key="m.code" class="perm-item">
            <el-icon class="perm-icon"><component :is="m.icon" /></el-icon>
            {{ m.title }}
            <span class="perm-path">{{ m.path }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 个人中心逻辑：
 *   - 资料编辑（实时回显后端最新数据）；
 *   - 密码修改（两次一致 + 旧密码校验，成功后提示重新登录）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Lock } from '@element-plus/icons-vue'
import { get, put } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const saving = ref(false)
const changing = ref(false)
const pwdRef = ref<FormInstance>()

const infoForm = reactive({ real_name: '', email: '', phone: '' })
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })

const pwdRules: FormRules = {
  old_password: [{ required: true, min: 6, message: '请输入旧密码（至少6位）', trigger: 'blur' }],
  new_password: [{ required: true, min: 6, message: '新密码至少 6 位', trigger: 'blur' }],
  confirm: [{
    required: true, message: '请再次输入新密码', trigger: 'blur',
    validator: (_r, v: string, cb: (ok?: Error) => void) =>
      v === pwdForm.new_password ? cb() : cb(new Error('两次输入的密码不一致')),
  }],
}

/** 角色徽标配色 */
const roleTag = computed(() =>
  auth.user?.role === 'admin' ? 'danger' : auth.user?.role === 'legal_admin' ? 'warning' : 'info')

onMounted(async () => {
  const data = await get<any>('/auth/profile')
  Object.assign(infoForm, {
    real_name: data.real_name || '', email: data.email || '', phone: data.phone || '',
  })
})

/** 保存资料（同步刷新本地登录态缓存） */
async function saveProfile() {
  saving.value = true
  try {
    await put('/auth/profile', { ...infoForm })
    // 同步本地存储（顶栏姓名即时更新）
    if (auth.user) {
      auth.user.real_name = infoForm.real_name
      auth.user.email = infoForm.email
      auth.user.phone = infoForm.phone
    }
    ElMessage.success('资料已更新')
  } finally { saving.value = false }
}

/** 修改密码 */
async function changePwd() {
  await pwdRef.value?.validate().catch(() => Promise.reject())
  changing.value = true
  try {
    await put('/auth/password', {
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password,
    })
    ElMessage.success('密码已修改，请牢记新密码')
    pwdForm.old_password = pwdForm.new_password = pwdForm.confirm = ''
  } finally { changing.value = false }
}
</script>

<style scoped>
.profile-page { max-width: 1100px; }
.profile-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.profile-card {
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  border-radius: 14px;
  padding: 24px;
  box-shadow: var(--zft-shadow-card);
}
.avatar-zone { display: flex; align-items: center; gap: 20px; }
.big-avatar {
  background: var(--zft-grad-brand);
  font-size: 32px;
  box-shadow: 0 8px 26px rgba(99, 102, 241, 0.4);
}
.who-name { font-size: 20px; font-weight: 700; color: var(--zft-text); margin-bottom: 8px; }
.card-t {
  display: flex; align-items: center; gap: 8px;
  color: var(--zft-text); font-size: 15px; margin: 0 0 16px;
}

.perm-list { display: flex; flex-direction: column; gap: 8px; }
.perm-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--zft-stat-chip-bg);
  border: 1px solid var(--zft-stat-chip-border);
  color: var(--zft-text-regular);
  font-size: 13px;
}
.perm-item .perm-icon { color: var(--zft-primary-text); }
.perm-path { margin-left: auto; color: var(--zft-text-faint); font-size: 11.5px; }

@media (max-width: 900px) { .profile-grid { grid-template-columns: 1fr; } }
</style>
