<!--
  ==================================================================
  智法通前端 | MCP 工具管理页（admin 专属）
  ------------------------------------------------------------------
  数据源：
    GET  /mcp/tools         工具目录（发现）
    PUT  /mcp/tools/status  启用/停用
    POST /mcp/tools/call    调用工具（参数编辑器 + 结果面板）
    GET  /mcp/health        服务器健康检查
    GET  /mcp/stats         调用统计
  ==================================================================
-->
<template>
  <div class="mcp-page">
    <!-- ============ 统计与健康 ============ -->
    <div class="mcp-top">
      <div class="stat-chips">
        <div class="chip"><div class="cv">{{ stats.total_servers ?? 0 }}</div><div class="cl">服务器</div></div>
        <div class="chip"><div class="cv">{{ stats.total_tools ?? 0 }}</div><div class="cl">注册工具</div></div>
        <div class="chip"><div class="cv">{{ stats.active_tools ?? 0 }}</div><div class="cl">启用中</div></div>
        <div class="chip"><div class="cv">{{ stats.total_calls ?? 0 }}</div><div class="cl">累计调用</div></div>
      </div>
      <el-button :icon="Monitor" :loading="checking" @click="checkHealth">健康检查</el-button>
    </div>

    <!-- ============ 健康结果 ============ -->
    <div v-if="servers.length" class="health-row">
      <div v-for="s in servers" :key="s.server" class="health-dot" :class="s.status === 'healthy' ? 'ok' : 'bad'">
        <span class="dot" />{{ s.server }}
        <em>{{ s.status === 'healthy' ? '在线' : '异常' }}</em>
      </div>
    </div>

    <!-- ============ 工具卡片墙 ============ -->
    <div v-loading="loading" class="tool-grid">
      <div
        v-for="t in tools"
        :key="t.name"
        class="tool-card"
        :class="{ disabled: t.status !== 'active' }"
      >
        <div class="tool-head">
          <div class="tool-name">{{ t.name }}</div>
          <el-switch
            :model-value="t.status === 'active'"
            size="small"
            @change="(v: any) => toggleTool(t.name, v)"
          />
        </div>
        <div class="tool-server">
          <el-icon><SetUp /></el-icon>{{ t.server }}
        </div>
        <div class="tool-desc">{{ t.description || '暂无描述' }}</div>
        <div class="tool-actions">
          <el-button size="small" type="primary" round @click="openCaller(t)">调用</el-button>
        </div>
      </div>
      <el-empty v-if="!tools.length && !loading" description="暂无注册工具" />
    </div>

    <!-- ============ 调用对话框 ============ -->
    <el-dialog v-model="callerVisible" :title="`调用工具：${activeTool?.name || ''}`" width="620px">
      <div v-if="activeTool" class="caller">
        <div class="schema-props">
          <div v-for="(p, name) in schemaProps" :key="name" class="prop-row">
            <label>
              {{ name }}
              <el-tag v-if="isRequired(name)" size="small" type="danger" effect="plain">必填</el-tag>
            </label>
            <!-- 按参数类型渲染输入控件 -->
            <el-input
              v-if="p.type === 'number'"
              v-model.number="args[name as string]"
              type="number"
              :placeholder="p.description"
            />
            <el-input
              v-else
              v-model="args[name as string]"
              :placeholder="p.description || `参数 ${name}`"
            />
          </div>
          <div v-if="!schemaCount" class="no-args">该工具无需参数</div>
        </div>
        <el-button
          type="primary"
          :loading="calling"
          style="margin-top: 14px"
          @click="callTool"
        >执行调用</el-button>

        <!-- 调用结果 -->
        <div v-if="callResult" class="result-box">
          <div class="result-head">
            执行结果（{{ callResult.latency_ms }}ms）
            <el-tag size="small" :type="callResult.status === 'ok' ? 'success' : 'danger'">
              {{ callResult.status }}
            </el-tag>
          </div>
          <pre class="result-json">{{ JSON.stringify(callResult.result, null, 2) }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * MCP 工具管理逻辑：
 *   - 工具目录加载与启停切换；
 *   - 依据 input schema 动态渲染参数表单；
 *   - 调用结果 JSON 展示。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Monitor } from '@element-plus/icons-vue'
import { get, post, put } from '../api/client'
import { useAutoRefresh } from '../composables/useAutoRefresh'

const loading = ref(false)
const tools = ref<any[]>([])
const stats = ref<any>({})
const servers = ref<any[]>([])
const checking = ref(false)

const callerVisible = ref(false)
const activeTool = ref<any>(null)
const args = reactive<Record<string, any>>({})
const calling = ref(false)
const callResult = ref<any>(null)

onMounted(() => {
  loadTools()
  loadStats()
})

/**
 * 工具目录与调用统计自动刷新（数据变化自动上屏）：
 * 定时 + 窗口聚焦时静默拉取（工具启停/调用次数跨端即时同步）。
 */
useAutoRefresh(() => {
  loadTools(true)
  loadStats(true)
}, 30000)

/** 工具目录（silent=true 时不触发 loading 动画） */
async function loadTools(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await get<any>('/mcp/tools')
    tools.value = data.tools || []
  } finally { if (!silent) loading.value = false }
}

/** 调用统计 */
async function loadStats() {
  try { stats.value = await get<any>('/mcp/stats') } catch { /* 降级 */ }
}

/** 健康检查 */
async function checkHealth() {
  checking.value = true
  try {
    const data = await get<any>('/mcp/health')
    servers.value = data.servers || []
  } finally { checking.value = false }
}

/** 启用/停用切换 */
async function toggleTool(name: string, active: boolean) {
  await put('/mcp/tools/status', { tool_name: name, active })
  ElMessage.success(`工具 ${name} 已${active ? '启用' : '停用'}`)
  loadTools()
  loadStats()
}

/** 打开调用器：根据 schema 预填默认参数 */
function openCaller(t: any) {
  activeTool.value = t
  callResult.value = null
  Object.keys(args).forEach((k) => delete args[k])
  const props = t.schema?.properties || {}
  for (const [k, p] of Object.entries(props)) {
    (args as any)[k] = (p as any).default ?? ''
  }
  callerVisible.value = true
}

/** schema 参数属性（表单渲染数据源） */
const schemaProps = computed(() => activeTool.value?.schema?.properties || {})
const schemaCount = computed(() => Object.keys(schemaProps.value).length)

/** 必填判定（schema.required 数组） */
function isRequired(name: string): boolean {
  return (activeTool.value?.schema?.required || []).includes(name)
}

/** 执行调用 */
async function callTool() {
  calling.value = true
  callResult.value = null
  try {
    // 空字符串参数剔除（避免后端字符串校验失败）
    const payload: Record<string, any> = {}
    for (const [k, v] of Object.entries(args)) {
      if (v !== '' && v !== null && v !== undefined) payload[k] = v
    }
    callResult.value = await post<any>('/mcp/tools/call', {
      tool_name: activeTool.value.name,
      arguments: payload,
    })
    loadStats()
  } catch { /* 错误已由拦截器提示 */ }
  finally { calling.value = false }
}
</script>

<style scoped>
.mcp-page { display: flex; flex-direction: column; gap: 16px; }

/* ===== 统计 + 健康按钮 ===== */
.mcp-top {
  display: flex; align-items: center; gap: 14px;
  padding: 16px 18px;
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  border-radius: 14px;
  box-shadow: var(--zft-shadow-card);
}
.stat-chips { display: flex; gap: 12px; flex: 1; flex-wrap: wrap; }
.chip { flex: 1; min-width: 110px; text-align: center; }
.cv { font-size: 24px; font-weight: 800; color: var(--zft-primary-text); }
.cl { font-size: 12px; color: var(--zft-text-dim); }

/* ===== 健康指示 ===== */
.health-row { display: flex; gap: 10px; flex-wrap: wrap; }
.health-dot {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 12.5px; color: var(--zft-text-dim);
  padding: 6px 14px;
  border-radius: 999px;
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
}
.health-dot .dot { width: 8px; height: 8px; border-radius: 50%; }
.health-dot.ok .dot { background: #34d399; box-shadow: 0 0 8px #34d399; }
.health-dot.bad .dot { background: #f87171; box-shadow: 0 0 8px #f87171; }
.health-dot em { font-style: normal; color: var(--zft-text-faint); }

/* ===== 工具卡片墙 ===== */
.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}
.tool-card {
  padding: 16px;
  border-radius: 14px;
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  transition: all 0.25s;
}
.tool-card:hover {
  transform: translateY(-3px);
  border-color: rgba(96, 165, 250, 0.5);
  box-shadow: var(--zft-shadow-card-hover);
}
.tool-card.disabled { opacity: 0.45; filter: grayscale(0.6); }
.tool-head { display: flex; justify-content: space-between; align-items: center; }
.tool-name { font-weight: 700; color: var(--zft-text); font-size: 14.5px; }
.tool-server {
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--zft-primary-text); font-size: 12px; margin: 8px 0;
}
.tool-desc {
  color: var(--zft-text-dim); font-size: 12.5px; line-height: 1.6;
  min-height: 40px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tool-actions { margin-top: 10px; }

/* ===== 调用器 ===== */
.prop-row { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.prop-row label { font-size: 13px; color: var(--zft-text-regular); }
.no-args { color: var(--zft-text-faint); font-size: 13px; }
.result-box {
  margin-top: 18px;
  border-radius: 12px;
  background: var(--zft-code-bg);
  border: 1px solid rgba(52, 211, 153, 0.35);
  padding: 14px;
}
.result-head {
  display: flex; align-items: center; gap: 10px;
  color: var(--zft-risk-low); font-size: 13px; margin-bottom: 10px;
}
.result-json {
  font-size: 12px; color: var(--zft-code-text);
  white-space: pre-wrap; word-break: break-all;
  max-height: 320px; overflow: auto;
}
</style>
