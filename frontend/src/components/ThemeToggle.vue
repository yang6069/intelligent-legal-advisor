<!--
  ==================================================================
  智法通前端 | 主题切换控件（格式刷风格 · 小巧工具件）
  ------------------------------------------------------------------
  设计隐喻：
    仿办公软件"格式刷"工具按钮 —— 小巧、工具化、单击即刷。
    药丸形分段控件内置「日 / 月」双图标，滑块如刷头滑动到目标档位，
    顶部一道品牌渐变"刷漆条"随主题点亮，暗示"刷"上新皮肤。
  功能：
    1. 单击切换 亮色 <-> 暗色（theme store 统一管理）；
    2. 主题选择持久化 localStorage（zft_theme），刷新后保留；
    3. 滑块动画 + tooltip 提示 + 键盘可达（Enter/Space）。
  ==================================================================
-->
<template>
  <el-tooltip :content="theme.toggleTip" placement="bottom" :show-after="300">
    <button
      class="fmt-brush"
      role="switch"
      :aria-checked="theme.isDark"
      :aria-label="theme.toggleTip"
      @click="theme.toggle()"
      @keydown.enter.prevent="theme.toggle()"
      @keydown.space.prevent="theme.toggle()"
    >
      <!-- 顶部"刷漆条"：暗色时点亮青金渐变，亮色时为品牌蓝 -->
      <span class="paint-line" aria-hidden="true" />

      <!-- 双档位：太阳（亮） / 月亮（暗），滑块随主题滑动 -->
      <span class="seg seg-light" :class="{ on: !theme.isDark }">
        <el-icon><Sunny /></el-icon>
      </span>
      <span class="seg seg-dark" :class="{ on: theme.isDark }">
        <el-icon><Moon /></el-icon>
      </span>

      <!-- 滑动刷头 -->
      <span class="thumb" :class="theme.isDark ? 'to-dark' : 'to-light'" aria-hidden="true" />
    </button>
  </el-tooltip>
</template>

<script setup lang="ts">
/**
 * 主题切换控件逻辑：
 *   - 直接复用全局 theme store（无本地状态，保证多控件同源同步）；
 *   - 点击 / 回车 / 空格均可触发 toggle()，状态变化由 store 驱动 DOM。
 */
import { useThemeStore } from '../stores/theme'
import { Sunny, Moon } from '@element-plus/icons-vue'

const theme = useThemeStore()
</script>

<style scoped>
/* ===== 药丸形分段控件（工具件尺寸：紧凑小巧） ===== */
.fmt-brush {
  --brush-w: 62px;
  --brush-h: 28px;
  position: relative;
  width: var(--brush-w);
  height: var(--brush-h);
  padding: 0;
  border: 1px solid var(--zft-border-strong);
  border-radius: 999px;
  background: var(--zft-panel-deep);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  overflow: hidden;
  transition: border-color 0.25s ease, box-shadow 0.25s ease, background 0.25s ease;
}
.fmt-brush:hover {
  border-color: rgba(59, 130, 246, 0.6);
  box-shadow: 0 2px 10px rgba(59, 130, 246, 0.25);
}
.fmt-brush:focus-visible {
  outline: 2px solid var(--zft-primary);
  outline-offset: 2px;
}

/* ===== 顶部"刷漆条"（格式刷隐喻：刷过的漆痕） ===== */
.paint-line {
  position: absolute;
  top: 0; left: 8%; right: 8%;
  height: 2px;
  border-radius: 0 0 4px 4px;
  background: var(--zft-grad-brand-h);
  opacity: 0.85;
  transition: opacity 0.25s ease;
}
html.dark .paint-line {
  background: linear-gradient(90deg, #22d3ee, #e3b341);
}

/* ===== 双档位图标 ===== */
.seg {
  position: relative;
  z-index: 1;
  flex: 1;
  height: 100%;
  display: grid;
  place-items: center;
  font-size: 14px;
  /* 未选中档位：次要色，保证在两种底色上都可辨识 */
  color: var(--zft-text-faint);
  transition: color 0.25s ease, transform 0.25s ease;
}
/* 选中档位图标颜色：亮档太阳用琥珀金 / 暗档月亮用品牌蓝，
   与各自滑块底色形成足够对比 */
.seg.seg-light.on { color: #f59e0b; transform: scale(1.08); }
.seg.seg-dark.on { color: #93c5fd; transform: scale(1.08); }

/* ===== 滑动刷头 ===== */
.thumb {
  position: absolute;
  top: 2px;
  width: calc(50% - 3px);
  height: calc(100% - 4px);
  border-radius: 999px;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.25);
  transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1), background 0.28s ease;
}
.thumb.to-light {
  transform: translateX(2px);
  /* 亮色刷头：白底 + 淡金描边，太阳图标其上清晰 */
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  border: 1px solid rgba(245, 158, 11, 0.55);
}
.thumb.to-dark {
  transform: translateX(calc(100% + 4px));
  /* 暗色刷头：深蓝底，月亮图标其上清晰 */
  background: linear-gradient(135deg, #1e3a5f, #17263f);
  border: 1px solid rgba(96, 165, 250, 0.55);
}
</style>
