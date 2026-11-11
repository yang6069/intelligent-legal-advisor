<!--
  ==================================================================
  智法通前端 | 我的套餐页
  ------------------------------------------------------------------
  功能：
    1. 顶部卡片展示当前套餐状态（问答+合同审查双功能额度）
    2. 三张套餐卡片（39元/59元/99元），统一余额池同时覆盖问答+审查
    3. 管理员角色看到 "您无限制" 的提示，不展示购买按钮
  ==================================================================
-->
<template>
  <div class="sub-page">
    <!-- ===== 当前状态卡片 ===== -->
    <div class="status-card" :class="{ admin: quota.unlimited }">
      <div class="status-left">
        <div class="status-icon">
          <el-icon v-if="quota.unlimited"><Trophy /></el-icon>
          <el-icon v-else-if="quota.plan_type !== 'free'"><Medal /></el-icon>
          <el-icon v-else><Wallet /></el-icon>
        </div>
        <div class="status-info">
          <div class="status-title">
            <span v-if="quota.unlimited">问答 & 合同审查次数无限制</span>
            <span v-else-if="quota.plan_type === 'free'">当前：免费版</span>
            <span v-else>当前套餐：{{ quota.plan_label }}</span>
          </div>
          <div class="status-tip">{{ quota.tip || '加载中...' }}</div>
        </div>
      </div>
      <div class="status-right">
        <div v-if="quota.unlimited" class="unlimited-badge">
          <el-icon><Star /></el-icon>
          您当前角色问答 & 审查均无限制
        </div>
        <div v-else class="quota-stats">
          <!-- 问答 -->
          <div class="stat">
            <div class="stat-num">
              {{ quota.qa.daily_limit === -1 ? '∞' : quota.qa.daily_remaining }}
            </div>
            <div class="stat-label">
              问答剩余
              <template v-if="quota.qa.daily_limit !== -1"> / {{ quota.qa.daily_limit }} 次/天</template>
              <template v-else>（付费套餐·不限）</template>
            </div>
          </div>
          <!-- 合同审查 -->
          <div class="stat">
            <div class="stat-num">
              {{ quota.contract.daily_limit === -1 ? '∞' : quota.contract.daily_remaining }}
            </div>
            <div class="stat-label">
              审查剩余
              <template v-if="quota.contract.daily_limit !== -1"> / {{ quota.contract.daily_limit }} 次/天</template>
              <template v-else>（付费套餐·不限）</template>
            </div>
          </div>
          <!-- 付费余额 -->
          <div v-if="quota.total_balance > 0" class="stat bonus">
            <div class="stat-num">+{{ quota.total_balance }}</div>
            <div class="stat-label">付费余额（问答+审查共用）</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 套餐卡片区（仅普通用户展示） ===== -->
    <template v-if="!quota.unlimited">
      <div class="section-title">
        <span>免费额度不够用？升级套餐，问答 & 合同审查都能用</span>
        <span class="section-sub">真实支付接入后将替换为官方支付渠道</span>
      </div>
      <div class="plan-grid">
        <!-- 免费版卡片 -->
        <div class="plan-card free-card" :class="{ current: quota.plan_type === 'free' && quota.total_balance === 0 }">
          <div class="ribbon free-ribbon">当前方案</div>
          <div class="plan-header">
            <div class="plan-label">免费版</div>
            <el-tag size="small" type="info" effect="plain">永久免费</el-tag>
          </div>
          <div class="plan-body">
            <div class="plan-price">
              <span class="currency">¥</span>
              <span class="num">0</span>
            </div>
            <!-- 双功能额度 -->
            <div class="free-quota-pair">
              <div class="fq-item">
                <span class="fq-num">{{ FREE_DAILY_QA }}</span>
                <span class="fq-unit">次问答 / 天</span>
              </div>
              <div class="fq-plus">+</div>
              <div class="fq-item">
                <span class="fq-num">{{ FREE_DAILY_CONTRACT }}</span>
                <span class="fq-unit">次审查 / 天</span>
              </div>
            </div>
          </div>
          <!-- 今日用量进度条（问答） -->
          <div v-if="quota.plan_type === 'free' && quota.total_balance === 0" class="free-usage">
            <div class="usage-label">问答</div>
            <div class="usage-row">
              <span>今日已用</span>
              <span class="usage-num">{{ quota.qa.daily_used || 0 }} / {{ FREE_DAILY_QA }}</span>
            </div>
            <el-progress
              :percentage="qaUsagePercent"
              :stroke-width="6"
              :color="qaUsagePercent >= 80 ? '#ef4444' : '#f59e0b'"
              :show-text="false"
            />
            <div class="usage-label">合同审查</div>
            <div class="usage-row">
              <span>今日已用</span>
              <span class="usage-num">{{ quota.contract.daily_used || 0 }} / {{ FREE_DAILY_CONTRACT }}</span>
            </div>
            <el-progress
              :percentage="ctrUsagePercent"
              :stroke-width="6"
              :color="ctrUsagePercent >= 80 ? '#ef4444' : '#f59e0b'"
              :show-text="false"
            />
            <div class="usage-tip">额度用完当日无法再使用，明日 0 点自动重置</div>
          </div>
          <div class="plan-features">
            <div class="feature-item muted"><el-icon><Close /></el-icon>问答上限 {{ FREE_DAILY_QA }} 次/天 · 审查上限 {{ FREE_DAILY_CONTRACT }} 次/天</div>
            <div class="feature-item muted"><el-icon><Close /></el-icon>用完当日即止，不跨日累积</div>
            <div class="feature-item muted"><el-icon><Close /></el-icon>无付费余额</div>
          </div>
          <div class="superpose-tip">💡 升级后统一余额池，问答和审查都能用</div>
          <el-button class="buy-btn" plain disabled>基础权益</el-button>
        </div>

        <!-- 三张付费套餐卡 -->
        <div
          v-for="p in plans"
          :key="p.plan_type"
          class="plan-card"
          :class="{ featured: p.plan_type === 'pro' }"
        >
          <div v-if="p.plan_type === 'pro'" class="ribbon">热门推荐</div>
          <div v-else-if="p.plan_type === 'elite'" class="ribbon elite-ribbon">最划算</div>
          <div class="plan-header">
            <div class="plan-label">{{ p.label }}</div>
            <el-tag size="small" :type="p.tag" effect="dark">30 天有效</el-tag>
          </div>
          <div class="plan-body">
            <div class="plan-price">
              <span class="currency">¥</span>
              <span class="num">{{ p.price }}</span>
            </div>
            <!-- 套餐额度（统一余额池） -->
            <div class="plan-quota">
              <span class="qnum">{{ p.bonus_quota }}</span>
              <span class="qunit">次 · 问答 + 审查共用</span>
            </div>
            <div class="per-price">
              折合 ¥{{ perPrice(p) }} / 次（问答+审查都能用）
            </div>
          </div>
          <div class="plan-features">
            <div class="feature-item"><el-icon><Check /></el-icon>叠加免费 {{ FREE_DAILY_QA }} 次问答 + {{ FREE_DAILY_CONTRACT }} 次审查/天</div>
            <div class="feature-item"><el-icon><Check /></el-icon>统一余额池，问答 + 审查自由分配</div>
            <div class="feature-item"><el-icon><Check /></el-icon>余额跨日累积 · 支持叠加购买</div>
          </div>
          <el-button
            type="primary"
            class="buy-btn"
            :loading="buying === p.plan_type"
            @click="doPurchase(p.plan_type)"
          >立即升级</el-button>
        </div>
      </div>

      <div class="faq-title">常见问题</div>
      <el-collapse class="faq-collapse">
        <el-collapse-item title="套餐有效期如何计算？" name="1">
          从支付成功时刻起 30 天内有效。期间问答和合同审查都从同一余额池扣，用完或到期后恢复免费版。
        </el-collapse-item>
        <el-collapse-item title="问答和合同审查共用余额吗？" name="2">
          是的。付费套餐的统一余额池可以自由分配给问答或合同审查，想用在哪用在哪。
        </el-collapse-item>
        <el-collapse-item title="免费部分和付费部分怎么扣？" name="3">
          购买付费套餐后，每天仍然有免费额度（问答 {{ FREE_DAILY_QA }} + 审查 {{ FREE_DAILY_CONTRACT }}），
          同时付费余额也可以自由使用，不受每日限制——相当于"两层额度"同时生效。
        </el-collapse-item>
        <el-collapse-item title="可以叠加购买吗？" name="4">
          可以。购买新套餐会把次数累加至当前余额，有效期顺延 30 天。
        </el-collapse-item>
        <el-collapse-item title="管理员和法务负责人需要购买吗？" name="5">
          不需要。管理员和法务负责人问答 + 合同审查均无限制。
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Trophy, Medal, Wallet, Star, Check, Close } from '@element-plus/icons-vue'
import { get, post } from '../api/client'

/** 与后端 subscription/service.py 保持同步的常量 */
const FREE_DAILY_QA = 20
const FREE_DAILY_CONTRACT = 10

const quota = reactive<any>({
  unlimited: false,
  qa: { daily_limit: FREE_DAILY_QA, daily_used: 0, daily_remaining: FREE_DAILY_QA },
  contract: { daily_limit: FREE_DAILY_CONTRACT, daily_used: 0, daily_remaining: FREE_DAILY_CONTRACT },
  total_balance: 0,
})
const plans = ref<any[]>([])
const buying = ref<string | null>(null)

const qaUsagePercent = computed(() => {
  const used = quota.qa?.daily_used || 0
  const limit = FREE_DAILY_QA
  return Math.min(100, Math.round((used / limit) * 100))
})
const ctrUsagePercent = computed(() => {
  const used = quota.contract?.daily_used || 0
  const limit = FREE_DAILY_CONTRACT
  return Math.min(100, Math.round((used / limit) * 100))
})

/** 付费套餐单次折算价 */
function perPrice(p: any): string {
  if (!p.bonus_quota) return '-'
  return (p.price / p.bonus_quota).toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

async function loadQuota() {
  const data = await get<any>('/quota/status')
  Object.assign(quota, data)
}

async function loadPlans() {
  const data = await get<any>('/quota/plans')
  // 只展示付费套餐；后端 plan list 已改字段名 bonus_quota
  plans.value = (data.items || []).filter((p: any) => p.price > 0)
}

async function doPurchase(planType: string) {
  buying.value = planType
  try {
    const data = await post<any>('/quota/purchase', { plan_type: planType })
    ElMessageBox.alert(
      `🎉 购买成功！\n\n${data.plan_label}\n+${data.added_quota} 次（问答+审查共用）\n余额：${data.new_balance}\n有效期至 ${(data.plan_expire_at || '').substring(0, 10)}\n订单号 ${data.order_no}`,
      '购买成功',
      { confirmButtonText: '好的', type: 'success' }
    )
    await loadQuota()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '购买失败')
  } finally {
    buying.value = null
  }
}

onMounted(() => { loadQuota(); loadPlans() })
</script>

<style scoped>
/* ===== 状态卡片 ===== */
.sub-page { padding: 0; }
.status-card {
  display: flex; justify-content: space-between; align-items: center; gap: 24px;
  padding: 24px 28px; border-radius: 18px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.08), rgba(124, 58, 237, 0.08));
  border: 1px solid var(--zft-border-strong); margin-bottom: 28px;
}
.status-card.admin {
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(59, 130, 246, 0.1));
}
.status-left { display: flex; align-items: center; gap: 20px; }
.status-icon {
  width: 56px; height: 56px; border-radius: 16px;
  background: var(--zft-grad-brand); color: #fff;
  display: grid; place-items: center; font-size: 28px;
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.35);
}
.status-info .status-title { font-size: 20px; font-weight: 700; }
.status-info .status-tip { font-size: 13px; color: var(--zft-text-dim); margin-top: 4px; }
.quota-stats { display: flex; gap: 24px; align-items: center; }
.stat { text-align: center; min-width: 90px; }
.stat-num { font-size: 26px; font-weight: 800; color: var(--zft-primary-text); line-height: 1.1; }
.stat.bonus .stat-num { color: #f59e0b; }
.stat-label { font-size: 12px; color: var(--zft-text-dim); margin-top: 4px; }
.unlimited-badge {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 20px; border-radius: 24px;
  background: linear-gradient(135deg, #10b981, #3b82f6);
  color: #fff; font-weight: 600; font-size: 13px;
}

/* ===== 套餐网格 ===== */
.section-title {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 18px;
}
.section-title span:first-child { font-size: 17px; font-weight: 700; }
.section-sub { font-size: 12px; color: var(--zft-text-dim); }
.plan-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px;
}
@media (max-width: 1100px) { .plan-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px)  { .plan-grid { grid-template-columns: 1fr; } }

.plan-card {
  position: relative; border-radius: 16px; padding: 20px;
  background: var(--zft-card); border: 1.5px solid var(--zft-border);
  transition: all .25s ease; display: flex; flex-direction: column;
}
.plan-card:hover { transform: translateY(-4px); border-color: var(--zft-primary); box-shadow: var(--zft-shadow-float); }
.plan-card.featured { border-color: #f59e0b; }
.plan-card.free-card.current { border-color: var(--zft-border-strong); background: var(--zft-panel-deep); }
.ribbon {
  position: absolute; top: -10px; right: 12px;
  padding: 3px 12px; border-radius: 10px;
  background: #f59e0b; color: #fff; font-size: 12px; font-weight: 600;
}
.el-tag.ribbon { position: absolute; top: -10px; right: 12px; padding: 3px 12px; font-size: 12px; }
.el-tag.el-tag--info.free-ribbon { position: absolute; top: -10px; right: 12px; padding: 3px 12px; font-size: 12px; }
.el-tag.el-tag--success, .el-tag.el-tag--warning, .el-tag.el-tag--danger { font-size: 12px; }
/* 让 ribbon 正常显示 */
.plan-card .ribbon, .plan-card [class*="ribbon"] {
  position: absolute; top: -10px; right: 12px;
}
.plan-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.plan-label { font-size: 16px; font-weight: 700; }
.plan-body { margin-bottom: 14px; }
.plan-price { display: flex; align-items: baseline; margin-bottom: 8px; }
.plan-price .currency { font-size: 20px; color: var(--zft-text-dim); margin-right: 2px; }
.plan-price .num { font-size: 38px; font-weight: 800; background: var(--zft-grad-brand); -webkit-background-clip: text; background-clip: text; color: transparent; }
.plan-quota { display: flex; align-items: baseline; gap: 6px; margin-bottom: 6px; }
.plan-quota .qnum { font-size: 22px; font-weight: 700; color: var(--zft-text); }
.plan-quota .qunit { font-size: 13px; color: var(--zft-text-dim); }
.per-price { font-size: 12px; color: var(--zft-text-dim); }

/* 免费版卡片的"问答 + 审查"并排额度 */
.free-quota-pair {
  display: flex; align-items: center; gap: 10px;
  background: rgba(100, 116, 139, 0.08);
  border-radius: 10px; padding: 10px 14px; margin-bottom: 6px;
}
.fq-item { flex: 1; text-align: center; }
.fq-num { font-size: 22px; font-weight: 800; color: var(--zft-primary-text); display: block; line-height: 1.1; }
.fq-unit { font-size: 12px; color: var(--zft-text-dim); }
.fq-plus { font-size: 18px; font-weight: 700; color: var(--zft-text-dim); }

/* 免费用量进度条 */
.free-usage { margin-bottom: 14px; padding: 12px 14px; background: var(--zft-panel-deep); border-radius: 10px; }
.usage-label { font-size: 12px; font-weight: 600; color: var(--zft-text-dim); margin-top: 2px; }
.usage-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--zft-text-dim); margin: 4px 0 6px; }
.usage-num { color: var(--zft-text-regular); font-weight: 600; }
.usage-tip { font-size: 11px; color: var(--zft-text-dim); margin-top: 6px; }

.plan-features {
  display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px; flex: 1;
}
.feature-item { display: flex; gap: 6px; font-size: 13px; color: var(--zft-text-regular); }
.feature-item .el-icon { color: #16a34a; }
.feature-item.muted { color: var(--zft-text-dim); }
.feature-item.muted .el-icon { color: #94a3b8; }
.superpose-tip {
  font-size: 12px; color: #4f46e5; background: #eef2ff;
  padding: 8px 12px; border-radius: 8px; line-height: 1.5;
  margin-bottom: 12px;
}
.superpose-tip strong { font-weight: 700; }
.buy-btn { width: 100%; font-weight: 600; border-radius: 12px; }

/* ===== FAQ ===== */
.faq-title { font-size: 16px; font-weight: 600; margin: 28px 0 14px; }
.faq-collapse { border: 1px solid var(--zft-border); border-radius: 12px; }
</style>
