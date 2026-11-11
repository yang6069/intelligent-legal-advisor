<!--
  ==================================================================
  智法通前端 | 文书起草页
  ------------------------------------------------------------------
  功能：
    1. 左侧草稿列表（创建/切换/删除）
    2. 工具栏：模板选择 / AI 起草 / 保存 / 导出（Word/MD/TXT/HTML）
    3. 主区：编辑器 + 右侧实时预览（marked 渲染）
    4. 全部角色可见，普通用户和管理员完全相同

  技术要点：
    - 使用原生 textarea + marked.parse 实现简易双栏 Markdown 编辑器；
    - 导出走后端 /api/documents/{id}/export 直接下载，不用前端转换；
    - AI 起草调用 /api/documents/ai-draft 后自动选中新草稿。
  ==================================================================
-->
<template>
  <div class="draft-page">
    <!-- ===== 左侧：草稿列表 ===== -->
    <aside class="draft-sidebar">
      <div class="side-header">
        <el-select
          v-model="filterType"
          size="small"
          placeholder="全部类型"
          clearable
          @change="loadDrafts"
        >
          <el-option
            v-for="t in templateOptions"
            :key="t.template_id"
            :label="t.label"
            :value="t.template_id"
          />
        </el-select>
        <el-button class="new-btn" type="primary" :icon="Plus" size="small" @click="showNewDialog = true">
          新建
        </el-button>
      </div>
      <el-input
        v-model="keyword"
        class="search-input"
        size="small"
        placeholder="搜索标题/正文"
        clearable
        @clear="loadDrafts"
        @keyup.enter="loadDrafts"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-scrollbar class="draft-list">
        <div
          v-for="d in drafts"
          :key="d.id"
          class="draft-item"
          :class="{ active: currentId === d.id }"
          @click="selectDraft(d.id)"
        >
          <div class="di-title">{{ d.title }}</div>
          <div class="di-meta">
            <el-tag size="small" effect="plain">{{ typeLabelOf(d.doc_type) }}</el-tag>
            <span class="di-time">{{ formatTime(d.updated_at) }}</span>
          </div>
          <el-icon class="di-del" @click.stop="onDelete(d.id)"><Delete /></el-icon>
        </div>
        <el-empty v-if="!drafts.length" description="暂无草稿" :image-size="70" />
      </el-scrollbar>
    </aside>

    <!-- ===== 右侧：编辑主区 ===== -->
    <section class="draft-main">
      <!-- 空态：提示新建或 AI 起草 -->
      <div v-if="!currentId" class="empty-state">
        <div class="empty-logo">📝</div>
        <h2>法律文书智能起草</h2>
        <p>选择模板或直接描述你的法律需求，AI 帮你起草完整文书</p>
        <div class="empty-actions">
          <el-button size="large" :icon="MagicStick" @click="showAIDialog = true">
            <span>✨ AI 智能起草</span>
          </el-button>
          <el-button size="large" :icon="Plus" type="primary" @click="showNewDialog = true">
            <span>📄 新建空白草稿</span>
          </el-button>
        </div>
      </div>

      <!-- 已选中草稿：工具栏 + 编辑器 + 预览 -->
      <template v-else>
        <div class="toolbar">
          <div class="tb-left">
            <el-input v-model="draft.title" class="tb-title" placeholder="文书标题" @blur="autoSave" />
            <el-select v-model="draft.doc_type" size="small" @change="autoSave">
              <el-option
                v-for="t in templateOptions"
                :key="t.template_id"
                :label="t.label"
                :value="t.template_id"
              />
            </el-select>
            <el-tag size="small" effect="plain">字数 {{ wordCount }}</el-tag>
          </div>
          <div class="tb-right">
            <el-button size="small" :icon="MagicStick" :loading="rewriteLoading" @click="showRewriteDialog = true">AI 重写</el-button>
            <el-button size="small" type="success" :icon="Check" @click="saveDraft" :disabled="!dirty">保存</el-button>
            <el-dropdown>
              <el-button size="small" type="primary">
                <el-icon><Download /></el-icon> 导出
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="doExport('docx')">
                    <el-icon><Document /></el-icon> Word (.docx)
                  </el-dropdown-item>
                  <el-dropdown-item @click="doExport('md')">
                    <el-icon><EditPen /></el-icon> Markdown (.md)
                  </el-dropdown-item>
                  <el-dropdown-item @click="doExport('txt')">
                    <el-icon><DocumentCopy /></el-icon> 纯文本 (.txt)
                  </el-dropdown-item>
                  <el-dropdown-item @click="doExport('html')">
                    <el-icon><Promotion /></el-icon> 网页 (.html)
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>

        <div class="editor-area">
          <div class="editor-pane">
            <div class="pane-header">正文编辑（Markdown）</div>
            <textarea
              ref="editorRef"
              v-model="draft.content"
              class="editor-textarea"
              placeholder="在此处编写或粘贴文书正文..."
              @input="onContentChange"
            />
          </div>
          <div class="preview-pane">
            <div class="pane-header">实时预览</div>
            <div class="preview-body markdown-body" v-html="preview" />
          </div>
        </div>
      </template>
    </section>

    <!-- ===== 新建草稿弹窗 ===== -->
    <el-dialog v-model="showNewDialog" title="新建文书草稿" width="460px">
      <el-form :model="newForm" label-width="90px">
        <el-form-item label="文书标题">
          <el-input v-model="newForm.title" placeholder="如：张三诉李四民间借贷纠纷起诉状" />
        </el-form-item>
        <el-form-item label="文书类型">
          <el-select v-model="newForm.doc_type" style="width:100%">
            <el-option
              v-for="t in templateOptions"
              :key="t.template_id"
              :label="t.label"
              :value="t.template_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="使用模板">
          <el-switch v-model="newForm.withTemplate" active-text="是" inactive-text="否" />
          <div class="form-tip">开启后自动填入标准模板，【请填写：…】处为中文提示，直接替换成你的信息即可。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showNewDialog = false">取消</el-button>
        <el-button type="primary" @click="onCreateDraft">创建</el-button>
      </template>
    </el-dialog>

    <!-- ===== AI 重写弹窗（针对当前草稿全文润色改写） ===== -->
    <el-dialog v-model="showRewriteDialog" :title="rewriteLoading ? 'AI 正在重写...' : 'AI 重写当前文书'" width="520px">
      <template v-if="!rewriteLoading">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="AI 将保留文书中的人物与事实信息，整体优化结构、措辞与法律用语规范"
          style="margin-bottom: 16px"
        />
        <el-form label-width="90px">
          <el-form-item label="重写要求">
            <el-input
              v-model="rewriteInstruction"
              type="textarea"
              :rows="4"
              maxlength="500"
              show-word-limit
              placeholder="选填。例如：语气更正式一些；补充违约责任条款；精简到一页以内。留空则整体润色。"
            />
          </el-form-item>
        </el-form>
      </template>
      <template v-else>
        <div class="ai-loading">
          <el-icon class="spin-icon"><Loading /></el-icon>
          <div>AI 正在重写您的文书，请稍候...</div>
          <div class="ai-tip">（重写长文书可能需要 30~60 秒，请勿关闭页面）</div>
        </div>
      </template>
      <template #footer v-if="!rewriteLoading">
        <el-button @click="showRewriteDialog = false">取消</el-button>
        <el-button type="primary" :icon="MagicStick" @click="onAIRewrite">开始重写</el-button>
      </template>
    </el-dialog>

    <!-- ===== AI 起草弹窗 ===== -->
    <el-dialog v-model="showAIDialog" :title="aiLoading ? 'AI 正在起草...' : 'AI 智能起草'" width="560px">
      <el-form v-if="!aiLoading" :model="aiForm" label-width="90px">
        <el-form-item label="文书类型">
          <el-select v-model="aiForm.doc_type" style="width:100%">
            <el-option
              v-for="t in templateOptions"
              :key="t.template_id"
              :label="t.label"
              :value="t.template_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="起草要点">
          <el-input
            v-model="aiForm.ai_prompt"
            type="textarea"
            :rows="6"
            placeholder="描述具体事实、争议焦点、诉求等，AI 会自动生成完整文书。例如：张三借给李四 10 万元，约定年利率 12%，借期 1 年，到期未还。"
          />
        </el-form-item>
        <el-form-item label="参考模板">
          <el-switch v-model="aiForm.useTemplate" active-text="是" inactive-text="否" />
          <div class="form-tip">开启后 AI 会套用标准模板结构，产出更规范。</div>
        </el-form-item>
      </el-form>
      <template v-else>
        <div class="ai-loading">
          <el-icon class="spin-icon"><Loading /></el-icon>
          <div>AI 正在为您起草，请稍候...</div>
          <div class="ai-tip">（首次生成长文书可能需要 30~60 秒）</div>
        </div>
      </template>
      <template #footer v-if="!aiLoading">
        <el-button @click="showAIDialog = false">取消</el-button>
        <el-button type="primary" :icon="MagicStick" @click="onAIDraft">开始起草</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 文书起草页：草稿列表 + Markdown 双栏编辑 + AI 起草 + 多格式导出。
 * 使用 marked（package.json 已内置）做实时预览。
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Delete, Download, Document, EditPen, DocumentCopy, Promotion, MagicStick, Check, Search, Loading } from '@element-plus/icons-vue'
import { marked } from 'marked'
import { get, post, put, del, download } from '../api/client'

marked.setOptions({ breaks: true, gfm: true })

const templateOptions = ref<any[]>([])
const drafts = ref<any[]>([])
const currentId = ref<number | null>(null)
const keyword = ref('')
const filterType = ref('')
const dirty = ref(false)

const showNewDialog = ref(false)
const showAIDialog = ref(false)
const aiLoading = ref(false)
// AI 重写（针对当前草稿全文）
const showRewriteDialog = ref(false)
const rewriteLoading = ref(false)
const rewriteInstruction = ref('')

const newForm = reactive({ title: '', doc_type: 'complaint', withTemplate: true })
const aiForm = reactive({ doc_type: 'complaint', ai_prompt: '', useTemplate: true })

const draft = reactive({
  title: '',
  doc_type: '',
  content: '',
})

const wordCount = computed(() => {
  const text = draft.content || ''
  const chinese = (text.match(/[\u4e00-\u9fff]/g) || []).length
  const english = (text.match(/[a-zA-Z]+/g) || []).length
  return chinese + english
})

const preview = computed(() => marked.parse(draft.content || '') as string)

/** 加载模板列表 */
async function loadTemplates() {
  const data = await get<any>('/documents/templates')
  templateOptions.value = data.items || []
}

/** 加载草稿列表 */
async function loadDrafts() {
  const data = await get<any>('/documents/drafts', {
    doc_type: filterType.value,
    keyword: keyword.value,
  })
  drafts.value = data.items || []
}

/** 选中草稿 */
async function selectDraft(id: number) {
  // 有未保存修改 -> 提示
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('当前文书有未保存修改，是否丢弃？', '提示', {
        type: 'warning', confirmButtonText: '丢弃', cancelButtonText: '取消',
      })
    } catch { return }
  }
  currentId.value = id
  const data = await get<any>(`/documents/drafts/${id}`)
  draft.title = data.title
  draft.doc_type = data.doc_type
  draft.content = data.content || ''
  dirty.value = false
}

/** 新建草稿 */
async function onCreateDraft() {
  const payload: any = {
    title: newForm.title || '未命名文书',
    doc_type: newForm.doc_type,
    template_id: newForm.withTemplate ? newForm.doc_type : '',
  }
  const data = await post<any>('/documents/drafts', payload)
  showNewDialog.value = false
  newForm.title = ''
  await loadDrafts()
  await selectDraft(data.id)
  ElMessage.success('草稿已创建')
}

/** AI 起草 */
async function onAIDraft() {
  if (!aiForm.ai_prompt.trim() && !currentId.value) {
    ElMessage.warning('请先描述起草要点或选择已有草稿')
    return
  }
  aiLoading.value = true
  showAIDialog.value = false
  const payload: any = {
    doc_type: currentId.value ? draft.doc_type || aiForm.doc_type : aiForm.doc_type,
    ai_prompt: aiForm.ai_prompt,
    template_id: aiForm.useTemplate ? (currentId.value ? draft.doc_type || aiForm.doc_type : aiForm.doc_type) : '',
  }
  try {
    const data = await post<any>('/documents/ai-draft', payload)
    showAIDialog.value = false
    await loadDrafts()
    await selectDraft(data.id)
    ElMessage.success('AI 起草完成，请在编辑器中修改定稿')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || 'AI 起草失败')
  } finally {
    aiLoading.value = false
  }
}

/** AI 重写：把当前草稿全文交给 LLM 润色改写，结果覆盖写回 */
async function onAIRewrite() {
  if (!currentId.value) {
    ElMessage.warning('请先选择一份文书草稿')
    return
  }
  if (!draft.content.trim()) {
    ElMessage.warning('当前文书内容为空，请先编写内容或使用「AI 智能起草」')
    return
  }
  rewriteLoading.value = true
  try {
    const data = await post<any>('/documents/ai-rewrite', {
      doc_id: currentId.value,
      instruction: rewriteInstruction.value,
    })
    showRewriteDialog.value = false
    rewriteInstruction.value = ''
    draft.content = data.content
    dirty.value = false
    await loadDrafts()
    ElMessage.success('AI 重写完成，可继续手动微调')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || 'AI 重写失败')
  } finally {
    rewriteLoading.value = false
  }
}

/** 保存草稿 */
async function saveDraft() {
  if (!currentId.value) return
  await put(`/documents/drafts/${currentId.value}`, {
    title: draft.title,
    content: draft.content,
    doc_type: draft.doc_type,
  })
  dirty.value = false
  ElMessage.success('已保存')
  await loadDrafts()
}

/** 自动保存（失焦时） */
let saveTimer: any = null
function autoSave() {
  if (!currentId.value) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => saveDraft(), 1500)
}

function onContentChange() {
  dirty.value = true
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => { dirty.value = false }, 500)
}

/** 删除草稿 */
async function onDelete(id: number) {
  try {
    await ElMessageBox.confirm('确认删除这份文书草稿？', '提示', { type: 'warning' })
  } catch { return }
  await del(`/documents/drafts/${id}`)
  await loadDrafts()
  if (currentId.value === id) {
    currentId.value = null
    draft.title = ''; draft.doc_type = ''; draft.content = ''
  }
  ElMessage.success('已删除')
}

/** 导出 */
async function doExport(fmt: string) {
  if (!currentId.value) return
  try {
    const blob = await download(`/documents/${currentId.value}/export`, { fmt })
    const url = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = url
    const base = (draft.title || '文书').replace(/[\\/:*?"<>|]/g, '_')
    const extMap: Record<string, string> = { docx: 'docx', md: 'md', txt: 'txt', html: 'html' }
    a.download = `${base}.${extMap[fmt]}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 .${fmt} 文件`)
  } catch (e: any) {
    ElMessage.error(e?.message || '导出失败')
  }
}

function typeLabelOf(val: string) {
  return templateOptions.value.find(t => t.template_id === val)?.label || val
}

function formatTime(t: any) {
  if (!t) return ''
  const d = new Date(t)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(async () => {
  await loadTemplates()
  await loadDrafts()
})
</script>

<style scoped>
.draft-page {
  display: flex; gap: 16px; height: calc(100vh - 100px); min-height: 600px;
}

.draft-sidebar {
  width: 280px; display: flex; flex-direction: column; gap: 12px;
  background: var(--zft-panel); border: 1px solid var(--zft-border);
  border-radius: 16px; padding: 14px;
}
.side-header { display: flex; gap: 8px; align-items: center; }
.side-header .el-select { flex: 1; }
.search-input :deep(.el-input__wrapper) { border-radius: 10px; }
.draft-list { flex: 1; }
.draft-item {
  padding: 12px; border-radius: 10px; margin-bottom: 8px;
  background: var(--zft-panel-deep); border: 1px solid var(--zft-border);
  cursor: pointer; position: relative; transition: all 0.15s;
}
.draft-item:hover { border-color: var(--zft-primary); }
.draft-item.active {
  border-color: var(--zft-primary);
  background: var(--zft-menu-active-bg);
}
.di-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; color: var(--zft-text); }
.di-meta { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--zft-text-faint); }
.di-time { margin-left: auto; }
.di-del { position: absolute; top: 8px; right: 8px; opacity: 0; transition: opacity 0.15s; color: var(--zft-text-faint); }
.draft-item:hover .di-del { opacity: 1; }
.di-del:hover { color: #ef4444; }

.draft-main {
  flex: 1; display: flex; flex-direction: column;
  background: var(--zft-panel); border: 1px solid var(--zft-border);
  border-radius: 16px; overflow: hidden;
}

.empty-state {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: center; text-align: center; gap: 12px; padding: 40px;
}
.empty-logo {
  width: 80px; height: 80px; font-size: 40px;
  display: grid; place-items: center;
  border-radius: 20px; background: var(--zft-grad-brand);
  box-shadow: 0 8px 30px rgba(99, 102, 241, 0.4); color: #fff;
}
.empty-state h2 { margin: 12px 0 6px; color: var(--zft-text); }
.empty-state p { color: var(--zft-text-faint); }
.empty-actions { display: flex; gap: 14px; margin-top: 20px; }

.toolbar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 18px; border-bottom: 1px solid var(--zft-border);
  background: var(--zft-panel-deep);
}
.tb-left { display: flex; gap: 12px; align-items: center; flex: 1; }
.tb-title { width: 280px; }
.tb-right { display: flex; gap: 8px; }

.editor-area {
  flex: 1; display: grid; grid-template-columns: 1fr 1fr; min-height: 0;
}
.editor-pane, .preview-pane {
  display: flex; flex-direction: column; min-height: 0;
  min-width: 0; /* grid 子元素防溢出，避免滚动条延伸到相邻列 */
  overflow: hidden;
  border-right: 1px solid var(--zft-border);
}
.preview-pane { border-right: none; }
.pane-header {
  padding: 10px 16px; font-size: 12px; color: var(--zft-text-faint);
  background: var(--zft-panel-deep); border-bottom: 1px solid var(--zft-border);
}
.editor-textarea {
  flex: 1; width: 100%; padding: 16px; resize: none;
  border: none; outline: none; background: transparent;
  color: var(--zft-text); font-family: 'Consolas', 'Microsoft YaHei', monospace;
  font-size: 14px; line-height: 1.75;
  box-sizing: border-box; /* padding 计入 width，不溢出 */
  overflow-y: auto;       /* 独立滚动，滚动条限定在本列内 */
}
.preview-body {
  padding: 18px 22px;
  overflow-y: auto;
  flex: 1;
  box-sizing: border-box;
}

/* 编辑器 + 预览面板 细滚动条美化（6px，不再视觉上压到相邻列） */
.editor-textarea::-webkit-scrollbar,
.preview-body::-webkit-scrollbar { width: 6px; height: 6px; }
.editor-textarea::-webkit-scrollbar-track,
.preview-body::-webkit-scrollbar-track { background: transparent; }
.editor-textarea::-webkit-scrollbar-thumb,
.preview-body::-webkit-scrollbar-thumb {
  background: rgba(148, 163, 184, 0.35); border-radius: 3px;
}
.editor-textarea::-webkit-scrollbar-thumb:hover,
.preview-body::-webkit-scrollbar-thumb:hover {
  background: rgba(99, 102, 241, 0.55);
}

.form-tip { font-size: 12px; color: var(--zft-text-faint); margin-top: 4px; }

.ai-loading {
  text-align: center; padding: 40px 20px; color: var(--zft-text);
}
.spin-icon { font-size: 32px; color: var(--zft-primary); animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.ai-tip { font-size: 12px; color: var(--zft-text-faint); margin-top: 6px; }

/* Markdown 预览样式 */
:deep(.markdown-body h1), :deep(.markdown-body h2), :deep(.markdown-body h3) { margin-top: 18px; color: var(--zft-text); border-bottom: none; }
:deep(.markdown-body h1) { font-size: 22px; }
:deep(.markdown-body h2) { font-size: 18px; }
:deep(.markdown-body h3) { font-size: 15px; }
:deep(.markdown-body p) { line-height: 1.8; color: var(--zft-text-regular); }
:deep(.markdown-body ul) { padding-left: 18px; }
:deep(.markdown-body strong) { color: var(--zft-primary-text); }
</style>
