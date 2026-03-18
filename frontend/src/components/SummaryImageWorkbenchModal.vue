<script setup lang="ts">
import {
  PhArrowsClockwise,
  PhArrowsCounterClockwise,
  PhCaretDown,
  PhCaretLeft,
  PhCaretRight,
  PhCircleNotch,
  PhDotsThree,
  PhDownloadSimple,
  PhEye,
  PhMinus,
  PhPlus,
  PhSlidersHorizontal,
  PhX,
} from '@phosphor-icons/vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import FloatingToolbarDivider from './FloatingToolbarDivider.vue'
import FloatingToolbarShell from './FloatingToolbarShell.vue'
import type {
  SummaryImageExportSettings,
  SummaryImageFormat,
  SummaryImageLayoutPreset,
  SummaryImageMetaMode,
  SummaryImagePreviewPage,
} from '../composables/useSummaryImageExporter'

const settings = defineModel<SummaryImageExportSettings>('settings', { required: true })
const showAllPreviewPages = defineModel<boolean>('showAllPreviewPages', { required: true })

const props = defineProps<{
  show: boolean
  layoutOptions: Array<{ label: string; value: SummaryImageLayoutPreset }>
  metaModeOptions: Array<{ label: string; value: SummaryImageMetaMode }>
  formatOptions: Array<{ label: string; value: SummaryImageFormat }>
  widthOptions: number[]
  pixelRatioOptions: number[]
  isPreviewRendering: boolean
  renderProgress: { current: number; total: number } | null
  previewDirty: boolean
  canRefreshPreview: boolean
  refreshButtonLabel: string
  previewPages: SummaryImagePreviewPage[]
  previewActiveIndex: number
  previewTotalSizeKb: number
}>()

const emit = defineEmits<{
  close: []
  refreshPreview: []
  exportImage: []
  previewPrev: []
  previewNext: []
  selectPreviewPage: [index: number]
}>()

const PREVIEW_MIN_SCALE = 0.1
const PREVIEW_MAX_SCALE = 8
const PREVIEW_FIT_PADDING = 28

const isQualityDisabled = computed(() => settings.value.format === 'png')
const summaryPreviewActivePage = computed(() => props.previewPages[props.previewActiveIndex] || null)
const activePreviewZoom = computed(() => showAllPreviewPages.value ? gridScale.value : previewScale.value)
const isPreviewInteractionDisabled = computed(() => props.isPreviewRendering)
const canManipulatePreview = computed(() => props.previewPages.length > 0 && !props.isPreviewRendering)
const canTriggerToolbarRefresh = computed(() => props.canRefreshPreview && !props.isPreviewRendering)
const toolbarRefreshClass = computed(() => {
  const base = 'relative p-2 rounded-full transition-colors disabled:cursor-not-allowed'
  if (props.isPreviewRendering) {
    return `${base} bg-amber-100 text-amber-700 animate-pulse`
  }
  return `${base} bg-slate-100 text-slate-700 hover:bg-slate-200`
})
const toolbarExportClass = computed(() => {
  return 'p-2 rounded-full bg-primary/10 text-primary transition-colors hover:bg-primary/15 disabled:cursor-not-allowed disabled:opacity-45'
})
const mobileToolbarRefreshClass = computed(() => {
  const base = 'relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed'
  if (props.isPreviewRendering) {
    return `${base} bg-amber-100 text-amber-700 animate-pulse`
  }
  return `${base} bg-slate-100 text-slate-700 hover:bg-slate-200`
})
const mobileToolbarExportClass = computed(() => {
  return 'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary transition-colors hover:bg-primary/15 disabled:cursor-not-allowed disabled:opacity-45'
})
const previewInfoLabel = computed(() => {
  if (!props.previewPages.length) return '预览 0/0'
  if (showAllPreviewPages.value) {
    return `一并预览 ${props.previewPages.length} 张 · 总计约 ${props.previewTotalSizeKb}KB`
  }
  const page = summaryPreviewActivePage.value
  if (!page) return `预览 ${props.previewActiveIndex + 1}/${props.previewPages.length}`
  return `第 ${props.previewActiveIndex + 1}/${props.previewPages.length} 张 · ${page.width}x${page.height} · ${page.sizeKB}KB`
})
const mobilePreviewInfoLabel = computed(() => {
  if (!props.previewPages.length) return '0/0'
  if (showAllPreviewPages.value) {
    return `${props.previewPages.length} 张`
  }
  return `${props.previewActiveIndex + 1}/${props.previewPages.length}`
})

const previewStageRef = ref<HTMLElement | null>(null)
const previewViewportRef = ref<HTMLElement | null>(null)
const previewScale = ref(1)
const previewOffsetX = ref(0)
const previewOffsetY = ref(0)
const isPreviewDragging = ref(false)
const gridStageRef = ref<HTMLElement | null>(null)
const gridInnerRef = ref<HTMLElement | null>(null)
const gridScale = ref(1)
const isGridDragging = ref(false)
const isMobileSettingsOpen = ref(false)
const isMobileToolbarExpanded = ref(false)

let previewWidth = 1
let previewHeight = 1
let activePreviewPointerId: number | null = null
let lastPointerX = 0
let lastPointerY = 0
let activeGridPointerId: number | null = null
let gridDragStartX = 0
let gridDragStartY = 0
let gridDragStartScrollLeft = 0
let gridDragStartScrollTop = 0

const clampNumber = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

const applyPreviewTransform = () => {
  const viewport = previewViewportRef.value
  if (!viewport) return
  viewport.style.transform = `translate3d(${previewOffsetX.value}px, ${previewOffsetY.value}px, 0) scale(${previewScale.value})`
}

const ensurePreviewBaseSize = () => {
  const viewport = previewViewportRef.value
  const page = summaryPreviewActivePage.value
  if (!viewport || !page) return false

  previewWidth = Math.max(1, page.width)
  previewHeight = Math.max(1, page.height)
  viewport.style.width = `${previewWidth}px`
  viewport.style.height = `${previewHeight}px`
  return true
}

const centerPreviewAtScale = (targetScale: number) => {
  const stage = previewStageRef.value
  if (!stage || !ensurePreviewBaseSize()) return

  previewScale.value = clampNumber(targetScale, PREVIEW_MIN_SCALE, PREVIEW_MAX_SCALE)
  previewOffsetX.value = (stage.clientWidth - previewWidth * previewScale.value) / 2
  previewOffsetY.value = (stage.clientHeight - previewHeight * previewScale.value) / 2
  applyPreviewTransform()
}

const fitPreview = () => {
  const stage = previewStageRef.value
  if (!stage || showAllPreviewPages.value || !ensurePreviewBaseSize()) return

  const availableWidth = Math.max(1, stage.clientWidth - PREVIEW_FIT_PADDING * 2)
  const availableHeight = Math.max(1, stage.clientHeight - PREVIEW_FIT_PADDING * 2)
  const fitScale = Math.min(availableWidth / previewWidth, availableHeight / previewHeight)
  centerPreviewAtScale(fitScale)
}

const applyGridScale = () => {
  const inner = gridInnerRef.value
  if (!inner) return
  // Use layout scaling instead of transform scaling to keep text/button rendering crisp.
  inner.style.zoom = `${gridScale.value}`
}

const getGridBaseSize = () => {
  const inner = gridInnerRef.value
  if (!inner || props.previewPages.length === 0) return null
  const safeScale = Math.max(0.0001, gridScale.value)
  return {
    width: Math.max(1, inner.scrollWidth / safeScale),
    height: Math.max(1, inner.scrollHeight / safeScale),
  }
}

const setGridScale = (targetScale: number, anchorX?: number, anchorY?: number) => {
  const stage = gridStageRef.value
  if (!stage || !showAllPreviewPages.value) return

  const nextScale = clampNumber(targetScale, PREVIEW_MIN_SCALE, PREVIEW_MAX_SCALE)
  if (Math.abs(nextScale - gridScale.value) < 0.0001) return

  const rect = stage.getBoundingClientRect()
  const localX = anchorX ?? rect.width / 2
  const localY = anchorY ?? rect.height / 2
  const contentX = (stage.scrollLeft + localX) / gridScale.value
  const contentY = (stage.scrollTop + localY) / gridScale.value

  gridScale.value = nextScale
  applyGridScale()

  stage.scrollLeft = contentX * gridScale.value - localX
  stage.scrollTop = contentY * gridScale.value - localY
}

const fitGrid = () => {
  const stage = gridStageRef.value
  if (!stage || !showAllPreviewPages.value) return

  const baseSize = getGridBaseSize()
  if (!baseSize) return

  const availableWidth = Math.max(1, stage.clientWidth - PREVIEW_FIT_PADDING * 2)
  const availableHeight = Math.max(1, stage.clientHeight - PREVIEW_FIT_PADDING * 2)
  gridScale.value = clampNumber(
    Math.min(availableWidth / baseSize.width, availableHeight / baseSize.height),
    PREVIEW_MIN_SCALE,
    PREVIEW_MAX_SCALE,
  )
  applyGridScale()

  stage.scrollLeft = Math.max(0, (baseSize.width * gridScale.value - stage.clientWidth) / 2)
  stage.scrollTop = Math.max(0, (baseSize.height * gridScale.value - stage.clientHeight) / 2)
}

const resetPreview = () => {
  centerPreviewAtScale(1)
}

const resetGrid = () => {
  const stage = gridStageRef.value
  if (!stage || !showAllPreviewPages.value) return

  const baseSize = getGridBaseSize()
  if (!baseSize) return

  gridScale.value = 1
  applyGridScale()
  stage.scrollLeft = Math.max(0, (baseSize.width - stage.clientWidth) / 2)
  stage.scrollTop = Math.max(0, (baseSize.height - stage.clientHeight) / 2)
}

const zoomPreviewAt = (targetScale: number, stageX: number, stageY: number) => {
  if (!ensurePreviewBaseSize()) return

  const nextScale = clampNumber(targetScale, PREVIEW_MIN_SCALE, PREVIEW_MAX_SCALE)
  if (Math.abs(nextScale - previewScale.value) < 0.0001) return

  const contentX = (stageX - previewOffsetX.value) / previewScale.value
  const contentY = (stageY - previewOffsetY.value) / previewScale.value

  previewScale.value = nextScale
  previewOffsetX.value = stageX - contentX * previewScale.value
  previewOffsetY.value = stageY - contentY * previewScale.value
  applyPreviewTransform()
}

const zoomPreviewIn = () => {
  const stage = previewStageRef.value
  if (!stage) return
  zoomPreviewAt(previewScale.value * 1.2, stage.clientWidth / 2, stage.clientHeight / 2)
}

const zoomPreviewOut = () => {
  const stage = previewStageRef.value
  if (!stage) return
  zoomPreviewAt(previewScale.value / 1.2, stage.clientWidth / 2, stage.clientHeight / 2)
}

const zoomGridIn = () => {
  const stage = gridStageRef.value
  if (!stage) return
  setGridScale(gridScale.value * 1.2, stage.clientWidth / 2, stage.clientHeight / 2)
}

const zoomGridOut = () => {
  const stage = gridStageRef.value
  if (!stage) return
  setGridScale(gridScale.value / 1.2, stage.clientWidth / 2, stage.clientHeight / 2)
}

const stopPreviewDragging = () => {
  const stage = previewStageRef.value
  if (stage && activePreviewPointerId !== null && stage.hasPointerCapture(activePreviewPointerId)) {
    stage.releasePointerCapture(activePreviewPointerId)
  }
  isPreviewDragging.value = false
  activePreviewPointerId = null
}

const stopGridDragging = () => {
  const stage = gridStageRef.value
  if (stage && activeGridPointerId !== null && stage.hasPointerCapture(activeGridPointerId)) {
    stage.releasePointerCapture(activeGridPointerId)
  }
  isGridDragging.value = false
  activeGridPointerId = null
}

const handlePreviewPointerDown = (event: PointerEvent) => {
  if (isPreviewInteractionDisabled.value) return
  if (!summaryPreviewActivePage.value || showAllPreviewPages.value) return
  if (event.pointerType === 'mouse' && event.button !== 0) return

  const stage = previewStageRef.value
  if (!stage) return

  event.preventDefault()
  stage.setPointerCapture(event.pointerId)
  activePreviewPointerId = event.pointerId
  isPreviewDragging.value = true
  lastPointerX = event.clientX
  lastPointerY = event.clientY
}

const handlePreviewPointerMove = (event: PointerEvent) => {
  if (isPreviewInteractionDisabled.value) return
  if (!isPreviewDragging.value || activePreviewPointerId !== event.pointerId) return
  event.preventDefault()

  previewOffsetX.value += event.clientX - lastPointerX
  previewOffsetY.value += event.clientY - lastPointerY
  lastPointerX = event.clientX
  lastPointerY = event.clientY
  applyPreviewTransform()
}

const handlePreviewPointerUp = (event: PointerEvent) => {
  if (activePreviewPointerId !== event.pointerId) return
  stopPreviewDragging()
}

const handlePreviewWheel = (event: WheelEvent) => {
  if (isPreviewInteractionDisabled.value) return
  if (!summaryPreviewActivePage.value || showAllPreviewPages.value) return
  const stage = previewStageRef.value
  if (!stage) return

  event.preventDefault()
  const rect = stage.getBoundingClientRect()
  const stageX = event.clientX - rect.left
  const stageY = event.clientY - rect.top
  const nextScale = event.deltaY < 0 ? previewScale.value * 1.12 : previewScale.value / 1.12
  zoomPreviewAt(nextScale, stageX, stageY)
}

const handlePreviewResize = () => {
  if (!props.show) return
  if (showAllPreviewPages.value) {
    fitGrid()
    return
  }
  if (!summaryPreviewActivePage.value) return
  fitPreview()
}

const handleOpenSingleFromGrid = (index: number) => {
  showAllPreviewPages.value = false
  emit('selectPreviewPage', index)
}

const toggleShowAllPreview = () => {
  showAllPreviewPages.value = !showAllPreviewPages.value
}

const handleToolbarZoomIn = () => {
  if (isPreviewInteractionDisabled.value) return
  if (showAllPreviewPages.value) {
    zoomGridIn()
    return
  }
  zoomPreviewIn()
}

const handleToolbarZoomOut = () => {
  if (isPreviewInteractionDisabled.value) return
  if (showAllPreviewPages.value) {
    zoomGridOut()
    return
  }
  zoomPreviewOut()
}

const handleToolbarFit = () => {
  if (isPreviewInteractionDisabled.value) return
  if (showAllPreviewPages.value) {
    fitGrid()
    return
  }
  fitPreview()
}

const handleToolbarReset = () => {
  if (isPreviewInteractionDisabled.value) return
  if (showAllPreviewPages.value) {
    resetGrid()
    return
  }
  resetPreview()
}

const handleGridPointerDown = (event: PointerEvent) => {
  if (isPreviewInteractionDisabled.value) return
  if (!showAllPreviewPages.value) return
  if ((event.target as HTMLElement | null)?.closest('[data-open-single]')) return
  if (event.pointerType === 'mouse' && event.button !== 0) return

  const stage = gridStageRef.value
  if (!stage) return

  event.preventDefault()
  stage.setPointerCapture(event.pointerId)
  activeGridPointerId = event.pointerId
  isGridDragging.value = true
  gridDragStartX = event.clientX
  gridDragStartY = event.clientY
  gridDragStartScrollLeft = stage.scrollLeft
  gridDragStartScrollTop = stage.scrollTop
}

const handleGridPointerMove = (event: PointerEvent) => {
  if (isPreviewInteractionDisabled.value) return
  if (!isGridDragging.value || activeGridPointerId !== event.pointerId) return
  event.preventDefault()

  const stage = gridStageRef.value
  if (!stage) return

  const deltaX = event.clientX - gridDragStartX
  const deltaY = event.clientY - gridDragStartY
  stage.scrollLeft = gridDragStartScrollLeft - deltaX
  stage.scrollTop = gridDragStartScrollTop - deltaY
}

const handleGridPointerUp = (event: PointerEvent) => {
  if (activeGridPointerId !== event.pointerId) return
  stopGridDragging()
}

const handleGridWheel = (event: WheelEvent) => {
  if (isPreviewInteractionDisabled.value) return
  if (!showAllPreviewPages.value) return
  const stage = gridStageRef.value
  if (!stage) return

  event.preventDefault()
  const rect = stage.getBoundingClientRect()
  const stageX = event.clientX - rect.left
  const stageY = event.clientY - rect.top
  const nextScale = event.deltaY < 0 ? gridScale.value * 1.12 : gridScale.value / 1.12
  setGridScale(nextScale, stageX, stageY)
}

watch(
  () => [props.show, showAllPreviewPages.value, summaryPreviewActivePage.value?.index ?? -1],
  async ([show, showAll]) => {
    if (!show || showAll || !summaryPreviewActivePage.value) {
      stopPreviewDragging()
      return
    }
    await nextTick()
    fitPreview()
  },
  { immediate: true },
)

watch(
  () => ({
    show: props.show,
    showAll: showAllPreviewPages.value,
    pageCount: props.previewPages.length,
  }),
  async ({ show, showAll, pageCount }) => {
    if (!show || !showAll || pageCount <= 0) {
      stopGridDragging()
      return
    }
    await nextTick()
    fitGrid()
  },
  { immediate: true },
)

watch(
  () => props.isPreviewRendering,
  (isRendering) => {
    if (!isRendering) return
    stopPreviewDragging()
    stopGridDragging()
  },
)

watch(
  () => props.show,
  (show) => {
    if (!show) {
      isMobileSettingsOpen.value = false
      isMobileToolbarExpanded.value = false
    }
  },
)

watch(
  () => isMobileSettingsOpen.value,
  (open) => {
    if (open) {
      isMobileToolbarExpanded.value = false
    }
  },
)

if (typeof window !== 'undefined') {
  window.addEventListener('resize', handlePreviewResize)
}

onBeforeUnmount(() => {
  stopPreviewDragging()
  stopGridDragging()
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', handlePreviewResize)
  }
})
</script>

<template>
  <transition name="fade">
    <section
      v-if="show"
      class="fixed inset-0 z-[70] bg-slate-100/95 backdrop-blur-sm"
    >
      <div class="relative flex h-full w-full">
        <transition name="fade">
          <div
            v-if="isMobileSettingsOpen"
            class="absolute inset-0 z-30 bg-slate-900/30 md:hidden"
            @click="isMobileSettingsOpen = false"
          ></div>
        </transition>

        <aside
          :class="[
            'fixed inset-x-0 bottom-0 z-40 flex max-h-[78dvh] w-full flex-col rounded-t-3xl border-t border-slate-200 bg-white px-4 py-3 shadow-[0_-12px_40px_rgba(15,23,42,0.14)] transition-transform duration-300',
            'md:static md:z-auto md:h-full md:max-h-none md:w-[320px] md:shrink-0 md:rounded-none md:border-t-0 md:border-r md:bg-white/95 md:px-5 md:py-5 md:shadow-[0_10px_30px_rgba(15,23,42,0.06)]',
            isMobileSettingsOpen ? 'translate-y-0' : 'translate-y-full md:translate-y-0',
          ]"
        >
          <div class="mb-1 flex justify-center md:hidden">
            <div class="h-1.5 w-12 rounded-full bg-slate-200"></div>
          </div>
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="text-base font-semibold text-slate-800">成图工作台</h3>
            </div>
            <div class="flex items-center">
              <button
                class="rounded-full p-2 text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 md:hidden"
                title="收起参数面板"
                aria-label="收起参数面板"
                @click="isMobileSettingsOpen = false"
              >
                <PhCaretDown :size="16" weight="bold" />
              </button>
            </div>
          </div>

          <div class="mt-4 flex-1 space-y-3 overflow-y-auto pr-1">
            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">输出宽度</span>
              <select
                v-model.number="settings.width"
                class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option v-for="width in widthOptions" :key="width" :value="width">{{ width }} px</option>
              </select>
            </label>

            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">页面比例</span>
              <select
                v-model="settings.layoutPreset"
                class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option v-for="option in layoutOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>

            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">元信息设置</span>
              <select
                v-model="settings.metaMode"
                class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option v-for="option in metaModeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>

            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">编码格式</span>
              <select
                v-model="settings.format"
                class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option v-for="option in formatOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>

            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">渲染精度</span>
              <select
                v-model.number="settings.pixelRatio"
                class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option v-for="ratio in pixelRatioOptions" :key="ratio" :value="ratio">{{ ratio }}x</option>
              </select>
            </label>

            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">
                压缩质量 <span class="text-slate-400">{{ settings.quality }}</span>
              </span>
              <input
                v-model.number="settings.quality"
                type="range"
                min="50"
                max="100"
                step="1"
                :disabled="isQualityDisabled"
                class="w-full accent-primary disabled:cursor-not-allowed disabled:opacity-50"
              >
            </label>

            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">
                字体缩放 <span class="text-slate-400">{{ settings.fontScale.toFixed(2) }}x</span>
              </span>
              <input
                v-model.number="settings.fontScale"
                type="range"
                min="0.8"
                max="1.3"
                step="0.05"
                class="w-full accent-primary"
              >
            </label>

            <label class="block space-y-1.5">
              <span class="text-xs font-medium text-slate-600">
                间距缩放 <span class="text-slate-400">{{ settings.contentPaddingScale.toFixed(2) }}x</span>
              </span>
              <input
                v-model.number="settings.contentPaddingScale"
                type="range"
                min="0.8"
                max="1.35"
                step="0.05"
                class="w-full accent-primary"
              >
            </label>
          </div>
        </aside>

        <section class="relative min-w-0 flex-1 bg-slate-50/60 pb-20 md:pb-0">
          <div class="absolute top-4 left-1/2 z-30 -translate-x-1/2">
            <div class="hidden md:block">
              <FloatingToolbarShell>
                <button
                  :class="toolbarRefreshClass"
                  :disabled="!canTriggerToolbarRefresh"
                  :title="refreshButtonLabel"
                  @click="emit('refreshPreview')"
                >
                  <span
                    v-if="canTriggerToolbarRefresh"
                    class="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-rose-500 ring-2 ring-white"
                  ></span>
                  <PhEye :size="16" />
                </button>
                <button
                  :class="toolbarExportClass"
                  :disabled="isPreviewRendering"
                  title="导出全部"
                  @click="emit('exportImage')"
                >
                  <PhDownloadSimple :size="16" />
                </button>

                <FloatingToolbarDivider />

                <div class="max-w-[380px] truncate rounded-full bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary">
                  {{ previewInfoLabel }}
                </div>

                <button
                  class="p-2 rounded-full bg-slate-100 text-slate-600 hover:text-slate-900 hover:bg-slate-200 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="上一页"
                  :disabled="previewActiveIndex <= 0 || showAllPreviewPages || previewPages.length === 0"
                  @click="emit('previewPrev')"
                >
                  <PhCaretLeft :size="16" weight="bold" />
                </button>
                <button
                  class="p-2 rounded-full bg-slate-100 text-slate-600 hover:text-slate-900 hover:bg-slate-200 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="下一页"
                  :disabled="previewActiveIndex >= previewPages.length - 1 || showAllPreviewPages || previewPages.length === 0"
                  @click="emit('previewNext')"
                >
                  <PhCaretRight :size="16" weight="bold" />
                </button>
                <button
                  class="whitespace-nowrap rounded-full bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-200 hover:text-slate-900"
                  @click="toggleShowAllPreview"
                >
                  {{ showAllPreviewPages ? '单页预览' : '一并预览' }}
                </button>

                <FloatingToolbarDivider />

                <div class="rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700">
                  {{ Math.round(activePreviewZoom * 100) }}%
                </div>
                <button
                  class="p-2 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="缩小"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarZoomOut"
                >
                  <PhMinus :size="16" />
                </button>
                <button
                  class="p-2 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="放大"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarZoomIn"
                >
                  <PhPlus :size="16" />
                </button>
                <button
                  class="p-2 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="适应区域"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarFit"
                >
                  <PhArrowsClockwise :size="16" />
                </button>
                <button
                  class="p-2 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="100% 视图"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarReset"
                >
                  <PhArrowsCounterClockwise :size="16" />
                </button>

                <FloatingToolbarDivider />

                <button
                  class="p-2 text-slate-600 hover:text-rose-600 hover:bg-rose-50 rounded-full transition-colors"
                  title="关闭"
                  @click="emit('close')"
                >
                  <PhX :size="16" />
                </button>
              </FloatingToolbarShell>
            </div>

            <div class="flex flex-col items-center gap-1.5 md:hidden">
              <div class="flex items-center gap-2">
                <FloatingToolbarShell variant="solid" class="gap-0.5">
                  <button
                    :class="mobileToolbarRefreshClass"
                    :disabled="!canTriggerToolbarRefresh"
                    :title="refreshButtonLabel"
                    @click="emit('refreshPreview')"
                  >
                    <span
                      v-if="canTriggerToolbarRefresh"
                      class="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-rose-500 ring-2 ring-white"
                    ></span>
                    <PhEye :size="14" />
                  </button>
                  <button
                    :class="mobileToolbarExportClass"
                    :disabled="isPreviewRendering"
                    title="导出全部"
                    @click="emit('exportImage')"
                  >
                    <PhDownloadSimple :size="14" />
                  </button>

                  <FloatingToolbarDivider />

                  <button
                    class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600 transition-colors hover:bg-slate-200 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                    title="上一页"
                    :disabled="previewActiveIndex <= 0 || showAllPreviewPages || previewPages.length === 0"
                    @click="emit('previewPrev')"
                  >
                    <PhCaretLeft :size="14" weight="bold" />
                  </button>
                  <div class="max-w-[88px] truncate rounded-full bg-primary/10 px-2 py-1 text-[10px] font-semibold text-primary">
                    {{ mobilePreviewInfoLabel }}
                  </div>
                  <button
                    class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600 transition-colors hover:bg-slate-200 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                    title="下一页"
                    :disabled="previewActiveIndex >= previewPages.length - 1 || showAllPreviewPages || previewPages.length === 0"
                    @click="emit('previewNext')"
                  >
                    <PhCaretRight :size="14" weight="bold" />
                  </button>

                  <FloatingToolbarDivider />

                  <button
                    class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600 transition-colors hover:bg-slate-200 hover:text-slate-900"
                    :title="isMobileToolbarExpanded ? '收起更多操作' : '更多操作'"
                    @click="isMobileToolbarExpanded = !isMobileToolbarExpanded"
                  >
                    <PhDotsThree :size="14" weight="bold" />
                  </button>
                  <button
                    class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600 transition-colors hover:bg-slate-200 hover:text-slate-900"
                    title="打开参数面板"
                    @click="isMobileSettingsOpen = true"
                  >
                    <PhSlidersHorizontal :size="14" />
                  </button>
                </FloatingToolbarShell>

                <button
                  class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white text-slate-600 shadow-md ring-1 ring-slate-200 transition-colors hover:bg-rose-50 hover:text-rose-600"
                  title="关闭"
                  @click="emit('close')"
                >
                  <PhX :size="14" />
                </button>
              </div>

              <FloatingToolbarShell v-if="isMobileToolbarExpanded" variant="solid" class="gap-0.5">
                <button
                  class="whitespace-nowrap rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-700 transition hover:bg-slate-200 hover:text-slate-900"
                  @click="toggleShowAllPreview"
                >
                  {{ showAllPreviewPages ? '单页预览' : '一并预览' }}
                </button>
                <FloatingToolbarDivider />
                <div class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-700">
                  {{ Math.round(activePreviewZoom * 100) }}%
                </div>
                <button
                  class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  title="缩小"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarZoomOut"
                >
                  <PhMinus :size="14" />
                </button>
                <button
                  class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  title="放大"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarZoomIn"
                >
                  <PhPlus :size="14" />
                </button>
                <button
                  class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  title="适应区域"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarFit"
                >
                  <PhArrowsClockwise :size="14" />
                </button>
                <button
                  class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                  title="100% 视图"
                  :disabled="!canManipulatePreview"
                  @click="handleToolbarReset"
                >
                  <PhArrowsCounterClockwise :size="14" />
                </button>
              </FloatingToolbarShell>
            </div>

          </div>

          <div v-if="!previewPages.length" class="absolute inset-0 flex items-center justify-center text-sm text-slate-500">
            <span class="hidden md:inline">点击左侧“刷新预览”生成分页结果</span>
            <span class="md:hidden">点击上方眼睛按钮生成预览</span>
          </div>

          <div v-else-if="showAllPreviewPages" class="absolute inset-0">
            <div
              ref="gridStageRef"
              class="summary-preview-stage summary-grid-stage absolute inset-0 overflow-auto bg-transparent touch-none"
              :class="isPreviewInteractionDisabled ? 'pointer-events-none cursor-wait' : (isGridDragging ? 'cursor-grabbing' : 'cursor-grab')"
              @dblclick="fitGrid"
              @pointerdown="handleGridPointerDown"
              @pointermove="handleGridPointerMove"
              @pointerup="handleGridPointerUp"
              @pointercancel="handleGridPointerUp"
              @wheel="handleGridWheel"
            >
              <div ref="gridInnerRef" class="summary-grid-inner grid grid-cols-1 gap-6 p-12 sm:grid-cols-2 xl:grid-cols-3">
                <article
                  v-for="page in previewPages"
                  :key="page.index"
                  class="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
                >
                  <img :src="page.dataUrl" :alt="`预览第${page.index + 1}张`" class="h-auto w-full object-contain bg-slate-50" />
                  <div class="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-left text-sm text-slate-600">
                    <span>第 {{ page.index + 1 }} 张 · {{ page.width }}x{{ page.height }} · {{ page.sizeKB }}KB</span>
                    <button
                      data-open-single
                      class="shrink-0 rounded-md border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900"
                      @click.stop="handleOpenSingleFromGrid(page.index)"
                    >
                      单页查看
                    </button>
                  </div>
                </article>
              </div>
            </div>
          </div>

          <div v-else class="absolute inset-0">
            <div
              ref="previewStageRef"
              class="summary-preview-stage absolute inset-0 overflow-hidden bg-transparent touch-none"
              :class="isPreviewInteractionDisabled ? 'pointer-events-none cursor-wait' : (isPreviewDragging ? 'cursor-grabbing' : 'cursor-grab')"
              @dblclick="fitPreview"
              @pointerdown="handlePreviewPointerDown"
              @pointermove="handlePreviewPointerMove"
              @pointerup="handlePreviewPointerUp"
              @pointercancel="handlePreviewPointerUp"
              @wheel="handlePreviewWheel"
            >
              <div ref="previewViewportRef" class="summary-preview-viewport">
                <img
                  :src="summaryPreviewActivePage?.dataUrl"
                  :alt="`预览第${previewActiveIndex + 1}张`"
                  draggable="false"
                  class="block h-full w-full select-none pointer-events-none"
                >
              </div>
            </div>
          </div>

          <transition name="preview-loading">
            <div
              v-if="isPreviewRendering"
              class="absolute inset-0 z-20 flex items-center justify-center bg-white/45 backdrop-blur-[1px]"
            >
              <div class="flex items-center gap-3 rounded-2xl bg-white/95 backdrop-blur-sm px-6 py-4 text-base font-semibold text-slate-800 shadow-xl">
                <PhCircleNotch :size="24" class="animate-spin text-primary" />
                <span v-if="renderProgress">
                  正在生成第 {{ renderProgress.current }}/{{ renderProgress.total }} 张...
                </span>
                <span v-else>成图预览生成中……</span>
              </div>
            </div>
          </transition>

          <div v-if="previewPages.length" class="pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2 rounded-lg border border-slate-200 bg-white/95 px-3 py-1.5 text-xs text-slate-500 shadow-sm">
            {{ showAllPreviewPages ? '网格滚轮缩放 · 拖拽平移 · 双击适应' : '滚轮缩放 · 拖拽平移 · 双击适应' }}
          </div>
        </section>
      </div>
    </section>
  </transition>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.preview-loading-enter-active,
.preview-loading-leave-active {
  transition: opacity 0.25s ease;
}

.preview-loading-enter-from,
.preview-loading-leave-to {
  opacity: 0;
}

.summary-preview-stage {
  touch-action: none;
}

.summary-preview-viewport {
  position: absolute;
  left: 0;
  top: 0;
  transform-origin: 0 0;
  will-change: transform;
}

.summary-grid-stage {
  overscroll-behavior: contain;
}

.summary-grid-inner {
  width: max-content;
  transform-origin: top left;
}
</style>
