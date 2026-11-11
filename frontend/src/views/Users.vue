<!--
  ==================================================================
  智法通前端 | 用户管理页（admin 专属）
  ------------------------------------------------------------------
  数据源：
    GET /users            用户分页列表
    PUT /users/role       调整角色（user/legal_admin/admin）
    PUT /users/status     启用/禁用账号
    PUT /users/password   重置密码
  ==================================================================
-->
<template>
  <div class="users-page">
    <div class="toolbar">
      <el-input
        v-model="keyword"
        style="width: 260px"
        placeholder="搜索用户名/姓名"
        clearable
        :prefix-icon="Search"
        @keyup.enter="search"
        @clear="search"
      />
      <el-select v-model="roleFilter" style="width: 150px" placeholder="全部角色" clearable @change="search">
        <el-option v-for="r in dict.roles" :key="r.value" :value="r.value" :label="r.label" />
      </el-select>
    </div>

    <el-table :data="users" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="username" label="用户名" width="150" />
      <el-table-column prop="real_name" label="姓名" width="140" />
      <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip />
      <el-table-column label="角色" width="130">
        <template #default="{ row }">
          <el-select
            :model-value="row.role"
            size="small"
            :disabled="row.id === auth.user?.id"
            @change="(v: any) => changeRole(row, v)"
          >
            <el-option v-for="r in dict.roles" :key="r.value" :value="r.value" :label="r.label" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'danger'" effect="dark">
            {{ row.status === 1 ? '正常' : '已禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="注册时间" width="170" />
      <el-table-column prop="last_login" label="最近登录" width="170" />
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link :type="row.status === 1 ? 'danger' : 'success'" @click="toggleStatus(row)">
            {{ row.status === 1 ? '禁用' : '启用' }}
          </el-button>
          <el-button size="small" link @click="resetPwd(row)">重置密码</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="size"
        :total="total"
        layout="prev, pager, next, total"
        @current-change="loadUsers"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 用户管理逻辑：
 *   - 角色调整（当前登录者自身角色不可改，防权限自杀）；
 *   - 启用/禁用（禁用后无法登录）；
 *   - 管理员重置任意用户密码。
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { get, put } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useDictStore } from '../stores/dict'
import { useAutoRefresh } from '../composables/useAutoRefresh'

const auth = useAuthStore()
const dict = useDictStore()
const loading = ref(false)
const users = ref<any[]>([])
const page = ref(1)
const size = 12
const total = ref(0)
const keyword = ref('')
const roleFilter = ref('')

onMounted(() => {
  dict.ensure()   // 角色字典后端下发
  loadUsers()
})

/**
 * 用户列表自动刷新（数据变化自动上屏，无需手动刷新浏览器）：
 * 定时 + 窗口聚焦时静默拉取（角色调整/启用禁用跨端生效即时可见）。
 */
useAutoRefresh(() => loadUsers(true), 20000)

/** 分页加载用户列表（silent=true 时不触发 loading 动画） */
async function loadUsers(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await get<any>('/users', {
      page: page.value, size, keyword: keyword.value, role: roleFilter.value,
    })
    users.value = data.items || []
    total.value = data.total || 0
  } finally { if (!silent) loading.value = false }
}

/** 触发检索 */
function search() {
  page.value = 1
  loadUsers()
}

/** 调整角色（二次确认，防误操作；角色文案取自后端字典） */
async function changeRole(row: any, role: string) {
  const label = dict.labelOf(dict.roles, role)
  await ElMessageBox.confirm(
    `将「${row.real_name || row.username}」的角色调整为「${label}」？角色变更将实时影响其可见菜单与接口权限。`,
    '角色调整', { type: 'warning' },
  )
  await put('/users/role', { user_id: row.id, role })
  ElMessage.success('角色已调整')
  loadUsers(true)
}

/** 启用/禁用切换 */
async function toggleStatus(row: any) {
  const action = row.status === 1 ? '禁用' : '启用'
  await ElMessageBox.confirm(`确认${action}账号「${row.username}」？`, '提示', { type: 'warning' })
  await put('/users/status', { user_id: row.id, status: row.status === 1 ? 0 : 1 })
  ElMessage.success(`已${action}`)
  loadUsers(true)   // 静默刷新（数据变化自动上屏）
}

/** 重置密码 */
async function resetPwd(row: any) {
  const { value } = await ElMessageBox.prompt(
    `为「${row.username}」设置新密码（至少 6 位）`, '重置密码',
    { inputType: 'password', inputPattern: /^.{6,64}$/, inputErrorMessage: '密码长度 6-64 位' },
  )
  await put('/users/password', { user_id: row.id, new_password: value })
  ElMessage.success('密码已重置')
}
</script>

<style scoped>
.users-page { display: flex; flex-direction: column; gap: 14px; }
.toolbar { display: flex; gap: 12px; }
.pager { display: flex; justify-content: flex-end; }
</style>
