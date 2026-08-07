<script setup lang="ts">
import { ref } from 'vue'
import { PhPlus, PhFolderPlus } from '@phosphor-icons/vue'

const props = defineProps<{
  parentId: string | null
}>()

const emit = defineEmits<{
  create: [name: string, parentId: string | null]
  cancel: []
}>()

const folderName = ref('')

const submit = () => {
  if (folderName.value.trim()) {
    emit('create', folderName.value.trim(), props.parentId)
    folderName.value = ''
  }
}
</script>

<template>
  <div class="flex items-center gap-2 py-1.5 px-2">
    <PhFolderPlus :size="16" class="text-blue-500" />
    <input
      v-model="folderName"
      type="text"
      placeholder="新建文件夹名称..."
      class="flex-1 px-2 py-1 text-sm border border-blue-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-blue-200"
      @keyup.enter="submit"
      @keyup.escape="emit('cancel')"
      autofocus
    />
    <button @click="submit" class="px-2 py-1 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors">
      <PhPlus :size="14" />
    </button>
  </div>
</template>