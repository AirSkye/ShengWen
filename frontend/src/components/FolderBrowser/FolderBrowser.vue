<script setup lang="ts">
import { ref, computed } from 'vue'
import { PhFolderPlus } from '@phosphor-icons/vue'
import type { Task, Folder, FolderNode } from '../../types'
import FolderTreeNode from './FolderTreeNode.vue'
import TaskCard from './TaskCard.vue'
import FolderCreateDialog from './FolderCreateDialog.vue'

const props = defineProps<{
  tasks: Task[]
  folders: Folder[]
  folderTree: FolderNode[]
  selectedTask: Task | null
}>()

const emit = defineEmits<{
  selectTask: [task: Task]
  deleteTask: [taskId: string]
  showInfo: [task: Task]
  createFolder: [name: string, parentId: string | null]
  renameFolder: [folderId: string, newName: string]
  deleteFolder: [folderId: string]
  toggleFolder: [folderId: string]
  assignTaskToFolder: [taskId: string, folderId: string | null]
  moveFolder: [folderId: string, newParentId: string | null]
}>()

const expandedMap = ref<Record<string, boolean>>({})
const showCreateDialog = ref(false)

const tasksByFolder = computed(() => {
  const result = new Map<string, Task[]>()
  for (const folder of props.folders) {
    result.set(folder.id, [])
  }
  for (const task of props.tasks) {
    const fid = task.folder_id
    if (fid && result.has(fid)) {
      result.get(fid)!.push(task)
    }
  }
  return result
})

const rootTasks = computed(() => props.tasks.filter(t => !t.folder_id))

const isExpanded = (folderId: string) => !!expandedMap.value[folderId]

const toggleExpand = (folderId: string) => {
  expandedMap.value[folderId] = !expandedMap.value[folderId]
}

// Auto-expand auto-created folders on mount
for (const folder of props.folders) {
  if (folder.folder_type === 'auto') {
    expandedMap.value[folder.id] = true
  }
}

const handleTaskDragstart = (e: DragEvent, taskId: string) => {
  e.dataTransfer?.setData('text/plain', JSON.stringify({ type: 'task', id: taskId }))
}
</script>

<template>
  <div class="space-y-2">
    <!-- Top toolbar: new folder action -->
    <div v-if="!showCreateDialog" class="flex items-center px-2">
      <button
        @click="showCreateDialog = true"
        class="flex items-center gap-1.5 py-1.5 text-xs text-slate-400 hover:text-blue-500 hover:bg-blue-50/50 rounded-lg transition-colors"
      >
        <PhFolderPlus :size="14" />
        新建文件夹
      </button>
    </div>
    <FolderCreateDialog
      v-if="showCreateDialog"
      :parentId="null"
      @create="(name: string, parentId: string | null) => { emit('createFolder', name, parentId); showCreateDialog = false }"
      @cancel="showCreateDialog = false"
    />

    <!-- Folder tree -->
    <div v-if="folderTree.length > 0" class="space-y-0.5">
      <FolderTreeNode
        v-for="node in folderTree"
        :key="node.id"
        :folder="node"
        :depth="0"
        :tasks="tasksByFolder.get(node.id) || []"
        :selectedTask="selectedTask"
        :isExpanded="isExpanded(node.id)"
        :expandedMap="expandedMap"
        @toggle="toggleExpand"
        @selectTask="emit('selectTask', $event)"
        @deleteTask="emit('deleteTask', $event)"
        @showInfo="emit('showInfo', $event)"
        @rename="(a: any, b: any) => emit('renameFolder', a, b)"
        @delete="emit('deleteFolder', $event)"
        @dropTask="(a: any, b: any) => emit('assignTaskToFolder', a, b)"
        @dropFolder="(a: any, b: any) => emit('moveFolder', a, b)"
      />
    </div>

    <!-- Root-level (unassigned) tasks -->
    <div v-if="rootTasks.length > 0 || folderTree.length === 0" class="space-y-1.5">
      <div v-if="folderTree.length > 0" class="text-xs font-semibold text-slate-400 uppercase tracking-wider px-2 py-1">
        未分配
      </div>
      <div
        v-for="task in rootTasks"
        :key="task.id"
        draggable="true"
        @dragstart="handleTaskDragstart($event, task.id)"
      >
        <TaskCard
          :task="task"
          :isSelected="selectedTask?.id === task.id"
          @select="emit('selectTask', task)"
          @delete="emit('deleteTask', task.id)"
          @showInfo="emit('showInfo', task)"
        />
      </div>
    </div>

    <!-- Empty state -->
    <p v-if="tasks.length === 0" class="text-center text-gray-400 py-8 text-sm">暂无任务记录</p>
  </div>
</template>