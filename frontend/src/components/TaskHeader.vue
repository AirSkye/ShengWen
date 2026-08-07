<script setup lang="ts">
import { PhMonitorPlay, PhPencilSimple, PhCheck, PhX, PhArrowSquareOut, PhList } from '@phosphor-icons/vue'
import type { Task } from '../types'
import { ref, nextTick } from 'vue'
import { isLinkableSource } from '../utils/formatters'

interface Props {
  task: Task
  topic: string
  isEditingTopic: boolean
  editingTopicValue: string
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'start-edit-topic': []
  'save-topic': []
  'cancel-edit-topic': []
  'update:editing-topic-value': [value: string]
  'toggle-sidebar': []
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
  <header class="h-16 bg-white/60 backdrop-blur-sm border-b border-gray-200 px-4 md:px-8 flex items-center justify-between shadow-sm shrink-0 sticky top-0 z-20">
    <div class="flex items-center gap-3 md:gap-4 overflow-hidden">
      <!-- 移动端侧边栏切换按钮 -->
      <button @click="$emit('toggle-sidebar')" class="md:hidden p-1.5 -ml-1.5 text-slate-600 hover:bg-gray-100 rounded-lg active:scale-90 transition-transform">
        <PhList :size="24" />
      </button>

      <div class="p-2 bg-gray-100 rounded-lg text-slate-500 hidden md:block">
        <PhMonitorPlay :size="20" />
      </div>
      <div class="min-w-0 flex-1 mr-4">
        <div v-if="!isEditingTopic" class="flex items-center gap-2 group">
          <h2 class="text-sm md:text-base font-bold text-slate-800 truncate max-w-[200px] md:max-w-md" :title="topic || task.title || '任务详情'">
            {{ topic || task.title || '任务详情' }}
          </h2>
          <button @click="startEditingTopic" class="opacity-0 group-hover:opacity-100 transition-opacity text-slate-400 hover:text-primary p-1 rounded hover:bg-slate-100" title="编辑主题">
            <PhPencilSimple :size="16" />
          </button>
        </div>
        <div v-else class="flex items-center gap-1 mb-0.5">
          <input
            ref="inputRef"
            :value="editingTopicValue"
            type="text"
            class="flex-1 min-w-[150px] max-w-md px-2 py-1 text-sm border border-primary rounded focus:outline-none focus:ring-1 focus:ring-primary bg-white"
            @input="$emit('update:editing-topic-value', ($event.target as HTMLInputElement).value)"
            @keyup.enter="$emit('save-topic')"
            @keyup.esc="$emit('cancel-edit-topic')"
            placeholder="输入主题..."
          />
          <button @click="$emit('save-topic')" class="text-emerald-600 hover:bg-emerald-50 p-1 rounded" title="保存">
            <PhCheck :size="18" />
          </button>
          <button @click="$emit('cancel-edit-topic')" class="text-slate-400 hover:bg-slate-100 p-1 rounded" title="取消">
            <PhX :size="18" />
          </button>
        </div>
        <div class="text-[10px] md:text-xs truncate flex items-center gap-1 flex-wrap">
          <a v-if="isLinkableSource(task.video_url)" :href="task.video_url" target="_blank" class="text-primary hover:underline truncate flex items-center gap-1">
            {{ task.video_url }} <PhArrowSquareOut :size="12" />
          </a>
          <span v-else class="text-slate-500 truncate">{{ task.video_url }}</span>
          <template v-if="task.author_name">
            <span class="text-slate-400">By</span>
            <a
              v-if="task.author_url"
              :href="task.author_url"
              target="_blank"
              class="text-primary hover:underline truncate max-w-[160px]"
            >
              {{ task.author_name }}
            </a>
            <span v-else class="text-slate-500 truncate max-w-[160px]">
              {{ task.author_name }}
            </span>
          </template>
        </div>
      </div>
    </div>
  </header>
</template>
