<script setup lang="ts">
import { PhPencilSimple, PhCheck, PhX, PhArrowSquareOut } from '@phosphor-icons/vue'
import type { Task } from '../types'
import { formatDuration, formatTranscriptionDuration, formatConversionRatio, formatDateTime, isLinkableSource } from '../utils/formatters'
import { ref, nextTick } from 'vue'

const props = defineProps<{
  task: Task
  topic: string
  summaryWordCount?: number
  isEditingTopic?: boolean
  editingTopicValue?: string
}>()

const emit = defineEmits<{
  'start-edit-topic': []
  'save-topic': []
  'cancel-edit-topic': []
  'update:editing-topic-value': [value: string]
}>()

const inputRef = ref<HTMLInputElement | null>(null)

const startEditingTopic = () => {
  emit('start-edit-topic')
  nextTick(() => {
    inputRef.value?.focus()
  })
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 视频标题 -->
    <h1 class="text-2xl md:text-3xl font-bold text-slate-800">
      {{ task.title || '未命名视频' }}
    </h1>
    <!-- AI要点总结（副标题，可编辑） -->
    <div v-if="!isEditingTopic" class="flex items-center gap-2 -mt-2">
      <p v-if="topic" class="text-sm text-slate-500">{{ topic }}</p>
      <button
        @click="startEditingTopic"
        class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border border-gray-200 bg-white text-slate-400 hover:text-primary hover:border-blue-200 hover:bg-blue-50 transition-colors text-xs"
        title="编辑要点总结"
      >
        <PhPencilSimple :size="14" />
        <span>编辑要点</span>
      </button>
    </div>
    <p v-if="!topic && !isEditingTopic" class="text-sm text-slate-400 -mt-2">暂无要点总结，点击上方按钮添加</p>
    <!-- 编辑状态 -->
    <div v-if="isEditingTopic" class="flex items-center gap-2 -mt-2">
      <input
        ref="inputRef"
        :value="editingTopicValue"
        type="text"
        class="flex-1 px-3 py-2 text-xl font-bold border border-blue-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-200 bg-white"
        @input="$emit('update:editing-topic-value', ($event.target as HTMLInputElement).value)"
        @keyup.enter="$emit('save-topic')"
        @keyup.esc="$emit('cancel-edit-topic')"
        placeholder="输入要点总结..."
      />
      <button @click="$emit('save-topic')" class="text-emerald-600 hover:bg-emerald-50 p-2 rounded-lg" title="保存">
        <PhCheck :size="20" />
      </button>
      <button @click="$emit('cancel-edit-topic')" class="text-slate-400 hover:bg-slate-100 p-2 rounded-lg" title="取消">
        <PhX :size="20" />
      </button>
    </div>

    <!-- 视频链接 -->
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span class="text-slate-400">视频链接:</span>
      <a v-if="isLinkableSource(task.video_url)" :href="task.video_url" target="_blank" class="text-slate-600 hover:text-primary hover:underline truncate flex items-center gap-1 max-w-md">
        {{ task.video_url }}
        <PhArrowSquareOut :size="12" />
      </a>
      <span v-else class="text-slate-600 truncate max-w-md">{{ task.video_url }}</span>
      <template v-if="task.author_name">
        <span class="text-slate-400">By</span>
        <a
          v-if="task.author_url"
          :href="task.author_url"
          target="_blank"
          class="text-slate-600 hover:text-primary hover:underline truncate max-w-[220px]"
        >
          {{ task.author_name }}
        </a>
        <span v-else class="text-slate-600 truncate max-w-[220px]">
          {{ task.author_name }}
        </span>
      </template>
    </div>

    <!-- 元信息卡片 -->
    <div class="flex flex-col gap-3 text-sm text-slate-600">
      <!-- 第一行：视频时长、转录耗时、转换比 -->
      <div class="flex items-center gap-6 flex-wrap">
        <span class="flex items-center">
          视频时长 <strong class="ml-1 text-slate-800 font-semibold">{{ formatDuration(task.audio_duration) }}</strong>
        </span>
        <span class="flex items-center">
          转录耗时 <strong class="ml-1 text-slate-800 font-semibold">{{ formatTranscriptionDuration(task.transcription_time) }}</strong>
        </span>
        <span class="flex items-center">
          转换比 <strong class="ml-1 text-slate-800 font-semibold">{{ formatConversionRatio(task.audio_duration, task.transcription_time) }}x</strong>
        </span>
      </div>

      <!-- 第二行：总字数、生成时间 -->
      <div class="flex items-center gap-6 flex-wrap">
        <span v-if="summaryWordCount !== undefined" class="flex items-center">
          总字数 <strong class="ml-1 text-slate-800 font-semibold">{{ summaryWordCount }}</strong>
        </span>
        <span v-if="task.summary_mode" class="flex items-center">
          总结模式
          <strong class="ml-1 text-slate-800 font-semibold">
            {{
              task.summary_mode === 'agent'
                ? 'Agent 增强模式'
                : task.summary_mode === 'standard'
                  ? '通用模式'
                  : '自动模式'
            }}
          </strong>
        </span>
        <span v-if="task.summary_chunk_total && task.summary_chunk_total > 0" class="flex items-center">
          分块进度
          <strong class="ml-1 text-slate-800 font-semibold">
            {{ task.summary_chunk_done || 0 }}/{{ task.summary_chunk_total }}
          </strong>
        </span>
        <span class="flex items-center">
          生成时间 <strong class="ml-1 text-slate-800 font-semibold">{{ formatDateTime(task.created_at) }}</strong>
        </span>
      </div>
    </div>
  </div>
</template>
