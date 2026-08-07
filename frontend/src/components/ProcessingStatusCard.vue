<script setup lang="ts">
import { computed } from 'vue'
import {
  PhBrain,
  PhCheck,
  PhCloudArrowUp,
  PhSparkle,
  PhTimer,
  PhWaveform,
} from '@phosphor-icons/vue'
import type { Task, TranscriptionMeta } from '../types'
import { TaskStatus } from '../types'

const props = withDefaults(defineProps<{
  task: Task
  meta: TranscriptionMeta
  providerLabel: string
  message: string
  compact?: boolean
}>(), {
  compact: false,
})

const stage = computed(() => String(props.meta.stage || ''))
const isSummarizing = computed(() => props.task.status === TaskStatus.SUMMARIZING)
const isTingwu = computed(() => (
  props.meta.provider === 'tingwu'
  || ['creating', 'created', 'uploading', 'upload_complete', 'syncing', 'polling', 'fetching_result'].includes(stage.value)
))

const normalizeProgress = (value: unknown) => {
  const numeric = Number(value || 0)
  const percent = numeric > 0 && numeric <= 1 ? numeric * 100 : numeric
  return Math.max(0, Math.min(percent, 100))
}

const determinateProgress = computed<number | null>(() => {
  if (props.task.status === TaskStatus.COMPLETED) return 100

  if (isSummarizing.value) {
    const total = Number(props.task.summary_chunk_total || 0)
    const done = Number(props.task.summary_chunk_done || 0)
    if (total > 0 && done > 0) return Math.round(Math.min(done / total, 1) * 100)
    const taskProgress = normalizeProgress(props.task.progress)
    return taskProgress > 0 ? Math.round(taskProgress) : null
  }

  if (props.task.status === TaskStatus.TRANSCRIBING) {
    if (stage.value === 'polling') {
      const remoteProgress = normalizeProgress(props.meta.remote_progress)
      return remoteProgress > 0 ? Math.round(remoteProgress) : null
    }
    if (['uploading', 'upload_complete'].includes(stage.value)) {
      const uploadProgress = normalizeProgress(props.meta.upload_progress)
      return uploadProgress > 0 ? Math.round(uploadProgress) : null
    }
    if (stage.value === 'fetching_result') return 98
    if (stage.value === 'completed') return 100
    return null
  }

  const taskProgress = normalizeProgress(props.task.progress)
  return taskProgress > 0 ? Math.round(taskProgress) : null
})

const activeStepIndex = computed(() => {
  if (props.task.status === TaskStatus.COMPLETED) return 3
  if (isSummarizing.value) return 2
  if (props.task.status === TaskStatus.TRANSCRIBING) return 1
  return 0
})

const steps = computed(() => [
  {
    label: '准备媒体',
    detail: props.task.status === TaskStatus.DOWNLOADING ? '获取源文件' : '校验并上传',
    icon: PhCloudArrowUp,
  },
  {
    label: isTingwu.value ? '听悟转写' : '语音转写',
    detail: isTingwu.value ? '云端识别与字幕' : '提取语音内容',
    icon: PhWaveform,
  },
  {
    label: props.task.summary_mode === 'agent' ? 'Agent 总结' : '通用总结',
    detail: props.task.summary_mode === 'agent' ? '分块理解与整合' : '生成结构化笔记',
    icon: props.task.summary_mode === 'agent' ? PhBrain : PhSparkle,
  },
])

const stepState = (index: number) => {
  if (activeStepIndex.value > index) return 'complete'
  if (activeStepIndex.value === index) return 'active'
  return 'pending'
}

const primaryTitle = computed(() => {
  if (isSummarizing.value) {
    return props.task.summary_mode === 'agent'
      ? 'Agent 正在深度整理内容'
      : '正在生成通用总结'
  }
  if (stage.value === 'polling') return '听悟正在云端识别'
  if (['uploading', 'upload_complete', 'syncing'].includes(stage.value)) return '媒体正在进入听悟'
  if (stage.value === 'fetching_result') return '正在收取转录与字幕'
  if (props.task.status === TaskStatus.UPLOADING) return '正在接收上传文件'
  if (props.task.status === TaskStatus.DOWNLOADING) return '正在准备远程媒体'
  return '处理流水线已启动'
})

const progressText = computed(() => {
  if (determinateProgress.value !== null) return `${determinateProgress.value}%`
  if (isSummarizing.value) return '生成中'
  if (stage.value === 'polling') return '识别中'
  return '处理中'
})

const progressHint = computed(() => {
  if (isSummarizing.value && props.task.summary_mode === 'agent') {
    const total = Number(props.task.summary_chunk_total || 0)
    const done = Number(props.task.summary_chunk_done || 0)
    return total > 0 ? `已完成 ${done}/${total} 个内容分块` : '正在分析结构并规划内容分块'
  }
  if (isSummarizing.value) return '总结内容会持续生成并实时展示'
  if (stage.value === 'polling') {
    const pollCount = Number(props.meta.poll_count || 0)
    return pollCount > 0 ? `已完成 ${pollCount} 次状态同步` : '正在等待听悟返回识别状态'
  }
  if (['uploading', 'upload_complete'].includes(stage.value)) return '文件仅用于本次转录任务'
  return '各阶段状态会在这里自动更新'
})

const displayServiceLabel = computed(() => {
  if (!isSummarizing.value) return props.providerLabel
  return props.task.summary_mode === 'agent' ? 'Agent 总结引擎' : '通用总结引擎'
})

const formatDuration = (seconds: number) => {
  const total = Math.max(0, Math.floor(seconds || 0))
  const minutes = Math.floor(total / 60)
  const remain = total % 60
  return minutes > 0 ? `${minutes}分 ${remain}秒` : `${remain}秒`
}

const formatBytes = (bytes: number) => {
  const value = Math.max(0, Number(bytes || 0))
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

const stageLabel = computed(() => {
  const labels: Record<string, string> = {
    browser_upload: '浏览器上传',
    browser_upload_complete: '上传完成',
    creating: '创建任务',
    created: '任务已创建',
    uploading: '上传听悟',
    upload_complete: '云端已接收',
    syncing: '同步任务',
    polling: '云端识别',
    fetching_result: '获取结果',
    completed: '转录完成',
  }
  if (isSummarizing.value) return props.task.summary_mode === 'agent' ? 'Agent 总结' : '通用总结'
  return labels[stage.value] || '任务处理中'
})

const metrics = computed(() => {
  const values: Array<{ label: string; value: string }> = []
  const elapsedSeconds = Number(props.meta.elapsed_seconds || 0)
  const pollCount = Number(props.meta.poll_count || 0)
  const uploadedBytes = Number(props.meta.uploaded_bytes || 0)
  const totalBytes = Number(props.meta.total_bytes || 0)

  if (elapsedSeconds > 0) values.push({ label: '云端耗时', value: formatDuration(elapsedSeconds) })
  if (pollCount > 0) values.push({ label: '状态同步', value: `${pollCount} 次` })
  if (totalBytes > 0) {
    values.push({
      label: '媒体传输',
      value: uploadedBytes > 0 ? `${formatBytes(uploadedBytes)} / ${formatBytes(totalBytes)}` : formatBytes(totalBytes),
    })
  }
  if (isSummarizing.value) {
    values.push({
      label: '总结策略',
      value: props.task.summary_mode === 'agent' ? 'Agent 深度模式' : '通用模式',
    })
  }
  values.push({ label: '当前阶段', value: stageLabel.value })
  values.push({ label: '处理服务', value: displayServiceLabel.value })
  return values.slice(0, 3)
})

const ringStyle = computed(() => ({
  '--processing-angle': `${(determinateProgress.value || 0) * 3.6}deg`,
}))
</script>

<template>
  <section
    class="processing-status-card relative overflow-hidden border border-blue-100/80 bg-white shadow-[0_24px_70px_-34px_rgba(37,99,235,0.45)]"
    :class="compact ? 'rounded-2xl px-5 py-4' : 'rounded-[28px] px-5 py-6 md:px-8 md:py-8'"
  >
    <div class="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-cyan-200/30 blur-3xl"></div>
    <div class="pointer-events-none absolute -bottom-28 -left-16 h-64 w-64 rounded-full bg-indigo-200/30 blur-3xl"></div>

    <template v-if="compact">
      <div class="relative flex items-center gap-4">
        <div class="processing-compact-icon flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-blue-600 text-white shadow-lg shadow-blue-200/70">
          <PhBrain v-if="isSummarizing" :size="22" weight="duotone" />
          <PhWaveform v-else :size="22" weight="bold" />
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center justify-between gap-4">
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-slate-800">{{ primaryTitle }}</p>
              <p class="mt-0.5 truncate text-xs text-slate-500">{{ message }} · {{ progressHint }}</p>
            </div>
            <span class="shrink-0 rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold tabular-nums text-blue-600">
              {{ progressText }}
            </span>
          </div>
          <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-blue-100/80">
            <div
              v-if="determinateProgress !== null"
              class="h-full rounded-full bg-gradient-to-r from-cyan-400 via-blue-500 to-indigo-500 transition-[width] duration-500"
              :style="{ width: `${determinateProgress}%` }"
            ></div>
            <div v-else class="processing-indeterminate h-full w-2/5 rounded-full bg-gradient-to-r from-cyan-400 via-blue-500 to-indigo-500"></div>
          </div>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="relative flex flex-wrap items-center justify-between gap-3">
        <div class="inline-flex items-center gap-2 rounded-full border border-blue-100 bg-white/80 px-3 py-1.5 text-[11px] font-semibold tracking-[0.16em] text-blue-600 shadow-sm backdrop-blur">
          <span class="processing-live-dot h-2 w-2 rounded-full bg-cyan-400"></span>
          SHENGWEN PIPELINE
        </div>
        <div class="inline-flex items-center gap-2 rounded-full bg-slate-900 px-3 py-1.5 text-xs font-medium text-white shadow-lg shadow-slate-200">
          <PhTimer :size="14" />
          {{ stageLabel }}
        </div>
      </div>

      <div class="relative mt-7 grid items-center gap-7 md:grid-cols-[180px_minmax(0,1fr)]">
        <div class="mx-auto flex flex-col items-center">
          <div class="processing-ring-shell relative h-36 w-36" :style="ringStyle">
            <div
              class="processing-ring-track absolute inset-0 rounded-full"
              :class="{ 'processing-ring-track--indeterminate': determinateProgress === null }"
            ></div>
            <div class="absolute inset-[10px] flex flex-col items-center justify-center rounded-full border border-white/90 bg-white/95 shadow-inner shadow-blue-100">
              <span v-if="determinateProgress !== null" class="text-3xl font-bold tracking-tight text-slate-800 tabular-nums">
                {{ determinateProgress }}<span class="text-base text-blue-500">%</span>
              </span>
              <div v-else class="processing-wave-bars flex h-8 items-end gap-1">
                <span v-for="index in 5" :key="index" :style="{ animationDelay: `${index * 90}ms` }"></span>
              </div>
              <span class="mt-1 text-[10px] font-semibold tracking-[0.16em] text-slate-400">
                {{ determinateProgress !== null ? '阶段进度' : progressText }}
              </span>
            </div>
          </div>
          <p class="mt-3 text-center text-xs text-slate-400">{{ displayServiceLabel }}</p>
        </div>

        <div class="min-w-0">
          <h3 class="text-xl font-semibold tracking-tight text-slate-800 md:text-2xl">{{ primaryTitle }}</h3>
          <p class="mt-2 text-sm leading-6 text-slate-500">{{ message }}</p>
          <p class="mt-1 text-xs text-blue-600/80">{{ progressHint }}</p>

          <div class="mt-6 grid grid-cols-3 gap-2 md:gap-3">
            <div
              v-for="(item, index) in steps"
              :key="item.label"
              class="processing-step relative overflow-hidden rounded-2xl border px-2.5 py-3 transition-all duration-500 md:px-3.5"
              :class="{
                'border-emerald-100 bg-emerald-50/70 text-emerald-700': stepState(index) === 'complete',
                'processing-step--active border-blue-200 bg-blue-50 text-blue-700 shadow-sm shadow-blue-100': stepState(index) === 'active',
                'border-slate-100 bg-slate-50/70 text-slate-400': stepState(index) === 'pending',
              }"
            >
              <div class="flex items-center gap-2">
                <div
                  class="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl"
                  :class="{
                    'bg-emerald-100': stepState(index) === 'complete',
                    'bg-blue-100': stepState(index) === 'active',
                    'bg-slate-100': stepState(index) === 'pending',
                  }"
                >
                  <PhCheck v-if="stepState(index) === 'complete'" :size="14" weight="bold" />
                  <component v-else :is="item.icon" :size="14" :weight="stepState(index) === 'active' ? 'fill' : 'regular'" />
                </div>
                <div class="min-w-0">
                  <p class="truncate text-[11px] font-semibold md:text-xs">{{ item.label }}</p>
                  <p class="mt-0.5 hidden truncate text-[10px] opacity-70 sm:block">{{ item.detail }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="relative mt-7 grid gap-2 sm:grid-cols-3">
        <div
          v-for="item in metrics"
          :key="item.label"
          class="rounded-2xl border border-white/80 bg-white/65 px-4 py-3 shadow-sm shadow-blue-100/50 backdrop-blur"
        >
          <p class="text-[10px] font-medium tracking-wide text-slate-400">{{ item.label }}</p>
          <p class="mt-1 truncate text-xs font-semibold text-slate-700" :title="item.value">{{ item.value }}</p>
        </div>
      </div>

      <p v-if="meta.remote_task_id" class="relative mt-4 truncate text-center font-mono text-[10px] text-slate-400" :title="meta.remote_task_id">
        TINGWU · {{ meta.remote_task_id }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.processing-status-card {
  background-image:
    linear-gradient(135deg, rgba(239, 246, 255, 0.92), rgba(255, 255, 255, 0.96) 48%, rgba(238, 242, 255, 0.9)),
    radial-gradient(circle at 1px 1px, rgba(59, 130, 246, 0.08) 1px, transparent 0);
  background-size: auto, 18px 18px;
}

.processing-ring-track {
  background: conic-gradient(
    from -35deg,
    #22d3ee 0deg,
    #3b82f6 var(--processing-angle),
    rgba(191, 219, 254, 0.65) var(--processing-angle),
    rgba(224, 231, 255, 0.48) 360deg
  );
  box-shadow: 0 18px 45px -20px rgba(37, 99, 235, 0.75);
  transition: background 500ms ease;
}

.processing-ring-track--indeterminate {
  background: conic-gradient(from 0deg, rgba(191, 219, 254, 0.35), #22d3ee, #3b82f6, #6366f1, rgba(191, 219, 254, 0.35) 78%);
  animation: processing-ring-spin 2.4s linear infinite;
}

.processing-wave-bars span {
  width: 4px;
  min-height: 8px;
  border-radius: 999px;
  background: linear-gradient(to top, #3b82f6, #22d3ee);
  animation: processing-wave 900ms ease-in-out infinite alternate;
}

.processing-live-dot {
  box-shadow: 0 0 0 0 rgba(34, 211, 238, 0.5);
  animation: processing-live 1.8s ease-out infinite;
}

.processing-step--active::after {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(110deg, transparent 20%, rgba(255, 255, 255, 0.75) 46%, transparent 72%);
  content: '';
  transform: translateX(-120%);
  animation: processing-sheen 2.8s ease-in-out infinite;
}

.processing-indeterminate {
  animation: processing-slide 1.8s ease-in-out infinite;
}

.processing-compact-icon {
  animation: processing-float 3.2s ease-in-out infinite;
}

@keyframes processing-ring-spin {
  to { transform: rotate(360deg); }
}

@keyframes processing-wave {
  from { height: 9px; opacity: 0.55; }
  to { height: 30px; opacity: 1; }
}

@keyframes processing-live {
  0% { box-shadow: 0 0 0 0 rgba(34, 211, 238, 0.5); }
  70% { box-shadow: 0 0 0 8px rgba(34, 211, 238, 0); }
  100% { box-shadow: 0 0 0 0 rgba(34, 211, 238, 0); }
}

@keyframes processing-sheen {
  0%, 35% { transform: translateX(-120%); }
  70%, 100% { transform: translateX(120%); }
}

@keyframes processing-slide {
  0% { transform: translateX(-110%); }
  50% { transform: translateX(75%); }
  100% { transform: translateX(250%); }
}

@keyframes processing-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}

@media (prefers-reduced-motion: reduce) {
  .processing-ring-track--indeterminate,
  .processing-wave-bars span,
  .processing-live-dot,
  .processing-step--active::after,
  .processing-indeterminate,
  .processing-compact-icon {
    animation: none;
  }
}
</style>
