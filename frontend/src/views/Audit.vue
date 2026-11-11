<!--
  ==================================================================
  智法通前端 | 审计日志页（admin 专属）
  ------------------------------------------------------------------
  数据源：GET /audit/logs（分页 + 用户/模块/行为/时间范围过滤）
  展示：操作流水（登录/问答/合同/MCP/知识库等全部敏感操作留痕）
  全部展示字段前端中文化（行为/模块/操作人/IP 列），后端不返回中文值。
  ==================================================================
-->
<template>
  <div class="audit-page">
    <!-- 过滤工具栏 -->
    <div class="toolbar">
      <el-select v-model="module" style="width: 140px" placeholder="全部模块" clearable @change="search">
        <el-option v-for="(label, val) in MODULE_ZH" :key="val" :value="val" :label="label" />
      </el-select>
      <el-select v-model="action" style="width: 140px" placeholder="全部行为" clearable @change="search">
        <el-option v-for="(label, val) in ACTION_ZH" :key="val" :value="val" :label="label" />
      </el-select>
      <el-date-picker
        v-model="range"
        type="datetimerange"
        range-separator="至"
        start-placeholder="开始时间"
        end-placeholder="结束时间"
        style="width: 360px"
        value-format="YYYY-MM-DD HH:mm:ss"
        @change="search"
      />
    </div>

    <el-table :data="logs" v-loading="loading" stripe>
      <el-table-column label="序号" width="70">
        <template #default="{ $index }">{{ (page - 1) * size + $index + 1 }}</template>
      </el-table-column>
      <el-table-column label="操作人" width="130">
        <template #default="{ row }">{{ zhifyUser(row.real_name || row.username) }}</template>
      </el-table-column>
      <el-table-column label="行为" width="130">
        <template #default="{ row }">
          <el-tag size="small" :type="actionType(row.action)" effect="plain">
            {{ ACTION_ZH[row.action] || row.action }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="模块" width="110">
        <template #default="{ row }">{{ MODULE_ZH[row.module] || row.module }}</template>
      </el-table-column>
      <el-table-column prop="detail" label="操作详情" min-width="300" show-overflow-tooltip />
      <el-table-column v-if="!allUnknownIP" label="IP" width="140">
        <template #default="{ row }">{{ row.ip || '-' }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="时间" width="180" />
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="size"
        :total="total"
        layout="prev, pager, next, total"
        @current-change="loadLogs"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 审计日志逻辑：分页 + 多条件过滤 + 全字段中文化。
 */
import { onMounted, ref } from 'vue'
import { get } from '../api/client'
import { useAutoRefresh } from '../composables/useAutoRefresh'

// ---- 行为中文映射（后端 log_audit 传入的英文 action -> 中文） ----
const ACTION_ZH: Record<string, string> = {
  LOGIN: '登录成功',
  LOGIN_FAIL: '登录失败',
  LOGIN_LOCKED: '登录锁定',
  LOGIN_BLOCKED: '登录禁用账号',
  REGISTER: '用户注册',
  CHANGE_PWD: '修改密码',
  PASSWORD_CHANGE: '密码修改',
  PASSWORD_RESET: '密码重置',
  ROLE_CHANGE: '角色变更',
  STATUS_CHANGE: '状态变更',
  UPLOAD: '上传合同',
  REVIEW_TEXT: '文本审查',
  EXPORT: '导出报告',
  DELETE: '删除记录',
  CREATE: '创建',
  UPDATE: '更新',
  REBUILD: '重建索引',
  REGISTER_MCP: '注册MCP工具',
  TOOL_STATUS: '工具启停',
  TOOL_CALL: '工具调用',
  DOC_CREATE: '创建文档',
  DOC_UPDATE: '编辑文档',
  DOC_DELETE: '删除文档',
  DOC_AI_DRAFT: 'AI起草',
  DOC_AI_REWRITE: 'AI改写',
  DOC_EXPORT: '导出文档',
  PURCHASE_PLAN: '购买套餐',
}

// ---- 模块中文映射 ----
const MODULE_ZH: Record<string, string> = {
  auth: '认证',
  users: '用户管理',
  profile: '个人资料',
  contract: '合同审查',
  kb: '知识库',
  mcp: 'MCP工具',
  documents: '文书',
  subscription: '套餐',
  analytics: '数据分析',
  chat: '智能问答',
  system: '系统',
}

const _USER_ZH_MAP: Record<string, string> = {
  admin: '超级管理员',
  test: '测试用户',
}
function zhifyUser(name: string): string {
  if (!name) return '-'
  const zh = _USER_ZH_MAP[name?.toLowerCase()]
  return zh || name
}

const loading = ref(false)
const logs = ref<any[]>([])
const page = ref(1)
const size = 15
const total = ref(0)
const module = ref('')
const action = ref('')
const range = ref<[string, string] | null>(null)
const allUnknownIP = ref(false)   // 如果当前页所有 IP 都是 unknown，就隐藏 IP 列

onMounted(() => {
  loadLogs()
})

useAutoRefresh(() => loadLogs(true), 15000)

async function loadLogs(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await get<any>('/audit/logs', {
      page: page.value, size,
      module: module.value, action: action.value,
      start: range.value?.[0] || '', end: range.value?.[1] || '',
    })
    logs.value = data.items || []
    total.value = data.total || 0
    // 全部 IP 都是 unknown/空就隐藏该列
    allUnknownIP.value = logs.value.length > 0 &&
      logs.value.every((r: any) => !r.ip || r.ip === 'unknown')
  } finally { if (!silent) loading.value = false }
}

function search() {
  page.value = 1
  loadLogs()
}

function actionType(a: string): string {
  const d = a?.toUpperCase() || ''
  if (d.includes('DELETE') || d.includes('FAIL') || d.includes('LOCKED') || d.includes('BLOCKED')) return 'danger'
  if (d.includes('LOGIN') || d.includes('REGISTER') || d.includes('CHANGE')) return 'success'
  if (d.includes('CALL') || d.includes('EXPORT') || d.includes('PURCHASE')) return 'primary'
  return 'info'
}
</script>

<style scoped>
.audit-page { display: flex; flex-direction: column; gap: 14px; }
.toolbar { display: flex; gap: 12px; flex-wrap: wrap; }
.pager { display: flex; justify-content: flex-end; }
</style>
