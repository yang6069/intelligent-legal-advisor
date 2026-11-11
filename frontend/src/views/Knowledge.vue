<!--
  ==================================================================
  智法通前端 | 知识库管理页（legal_admin / admin）
  ------------------------------------------------------------------
  数据源：
    GET    /knowledge/docs        文档分页检索（类型/关键词过滤）
    POST   /knowledge/docs        新增文档（同步三路索引）
    PUT    /knowledge/docs/{id}   更新文档
    DELETE /knowledge/docs/{id}   删除文档
    POST   /knowledge/rebuild     全量重建索引
    GET    /knowledge/stats       索引规模统计
    GET    /knowledge/graph       图谱节点预览
  ==================================================================
-->
<template>
  <div class="kb-page">
    <!-- ============ 顶部统计条 ============ -->
    <div class="kb-stats">
      <div v-for="s in statCards" :key="s.label" class="stat-chip" :style="{ '--sc': s.color }">
        <div class="stat-val">{{ s.value }}</div>
        <div class="stat-label">{{ s.label }}</div>
      </div>
      <el-button
        class="rebuild-btn"
        type="warning"
        :loading="rebuilding"
        :icon="RefreshRight"
        @click="rebuild"
      >全量重建索引</el-button>
    </div>

    <!-- ============ 工具栏 ============ -->
    <div class="kb-toolbar">
      <el-select v-model="filterType" style="width: 150px" placeholder="全部类型" clearable @change="search">
        <el-option v-for="t in dict.docTypes" :key="t.value" :value="t.value" :label="t.label" />
      </el-select>
      <el-input
        v-model="keyword"
        style="width: 260px"
        placeholder="搜索标题/法名/内容"
        clearable
        :prefix-icon="Search"
        @keyup.enter="search"
        @clear="search"
      />
      <el-button type="primary" :icon="Plus" @click="openEditor()">新增文档</el-button>
    </div>

    <!-- ============ 文档列表 ============ -->
    <el-table :data="docs" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column label="类型" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="typeTag(row.doc_type)" effect="plain">
            {{ typeLabel(row.doc_type) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="law_name" label="法律名称" width="170" show-overflow-tooltip />
      <el-table-column prop="article_no" label="条文号" width="100" />
      <el-table-column label="时效性" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.effectiveness === '有效' ? 'success' : 'info'" effect="dark">
            {{ row.effectiveness }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="字数" width="90">
        <template #default="{ row }">{{ row.char_count }}</template>
      </el-table-column>
      <el-table-column label="索引" width="80">
        <template #default="{ row }">
          <el-icon :color="row.indexed ? '#34d399' : '#f87171'">
            <CircleCheckFilled v-if="row.indexed" /><CircleCloseFilled v-else />
          </el-icon>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" link @click="openDetail(row.id)">查看</el-button>
          <el-button size="small" link @click="openEditor(row)">编辑</el-button>
          <el-button size="small" type="danger" link @click="remove(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="size"
        :total="total"
        layout="prev, pager, next, total"
        @current-change="loadDocs"
      />
    </div>

    <!-- ============ 新增/编辑对话框 ============ -->
    <el-dialog
      v-model="editorVisible"
      :title="editId ? '编辑文档' : '新增文档'"
      width="680px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="90px">
        <!-- 录入方式：新增时可在"文字输入 / 上传文件"间切换（编辑仅保留文字输入） -->
        <el-form-item v-if="!editId" label="录入方式">
          <el-radio-group v-model="inputMode">
            <el-radio value="manual">文字输入</el-radio>
            <el-radio value="upload">上传文件</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="类型" prop="doc_type">
          <el-select v-model="form.doc_type">
            <el-option v-for="t in dict.docTypes" :key="t.value" :value="t.value" :label="t.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" maxlength="255" placeholder="如：中华人民共和国民法典 第一百一十七条" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="法律名称">
            <el-input v-model="form.law_name" maxlength="128" placeholder="如：中华人民共和国民法典" />
          </el-form-item>
          <el-form-item label="条文号">
            <el-input v-model="form.article_no" maxlength="64" placeholder="如：第117条" />
          </el-form-item>
        </div>
        <el-form-item label="时效性">
          <el-radio-group v-model="form.effectiveness">
            <el-radio value="有效">有效</el-radio>
            <el-radio value="已废止">已废止</el-radio>
            <el-radio value="部分修订">部分修订</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="发布日期">
          <el-date-picker v-model="form.publish_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <!-- 文字输入模式：正文粘贴 -->
        <el-form-item v-if="inputMode === 'manual' || editId" label="正文" prop="content">
          <el-input
            v-model="form.content"
            type="textarea" :rows="10"
            maxlength="100000" show-word-limit
            placeholder="粘贴法律条文/裁判要旨/司法解释/合同模板全文（至少10字）。入库后将自动执行：法律感知分块 -> 向量索引 + BM25 索引 + 图谱写入"
          />
        </el-form-item>
        <!-- 上传文件模式：docx/pdf/txt/md 自动提取全文 -->
        <el-form-item v-else label="文档文件">
          <el-upload
            class="kb-uploader"
            drag
            :auto-upload="false"
            accept=".docx,.doc,.pdf,.txt,.md"
            :limit="1"
            :on-change="onUploadChange"
            :on-remove="() => (uploadFile = null)"
            :on-exceed="() => ElMessage.warning('一次只能上传一个文件，请先移除已选文件')"
          >
            <div class="kb-upload-hint">
              <p class="kb-upload-title">将文件拖到此处，或点击选择</p>
              <p class="kb-upload-sub">支持 docx / pdf / txt / md，单文件不超过 10MB；正文将自动提取入库并建立三路索引</p>
            </div>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存并索引</el-button>
      </template>
    </el-dialog>

    <!-- ============ 详情抽屉 ============ -->
    <el-drawer v-model="detailVisible" :title="detail?.title || '文档详情'" size="46%">
      <div v-if="detail" class="doc-detail">
        <div class="doc-meta">
          <el-tag :type="typeTag(detail.doc_type)" effect="dark">{{ typeLabel(detail.doc_type) }}</el-tag>
          <el-tag :type="detail.effectiveness === '有效' ? 'success' : 'info'" effect="plain">
            {{ detail.effectiveness }}
          </el-tag>
          <span v-if="detail.law_name" class="dm">{{ detail.law_name }}</span>
          <span v-if="detail.article_no" class="dm">{{ detail.article_no }}</span>
        </div>
        <pre class="doc-content">{{ detail.content }}</pre>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
/**
 * 知识库管理逻辑：
 *   - 文档 CRUD + 分页过滤检索；
 *   - 保存后由后端同步建立三路索引（向量/BM25/图谱）；
 *   - 全量重建（模型切换/数据修复场景）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules, UploadFile } from 'element-plus'
import { Search, Plus, RefreshRight } from '@element-plus/icons-vue'
import { get, post, put, del } from '../api/client'
import http from '../api/client'
import { useDictStore } from '../stores/dict'
import { useAutoRefresh } from '../composables/useAutoRefresh'

const dict = useDictStore()
const loading = ref(false)
const docs = ref<any[]>([])
const page = ref(1)
const size = 12
const total = ref(0)
const filterType = ref('')
const keyword = ref('')
const stats = ref<any>({})

const editorVisible = ref(false)
const detailVisible = ref(false)
const detail = ref<any>(null)
const editId = ref<number | null>(null)
const saving = ref(false)
const rebuilding = ref(false)
const formRef = ref<FormInstance>()

// ---- 表单与校验 ----
const form = reactive({
  doc_type: 'law', title: '', law_name: '', article_no: '',
  effectiveness: '有效', publish_date: '', content: '',
})
/** 录入方式：manual=文字粘贴（默认） / upload=文件上传（仅新增时可选） */
const inputMode = ref<'manual' | 'upload'>('manual')
/** 上传模式已选文件（el-upload 文件对象，raw 为原生 File） */
const uploadFile = ref<UploadFile | null>(null)

/** 校验规则：上传模式下正文由文件提供，跳过 content 必填校验 */
const formRules = computed<FormRules>(() => ({
  doc_type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  title: [{ required: true, min: 2, max: 255, message: '标题 2-255 字', trigger: 'blur' }],
  ...(inputMode.value === 'upload' && !editId.value ? {} : {
    content: [{ required: true, min: 10, message: '正文至少 10 字', trigger: 'blur' }],
  }),
}))

/** 顶部统计卡片（stats 接口派生，字段名与后端 /knowledge/stats 一一对应） */
const statCards = computed(() => [
  { label: '文档总数', value: stats.value.total_docs ?? 0, color: '#60a5fa' },
  { label: '向量块数', value: stats.value.vector_count ?? 0, color: '#34d399' },
  { label: 'BM25 索引', value: stats.value.bm25_count ?? 0, color: '#a78bfa' },
  { label: '图谱节点', value: stats.value.graph_nodes ?? 0, color: '#f472b6' },
  { label: '图谱关系', value: stats.value.graph_edges ?? 0, color: '#fbbf24' },
])

onMounted(() => {
  dict.ensure()   // 文档类型字典后端下发
  loadDocs()
  loadStats()
})

/**
 * 文档列表与索引统计自动刷新（数据变化自动上屏）：
 * 定时 + 窗口聚焦时静默拉取（其他端增删文档/索引重建后自动同步）。
 */
useAutoRefresh(() => {
  loadDocs(true)
  loadStats(true)
}, 20000)

/** 加载文档分页列表（silent=true 时不触发 loading 动画） */
async function loadDocs(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await get<any>('/knowledge/docs', {
      page: page.value, size, doc_type: filterType.value, keyword: keyword.value,
    })
    docs.value = data.items || []
    total.value = data.total || 0
  } finally { if (!silent) loading.value = false }
}

/** 加载索引统计（无独立 loading 态；参数与 loadDocs 对齐） */
async function loadStats(_silent = false) {
  try { stats.value = await get<any>('/knowledge/stats') } catch { /* 降级 */ }
}

/** 触发检索（回到第一页） */
function search() {
  page.value = 1
  loadDocs(true)
}

/** 打开编辑器（row 为空则新增） */
function openEditor(row?: any) {
  editId.value = row?.id ?? null
  inputMode.value = 'manual'
  uploadFile.value = null
  Object.assign(form, {
    doc_type: row?.doc_type ?? 'law',
    title: row?.title ?? '',
    law_name: row?.law_name ?? '',
    article_no: row?.article_no ?? '',
    effectiveness: row?.effectiveness ?? '有效',
    publish_date: row?.publish_date ?? '',
    content: '',
  })
  editorVisible.value = true
}

/** 上传模式：选中文件回调（limit=1，记录最新选择） */
function onUploadChange(file: UploadFile) {
  uploadFile.value = file
}

/** 保存（新增或更新，后端自动同步索引） */
async function save() {
  // 上传模式前置检查：必须已选文件（在表单校验前给出友好提示）
  if (!editId.value && inputMode.value === 'upload' && !uploadFile.value?.raw) {
    ElMessage.warning('请先选择要上传的文档文件')
    return
  }
  await formRef.value?.validate().catch(() => Promise.reject())
  saving.value = true
  try {
    if (!editId.value && inputMode.value === 'upload') {
      // ---- 上传文件模式：multipart 提交（文件 + 元数据），后端提取全文并入库 ----
      const fd = new FormData()
      fd.append('file', uploadFile.value!.raw as File)
      fd.append('doc_type', form.doc_type)
      if (form.title) fd.append('title', form.title)
      if (form.law_name) fd.append('law_name', form.law_name)
      if (form.article_no) fd.append('article_no', form.article_no)
      fd.append('effectiveness', form.effectiveness)
      if (form.publish_date) fd.append('publish_date', form.publish_date)
      const res = await http.post<any>('/knowledge/docs/upload', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      ElMessage.success(`文档已上传入库（共 ${res.char_count} 字）并建立索引`)
    } else if (editId.value) {
      await put(`/knowledge/docs/${editId.value}`, { ...form })
      ElMessage.success('文档已更新，索引已同步')
    } else {
      await post('/knowledge/docs', { ...form })
      ElMessage.success('文档已入库并建立索引')
    }
    editorVisible.value = false
    loadDocs(true)
    loadStats(true)
  } finally { saving.value = false }
}

/** 查看详情 */
async function openDetail(id: number) {
  detail.value = await get<any>(`/knowledge/docs/${id}`)
  detailVisible.value = true
}

/** 删除文档（连带触发全量重建） */
async function remove(id: number) {
  await ElMessageBox.confirm('删除该文档将触发索引全量重建，确认删除？', '提示', { type: 'warning' })
  await del(`/knowledge/docs/${id}`)
  ElMessage.success('已删除')
  loadDocs(true)
  loadStats(true)
}

/** 全量重建三路索引 */
async function rebuild() {
  await ElMessageBox.confirm('全量重建将重新分块并写入向量/BM25/图谱索引，耗时与文档量成正比，确认执行？', '全量重建', { type: 'warning' })
  rebuilding.value = true
  try {
    const res = await post<any>('/knowledge/rebuild')
    ElMessage.success(`重建完成：${res.message || '索引已刷新'}`)
    loadDocs(true)
    loadStats(true)
  } finally { rebuilding.value = false }
}

/** 类型汉化与标签色（字典后端下发，前端零硬编码） */
function typeLabel(t: string): string {
  return dict.labelOf(dict.docTypes, t)
}
function typeTag(t: string): string {
  return dict.tagOf(dict.docTypes, t)
}
</script>

<style scoped>
.kb-page { display: flex; flex-direction: column; gap: 16px; }

/* ===== 统计条 ===== */
.kb-stats {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 16px 18px;
  background: var(--zft-card);
  border: 1px solid var(--zft-border);
  border-radius: 14px;
  box-shadow: var(--zft-shadow-card);
}
.stat-chip {
  flex: 1; min-width: 130px;
  text-align: center;
  padding: 8px 10px;
  border-radius: 10px;
  background: var(--zft-stat-chip-bg);
  border: 1px solid var(--zft-stat-chip-border);
}
.stat-val { font-size: 24px; font-weight: 800; color: var(--sc); }
.stat-label { font-size: 12px; color: var(--zft-text-dim); margin-top: 2px; }
.rebuild-btn { margin-left: auto; border-radius: 10px; }

/* ===== 工具栏 ===== */
.kb-toolbar { display: flex; gap: 12px; }
.kb-toolbar .el-button--primary { border-radius: 10px; }

.pager { display: flex; justify-content: flex-end; margin-top: 4px; }

.form-row { display: flex; gap: 14px; }

/* ===== 上传文件模式拖拽区 ===== */
.kb-uploader :deep(.el-upload-dragger) {
  padding: 26px 20px;
  border-radius: 12px;
  background: var(--zft-panel-deep);
}
.kb-uploader :deep(.el-upload-dragger:hover) { border-color: var(--zft-primary); }
.kb-upload-hint { text-align: center; }
.kb-upload-title {
  margin: 0 0 6px;
  color: var(--zft-text-regular);
  font-size: 14px;
  font-weight: 600;
}
.kb-upload-sub {
  margin: 0;
  color: var(--zft-text-faint);
  font-size: 12px;
  line-height: 1.7;
}

/* ===== 详情抽屉 ===== */
.doc-detail { padding: 0 6px; }
.doc-meta { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
.dm { color: var(--zft-text-dim); font-size: 13px; }
.doc-content {
  white-space: pre-wrap;
  color: var(--zft-text-regular);
  font-size: 13.5px;
  line-height: 2;
  font-family: inherit;
  background: var(--zft-panel-deep);
  border: 1px solid var(--zft-border);
  padding: 18px;
  border-radius: 12px;
  max-height: 70vh;
  overflow-y: auto;
}
</style>
