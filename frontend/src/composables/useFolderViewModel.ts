import { ref, computed, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import type { Folder, FolderNode, Task } from '../types'

const normalizeBase = (base?: string) => (base || '').trim().replace(/\/+$/, '')
const apiBaseUrl = normalizeBase(import.meta.env.VITE_API_BASE_URL)
const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
const wsBaseUrl = normalizeBase(import.meta.env.VITE_WS_BASE_URL) || `${wsProtocol}://${window.location.host}/ws`

export function useFolderViewModel() {
  const folders = ref<Folder[]>([])
  const isCreatingFolder = ref(false)
  const isRenamingFolder = ref(false)
  let ws: WebSocket | null = null

  // ── Computed ──

  const folderTree = computed<FolderNode[]>(() => {
    const map = new Map<string, FolderNode>()
    const roots: FolderNode[] = []

    for (const f of folders.value) {
      map.set(f.id, { ...f, children: [] })
    }

    for (const f of folders.value) {
      const node = map.get(f.id)!
      if (f.parent_id && map.has(f.parent_id)) {
        map.get(f.parent_id)!.children.push(node)
      } else {
        roots.push(node)
      }
    }

    return roots
  })

  const tasksByFolder = computed(() => {
    // This requires tasks to be passed in; computed as a function factory
    return (tasks: Task[]) => {
      const result = new Map<string, Task[]>()
      for (const folder of folders.value) {
        result.set(folder.id, [])
      }
      for (const task of tasks) {
        const fid = task.folder_id
        if (fid && result.has(fid)) {
          result.get(fid)!.push(task)
        }
      }
      return result
    }
  })

  const rootTasks = computed(() => {
    return (tasks: Task[]) => tasks.filter(t => !t.folder_id)
  })

  // ── Actions ──

  const fetchFolders = async () => {
    try {
      const response = await axios.get(`${apiBaseUrl}/folders/?include_tasks=true`)
      folders.value = response.data
    } catch (err) {
      console.error('Failed to fetch folders:', err)
    }
  }

  const createFolder = async (name: string, parentId: string | null = null) => {
    isCreatingFolder.value = true
    try {
      const response = await axios.post(`${apiBaseUrl}/folders/`, {
        name,
        parent_id: parentId,
      })
      // Don't push locally — WS will broadcast folder_created and onmessage handles it
      return response.data as Folder
    } catch (err) {
      console.error('Failed to create folder:', err)
      throw err
    } finally {
      isCreatingFolder.value = false
    }
  }

  const renameFolder = async (folderId: string, newName: string) => {
    isRenamingFolder.value = true
    try {
      await axios.patch(`${apiBaseUrl}/folders/${folderId}`, {
        name: newName,
      })
      // WS will broadcast folder_updated — onmessage handles the local update
    } catch (err) {
      console.error('Failed to rename folder:', err)
      throw err
    } finally {
      isRenamingFolder.value = false
    }
  }

  const deleteFolder = async (folderId: string) => {
    try {
      await axios.delete(`${apiBaseUrl}/folders/${folderId}`)
      // WS will broadcast folder_deleted — onmessage handles the local update
    } catch (err) {
      console.error('Failed to delete folder:', err)
      throw err
    }
  }

  const moveFolder = async (folderId: string, newParentId: string | null) => {
    try {
      await axios.patch(`${apiBaseUrl}/folders/${folderId}`, {
        parent_id: newParentId,
      })
      // WS will broadcast folder_updated — onmessage handles the local update
    } catch (err) {
      console.error('Failed to move folder:', err)
      throw err
    }
  }

  const assignTaskToFolder = async (taskId: string, folderId: string | null) => {
    try {
      await axios.patch(`${apiBaseUrl}/tasks/${taskId}/folder`, {
        folder_id: folderId,
      })
      // WS will broadcast task_update with the new folder_id
    } catch (err) {
      console.error('Failed to assign task to folder:', err)
      throw err
    }
  }

  // ── WebSocket ──

  const connectWebSocket = () => {
    ws = new WebSocket(wsBaseUrl)

    ws.onopen = () => {
      console.log('Folder WebSocket connected')
      fetchFolders()
    }

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'folder_created') {
        const folder = data.folder as Folder
        if (!folders.value.find(f => f.id === folder.id)) {
          folders.value.push(folder)
        }
      } else if (data.type === 'folder_updated') {
        const folder = data.folder as Folder
        const idx = folders.value.findIndex(f => f.id === folder.id)
        if (idx !== -1) {
          folders.value[idx] = folder
        }
      } else if (data.type === 'folder_deleted') {
        const folderId = data.folder_id as string
        folders.value = folders.value.filter(f => f.id !== folderId)
      }
    }

    ws.onclose = () => {
      console.log('Folder WebSocket disconnected, retrying in 3s...')
      setTimeout(connectWebSocket, 3000)
    }

    ws.onerror = (err) => {
      console.error('Folder WebSocket error:', err)
      ws?.close()
    }
  }

  // ── Lifecycle ──

  onMounted(() => {
    fetchFolders()
    connectWebSocket()
  })

  onUnmounted(() => {
    if (ws) {
      ws.close()
    }
  })

  return {
    folders,
    folderTree,
    tasksByFolder,
    rootTasks,
    isCreatingFolder,
    isRenamingFolder,
    fetchFolders,
    createFolder,
    renameFolder,
    deleteFolder,
    moveFolder,
    assignTaskToFolder,
  }
}