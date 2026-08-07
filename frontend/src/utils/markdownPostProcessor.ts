import { replaceTimestampMarksWithChips } from './markdownTimestampChips'

export interface MarkdownPostProcessOptions {
  videoUrl?: string
  enhancePresentation?: boolean
}

type SummaryCalloutKind = 'info' | 'dialogue' | 'key' | 'fact' | 'warning' | 'example' | 'action' | 'quote'

interface SummaryCalloutDescriptor {
  kind: SummaryCalloutKind
  label: string
}

const SUMMARY_CALLOUT_RE = /^\s*\[!([A-Z][A-Z0-9_-]{0,31})\]\s*/i
const SUMMARY_CALLOUT_LABELS: Record<SummaryCalloutKind, string> = {
  info: '信息说明',
  dialogue: '对话还原',
  key: '关键结论',
  fact: '事实与数据',
  warning: '边界与风险',
  example: '案例细节',
  action: '行动项',
  quote: '原话摘录',
}
const SUMMARY_CALLOUT_ALIASES: Record<string, SummaryCalloutDescriptor> = {
  NOTE: { kind: 'info', label: '信息说明' },
  INFO: { kind: 'info', label: '信息说明' },
  CONTEXT: { kind: 'info', label: '背景与语境' },
  BACKGROUND: { kind: 'info', label: '背景与语境' },
  DEFINITION: { kind: 'info', label: '概念定义' },
  CONCEPT: { kind: 'info', label: '概念说明' },
  PROCESS: { kind: 'info', label: '过程说明' },
  STEPS: { kind: 'action', label: '步骤流程' },
  TIMELINE: { kind: 'info', label: '时间脉络' },
  COMPARISON: { kind: 'info', label: '比较关系' },
  DIALOGUE: { kind: 'dialogue', label: '对话还原' },
  QUOTE: { kind: 'quote', label: '原话摘录' },
  KEY: { kind: 'key', label: '关键结论' },
  INSIGHT: { kind: 'key', label: '核心洞察' },
  IMPORTANT: { kind: 'key', label: '重要信息' },
  SUMMARY: { kind: 'key', label: '阶段小结' },
  FACT: { kind: 'fact', label: '事实信息' },
  DATA: { kind: 'fact', label: '数据指标' },
  METRIC: { kind: 'fact', label: '数据指标' },
  EVIDENCE: { kind: 'fact', label: '依据与证据' },
  SOURCE: { kind: 'fact', label: '来源依据' },
  WARNING: { kind: 'warning', label: '边界与风险' },
  CAUTION: { kind: 'warning', label: '注意事项' },
  RISK: { kind: 'warning', label: '风险提示' },
  EXAMPLE: { kind: 'example', label: '案例细节' },
  CASE: { kind: 'example', label: '案例细节' },
  ACTION: { kind: 'action', label: '行动项' },
  DECISION: { kind: 'action', label: '决定与结论' },
  TODO: { kind: 'action', label: '待办事项' },
  TIP: { kind: 'action', label: '实用建议' },
}
const SUMMARY_DIALOGUE_PREFIX_RE = /^(?:\*\*)?(主持人|嘉宾|发言者|提问者|回答者|观众|嘉宾[一二三四五六七八九十]|[A-Z][A-Za-z0-9_-]{0,15})(?:\*\*)?\s*[：:]/i
const SUMMARY_DIALOGUE_VERB_RE = /^([^：:，,。]{1,18})(?:率先发言|发言|追问|提问|回答|回应|补充|指出|表示|认为|强调|反驳|解释|坦言|介绍)[：:，,]/
const SUMMARY_TIMESTAMP_RE = /(?:\d{1,2}:)?\d{1,2}:\d{2}/

const normalizeSummaryHeading = (value: string) => {
  return value.replace(/\s+/g, ' ').trim().toLowerCase()
}

const previousElementSibling = (element: Element): HTMLElement | null => {
  let sibling = element.previousElementSibling
  while (sibling) {
    if (sibling instanceof HTMLElement) return sibling
    sibling = sibling.previousElementSibling
  }
  return null
}

const findFirstTextNode = (root: Node): Text | null => {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  return walker.nextNode() as Text | null
}

const removeCalloutMarker = (blockquote: HTMLElement): boolean => {
  const textNode = findFirstTextNode(blockquote)
  if (!textNode) return false
  const match = textNode.data.match(SUMMARY_CALLOUT_RE)
  if (!match || match.index !== 0) return false
  textNode.data = textNode.data.slice(match[0].length)
  return true
}

const resolveCalloutDescriptor = (rawType: string): SummaryCalloutDescriptor => {
  const token = rawType.trim().toUpperCase()
  return SUMMARY_CALLOUT_ALIASES[token] || {
    kind: 'info',
    label: token.replace(/[_-]+/g, ' ').slice(0, 24) || SUMMARY_CALLOUT_LABELS.info,
  }
}

const addCalloutLabel = (blockquote: HTMLElement, descriptor: SummaryCalloutDescriptor) => {
  if (blockquote.querySelector(':scope > .ss-report-callout-label')) return
  const label = document.createElement('div')
  label.className = 'ss-report-callout-label'
  label.dataset.kind = descriptor.kind
  label.textContent = descriptor.label
  blockquote.insertBefore(label, blockquote.firstChild)
}

const decorateCallouts = (root: ParentNode) => {
  const blockquotes = Array.from(root.querySelectorAll('blockquote')) as HTMLElement[]
  let dialogueIndex = 0

  blockquotes.forEach((blockquote) => {
    const text = (blockquote.textContent || '').trim()
    const marker = text.match(SUMMARY_CALLOUT_RE)
    let descriptor: SummaryCalloutDescriptor | null = marker?.[1]
      ? resolveCalloutDescriptor(marker[1])
      : null

    if (marker) removeCalloutMarker(blockquote)

    if (!descriptor && SUMMARY_DIALOGUE_PREFIX_RE.test(text)) {
      descriptor = { kind: 'dialogue', label: SUMMARY_CALLOUT_LABELS.dialogue }
    }
    if (!descriptor && /^(?:重点|关键结论|结论|事实|数据|指标|证据|风险|注意|待办|行动项|小结|定义|背景)\s*[：:]/.test(text)) {
      descriptor = /风险|注意/.test(text)
        ? { kind: 'warning', label: SUMMARY_CALLOUT_LABELS.warning }
        : /事实|数据|指标|证据/.test(text)
          ? { kind: 'fact', label: SUMMARY_CALLOUT_LABELS.fact }
          : { kind: 'key', label: SUMMARY_CALLOUT_LABELS.key }
    }
    if (!descriptor) return

    blockquote.classList.add('ss-report-callout', `ss-report-callout--${descriptor.kind}`)
    blockquote.dataset.calloutKind = descriptor.kind
    addCalloutLabel(blockquote, descriptor)
    if (descriptor.kind === 'dialogue') {
      blockquote.dataset.dialogueIndex = String(dialogueIndex % 2)
      dialogueIndex += 1
    }
  })

  // Also support a comment marker for models that prefer invisible metadata.
  const comments: Comment[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT)
  let node = walker.nextNode()
  while (node) {
    comments.push(node as Comment)
    node = walker.nextNode()
  }
  comments.forEach((comment) => {
    const match = comment.data.match(/\bsw:([a-z][a-z0-9_-]{0,31})\b/i)
    if (!match?.[1]) return
    let target = comment.nextSibling
    while (target && !(target instanceof HTMLElement)) target = target.nextSibling
    if (target instanceof HTMLElement) {
      const descriptor = resolveCalloutDescriptor(match[1])
      target.classList.add('ss-report-callout', `ss-report-callout--${descriptor.kind}`)
      target.dataset.calloutKind = descriptor.kind
      addCalloutLabel(target, descriptor)
    }
    comment.remove()
  })
}

const decorateDialogueParagraphs = (root: ParentNode) => {
  const paragraphs = Array.from(root.querySelectorAll('p')) as HTMLElement[]
  let dialogueIndex = 0
  paragraphs.forEach((paragraph) => {
    if (paragraph.closest('blockquote, li, .ss-report-callout')) return
    const text = (paragraph.textContent || '').trim()
    if (!text) return
    const explicit = text.match(SUMMARY_DIALOGUE_PREFIX_RE)
    const narrative = text.match(SUMMARY_DIALOGUE_VERB_RE)
    if (!explicit && !narrative) return

    paragraph.classList.add('ss-dialogue-card')
    paragraph.dataset.dialogueIndex = String(dialogueIndex % 2)
    if (explicit?.[1]) paragraph.dataset.speaker = explicit[1]
    else if (narrative?.[1]) paragraph.dataset.speaker = narrative[1].trim()
    dialogueIndex += 1
  })
}

const classifyHeading = (text: string): string | null => {
  const heading = normalizeSummaryHeading(text)
  if (/时间线|时间轴|历程|事件经过|发展阶段|过程回顾|大事记/.test(heading)) return 'timeline'
  if (/步骤|流程|操作|教程|方法|训练|实践|执行/.test(heading)) return 'steps'
  if (/对比|比较|差异|评测|优缺点|矩阵/.test(heading)) return 'comparison'
  if (/数据|指标|统计|规模|参数|数字/.test(heading)) return 'data'
  if (/重点|核心|结论|总结|要点|行动项|建议/.test(heading)) return 'key'
  return null
}

const decorateSections = (root: ParentNode) => {
  const topLevelHeadings = Array.from(root.querySelectorAll(':scope > h2')) as HTMLElement[]
  topLevelHeadings.forEach((heading) => {
    if (heading.parentElement?.classList.contains('ss-report-section')) return
    const parent = heading.parentNode
    if (!parent) return

    const sectionNodes: Node[] = [heading]
    let sibling = heading.nextSibling
    while (sibling) {
      const next = sibling.nextSibling
      if (sibling instanceof HTMLElement && sibling.tagName === 'H2') break
      sectionNodes.push(sibling)
      sibling = next
    }

    const section = document.createElement('section')
    section.className = 'ss-report-section'
    parent.insertBefore(section, heading)
    sectionNodes.forEach((node) => section.appendChild(node))
  })
}

const decorateHeadingsAndLists = (root: ParentNode) => {
  const headings = Array.from(root.querySelectorAll('h1, h2, h3, h4')) as HTMLElement[]
  headings.forEach((heading) => {
    heading.classList.add('ss-report-heading')
    const kind = classifyHeading(heading.textContent || '')
    if (kind) heading.classList.add(`ss-report-heading--${kind}`)
  })

  const lists = Array.from(root.querySelectorAll('ol, ul')) as HTMLElement[]
  lists.forEach((list) => {
    const items = Array.from(list.children)
      .filter((child) => child.tagName === 'LI')
      .map((child) => (child.textContent || '').trim())
    if (!items.length) return

    const preceding = previousElementSibling(list)
    const precedingKind = preceding?.className.match(/ss-report-heading--(timeline|steps|comparison|data|key)/)?.[1]
    const hasStepPrefix = items.some((item) => /^(?:步骤|第\s*\d+\s*[步章节]|\d+[、.)])/.test(item))
    const hasTimestamp = items.filter((item) => SUMMARY_TIMESTAMP_RE.test(item)).length >= 2
    const hasDate = items.filter((item) => /\d{4}\s*[年/-]\s*\d{1,2}|\d{1,2}\s*月\s*\d{1,2}/.test(item)).length >= 2

    if (list.tagName === 'OL' && (precedingKind === 'steps' || hasStepPrefix)) {
      list.classList.add('ss-report-steps')
    } else if (list.tagName === 'UL' && (precedingKind === 'timeline' || hasTimestamp || hasDate)) {
      list.classList.add('ss-report-timeline')
    } else if (precedingKind === 'key' || precedingKind === 'data') {
      list.classList.add('ss-report-key-list')
    }
  })
}

const decorateFactLines = (root: ParentNode) => {
  const paragraphs = Array.from(root.querySelectorAll('p')) as HTMLElement[]
  paragraphs.forEach((paragraph) => {
    if (paragraph.closest('blockquote, .ss-dialogue-card, .ss-report-callout')) return
    const text = (paragraph.textContent || '').trim()
    const match = text.match(/^\[?(重点|核心洞察|关键结论|结论|小结|定义|背景|事实|数据|指标|证据|风险|注意|提示|待办|行动项|决定|案例)\]?\s*[：:）)]?/)
    const marker = match?.[1]
    if (!marker) return
    const kind: SummaryCalloutKind = /风险|注意|提示/.test(marker)
      ? 'warning'
      : /数据|事实|指标|证据/.test(marker)
        ? 'fact'
        : /待办|行动|决定/.test(marker)
          ? 'action'
          : /案例/.test(marker)
            ? 'example'
            : /定义|背景/.test(marker)
              ? 'info'
              : 'key'
    paragraph.classList.add('ss-report-fact-line', `ss-report-fact-line--${kind}`)
    paragraph.dataset.factKind = kind
  })
}

const decorateTables = (root: ParentNode) => {
  const tables = Array.from(root.querySelectorAll('table')) as HTMLTableElement[]
  tables.forEach((table) => {
    table.classList.add('ss-report-table')
    if (table.parentElement?.classList.contains('ss-report-table-wrap')) return
    const wrapper = document.createElement('div')
    wrapper.className = 'ss-report-table-wrap'
    table.parentNode?.insertBefore(wrapper, table)
    wrapper.appendChild(table)
  })
}

/**
 * Adds report-style semantics without changing the summary's factual text.
 * Existing Markdown remains the source of truth; all transformations are
 * classes, labels, or transparent wrappers for layout and export.
 */
export const enhanceSummaryPresentation = (root: ParentNode) => {
  decorateCallouts(root)
  decorateDialogueParagraphs(root)
  decorateHeadingsAndLists(root)
  decorateFactLines(root)
  decorateTables(root)
  decorateSections(root)
}

/**
 * 在 Markdown HTML 编译后做统一后处理：
 * - 为行内 code 标记专用类，避免被第三方 prose 默认样式误伤；
 * - 清理行内 code 中异常换行，保持前端与一键成图输出一致；
 * - 将 `(见 HH:MM:SS)` / `（见 HH:MM:SS）` 替换为可点击时间芯片（支持主流视频站跳转）。
 */
export const postProcessCompiledMarkdown = (html: string, options?: MarkdownPostProcessOptions): string => {
  if (!html) {
    return html
  }

  const template = document.createElement('template')
  template.innerHTML = html

  const codeNodes = Array.from(template.content.querySelectorAll('code')) as HTMLElement[]
  for (const code of codeNodes) {
    if (code.closest('pre')) {
      code.classList.add('ss-block-code')
      continue
    }

    code.classList.add('ss-inline-code')
    if (code.textContent) {
      code.textContent = code.textContent.replace(/\s+/g, ' ')
    }
  }

  replaceTimestampMarksWithChips(template.content, { videoUrl: options?.videoUrl })
  if (options?.enhancePresentation) {
    enhanceSummaryPresentation(template.content)
  }

  return template.innerHTML
}
