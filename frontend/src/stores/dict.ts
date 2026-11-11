/**
 * ==================================================================
 * 智法通前端 | 业务字典 Store（后端下发，前端零硬编码业务数据）
 * ------------------------------------------------------------------
 * 职责：
 *   1. 会话内一次性拉取 /api/meta/dictionaries 全量业务字典并缓存；
 *   2. 提供类型化访问器：文档类型 / 角色 / 审计枚举 / 工作流阶段 /
 *      快捷提问 / 登录提示等，供各页面选项与标签渲染；
 *   3. ensure() 幂等：多页面并发调用只发一次请求。
 *
 * 设计约束（需求：数据驱动动态渲染）：
 *   - 业务枚举唯一来源是后端 config 业务字典中心；
 *   - 本 store 不内置任何业务兜底数据（未加载完成时返回空数组，
 *     页面选项随字典到达自动填充，由响应式驱动）。
 * ==================================================================
 */
import { defineStore } from 'pinia'
import { get } from '../api/client'

/** 字典条目通用结构（下拉选项 / 标签色） */
export interface DictItem {
  value: string
  label: string
  tag?: string
}

interface DictData {
  doc_types: DictItem[]
  roles: DictItem[]
  audit_modules: DictItem[]
  audit_actions: string[]
  contract_stages: string[]
  analytics_days: number[]
  cluster_features: Record<string, string>
  quick_questions: string[]
  login_hint: string
}

const EMPTY: DictData = {
  doc_types: [], roles: [], audit_modules: [], audit_actions: [],
  contract_stages: [], analytics_days: [], cluster_features: {},
  quick_questions: [], login_hint: '',
}

export const useDictStore = defineStore('dict', {
  state: () => ({
    data: EMPTY as DictData,
    loaded: false,
    /** 进行中的加载 Promise（并发去重） */
    _pending: null as Promise<void> | null,
  }),

  getters: {
    /** 知识库文档类型选项 */
    docTypes: (s) => s.data.doc_types,
    /** 角色选项 */
    roles: (s) => s.data.roles,
    /** 审计模块选项 */
    auditModules: (s) => s.data.audit_modules,
    /** 审计行为枚举 */
    auditActions: (s) => s.data.audit_actions,
    /** 合同审查工作流阶段（进度动画） */
    contractStages: (s) => s.data.contract_stages,
    /** 数据分析时间范围选项 */
    analyticsDays: (s) => s.data.analytics_days,
    /** 聚类特征中文标签 */
    clusterFeatures: (s) => s.data.cluster_features,
    /** 智能问答快捷提问（后端按真实高频咨询下发） */
    quickQuestions: (s) => s.data.quick_questions,
    /** 登录页演示账号提示 */
    loginHint: (s) => s.data.login_hint,
  },

  actions: {
    /**
     * 确保字典已加载（幂等；失败不缓存，下次调用重试）。
     * 页面在 onMounted 中 await，模板经 getter 响应式渲染。
     */
    async ensure(): Promise<void> {
      if (this.loaded) return
      if (this._pending) return this._pending
      this._pending = (async () => {
        try {
          const data = await get<Partial<DictData>>('/meta/dictionaries')
          // 逐字段合并：后端字段缺失时保持空值（不内置兜底业务数据）
          this.data = { ...EMPTY, ...(data || {}) }
          this.loaded = true
        } catch {
          /* 拉取失败：保持空字典，下次 ensure 重试 */
        } finally {
          this._pending = null
        }
      })()
      return this._pending
    },

    /** 按 value 查找字典条目标签（未命中回退原值展示） */
    labelOf(items: DictItem[], value: string): string {
      return items.find((i) => i.value === value)?.label || value
    },

    /** 按 value 查找字典条目标签色（未命中 info） */
    tagOf(items: DictItem[], value: string): string {
      return items.find((i) => i.value === value)?.tag || 'info'
    },
  },
})
