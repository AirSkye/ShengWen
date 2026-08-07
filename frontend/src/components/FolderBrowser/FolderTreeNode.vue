<script setup lang="ts">
import { ref, computed } from 'vue'
import { PhFolder, PhFolderOpen, PhCaretRight, PhCaretDown, PhDotsThree, PhTrash, PhPencilSimple, PhCheck, PhX } from '@phosphor-icons/vue'
import type { FolderNode, Task } from '../../types'
import TaskCard from './TaskCard.vue'

const props = defineProps<{
  folder: FolderNode
  depth: number
  tasks: Task[]
  selectedTask: Task | null
  isExpanded: boolean
  expandedMap: Record<string, boolean>
}>()

const emit = defineEmits<{
  toggle: [folderId: string]
  selectTask: [task: Task]
  deleteTask: [taskId: string]
  showInfo: [task: Task]
  rename: [folderId: string, newName: string]
  delete: [folderId: string]
  dropTask: [taskId: string, folderId: string]
  dropFolder: [childFolderId: string, parentFolderId: string]
}>()

const showMenu = ref(false)
const isRenaming = ref(false)
const renameValue = ref('')

const startRename = () => {
  renameValue.value = props.folder.name
  isRenaming.value = true
  showMenu.value = false
}

const confirmRename = () => {
  if (renameValue.value.trim()) {
    emit('rename', props.folder.id, renameValue.value.trim())
  }
  isRenaming.value = false
}

const cancelRename = () => {
  isRenaming.value = false
}

const taskCount = computed(() => props.tasks.length)

const handleDrop = (e: DragEvent) => {
  e.preventDefault()
  const data = e.dataTransfer?.getData('text/plain')
  if (!data) return
  try {
    const payload = JSON.parse(data)
    if (payload.type === 'task') {
      emit('dropTask', payload.id, props.folder.id)
    } else if (payload.type === 'folder' && payload.id !== props.folder.id) {
      emit('dropFolder', payload.id, props.folder.id)
    }
  } catch {
    emit('dropTask', data, props.folder.id)
  }
}
</script>

<template>
  <div>
    <!-- Folder row -->
    <div
      class="flex items-center gap-1.5 py-2 px-2 rounded-lg cursor-pointer hover:bg-gray-50 transition-colors group relative"
      :style="{ paddingLeft: (depth * 16 + 8) + 'px' }"
      @click="emit('toggle', folder.id)"
      @dragover.prevent
      @drop="handleDrop"
    >
      <component :is="isExpanded ? PhCaretDown : PhCaretRight" :size="14" class="text-slate-400 shrink-0" />
      <component :is="isExpanded ? PhFolderOpen : PhFolder" :size="16" :class="folder.folder_type === 'auto' ? 'text-blue-500' : 'text-slate-500'" class="shrink-0" />
      <div v-if="!isRenaming" class="text-sm font-medium text-slate-700 truncate flex-1">
        {{ folder.name }}
      </div>
      <div v-else class="flex-1 flex items-center gap-1">
        <input
          v-model="renameValue"
          class="flex-1 px-1.5 py-0.5 text-sm border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-200"
          @keyup.enter="confirmRename"
          @keyup.escape="cancelRename"
          @click.stop
        />
        <button @click.stop="confirmRename" class="text-emerald-600 hover:bg-emerald-50 p-0.5 rounded"><PhCheck :size="14" /></button>
        <button @click.stop="cancelRename" class="text-slate-400 hover:text-red-500 p-0.5 rounded"><PhX :size="14" /></button>
      </div>
      <span v-if="taskCount > 0" class="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded-full shrink-0">{{ taskCount }}</span>
      <button @click.stop="showMenu = !showMenu" class="text-slate-400 hover:text-slate-600 p-0.5 shrink-0 md:opacity-0 md:group-hover:opacity-100 md:transition-opacity">
        <PhDotsThree :size="14" />
      </button>
    </div>

    <!-- Context menu -->
    <div v-if="showMenu" class="absolute right-4 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg py-1 z-50 min-w-[120px]" @click="showMenu = false">
      <button @click="startRename" class="w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 flex items-center gap-2"><PhPencilSimple :size="14" /> 重命名</button>
      <button @click="emit('delete', folder.id)" class="w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 text-red-600 flex items-center gap-2"><PhTrash :size="14" /> 删除文件夹</button>
    </div>

    <!-- Expanded content: tasks + recursive sub-folders -->
    <div v-if="isExpanded">
      <div :style="{ paddingLeft: (depth * 16 + 24) + 'px' }" class="space-y-1.5">
        <TaskCard
          v-for="task in tasks"
          :key="task.id"
          :task="task"
          :isSelected="selectedTask?.id === task.id"
          @select="emit('selectTask', $event)"
          @delete="emit('deleteTask', $event)"
          @showInfo="emit('showInfo', $event)"
          @dragstart="emit('dropTask', $event, folder.id)"
        />
      </div>
      <FolderTreeNode
        v-for="child in folder.children"
        :key="child.id"
        :folder="child"
        :depth="depth + 1"
        :tasks="[]"
        :selectedTask="selectedTask"
        :isExpanded="!!expandedMap[child.id]"
        :expandedMap="expandedMap"
        @toggle="emit('toggle', $event)"
        @selectTask="emit('selectTask', $event)"
        @deleteTask="emit('deleteTask', $event)"
        @showInfo="emit('showInfo', $event)"
        @rename="(a: any, b: any) => emit('rename', a, b)"
        @delete="emit('delete', $event)"
        @dropTask="(a: any, b: any) => emit('dropTask', a, b)"
        @dropFolder="(a: any, b: any) => emit('dropFolder', a, b)"
      />
    </div>
  </div>
</template>

<script lang="ts">
export default { name: 'FolderTreeNode' }
</script>