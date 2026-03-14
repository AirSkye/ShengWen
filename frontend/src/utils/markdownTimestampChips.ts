import { buildTimestampJumpUrl, toTimestampSeconds } from './videoTimeJump'

// 支持多种时间戳格式：
// - (见 HH:MM:SS) / （见 HH:MM:SS）
// - (HH:MM:SS) / （HH:MM:SS）
// - 支持半角和全角括号
// - "见"字可选
const TIMESTAMP_MARK_RE = /([（(])\s*(?:见\s*)?(\d{1,2}):([0-5]\d):([0-5]\d)\s*([）)])/g

export interface TimestampChipReplaceOptions {
  videoUrl?: string
}

const shouldSkipTextNode = (parent: HTMLElement | null) => {
  if (!parent) return true
  return Boolean(parent.closest('pre, code, a, svg, .mermaid'))
}

const createPlayIcon = () => {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('viewBox', '0 0 16 16')
  svg.setAttribute('fill', 'currentColor')
  svg.classList.add('ss-time-chip-icon')

  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  path.setAttribute('d', 'M5 3.5v9l7-4.5z')
  svg.appendChild(path)

  return svg
}

const createTimeChip = (hh: string, mm: string, ss: string, videoUrl?: string) => {
  const seconds = toTimestampSeconds(hh, mm, ss)
  const jumpUrl = buildTimestampJumpUrl(videoUrl || '', seconds)
  const chip = document.createElement(jumpUrl ? 'a' : 'span')

  chip.className = 'ss-time-jump-chip'

  const playIcon = createPlayIcon()
  chip.appendChild(playIcon)

  const textSpan = document.createElement('span')
  textSpan.textContent = hh === '00' ? `${mm}:${ss}` : `${hh}:${mm}:${ss}`
  chip.appendChild(textSpan)

  if (jumpUrl && chip instanceof HTMLAnchorElement) {
    chip.href = jumpUrl
    chip.target = '_blank'
    chip.rel = 'noopener noreferrer'
    chip.dataset.seconds = String(seconds)
    chip.title = `跳转到 ${hh}:${mm}:${ss}`
  } else {
    chip.classList.add('ss-time-jump-chip--disabled')
  }

  return chip
}

export const replaceTimestampMarksWithChips = (
  root: ParentNode,
  options?: TimestampChipReplaceOptions,
) => {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []

  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    if (shouldSkipTextNode(node.parentElement)) continue
    if (!node.nodeValue) continue
    TIMESTAMP_MARK_RE.lastIndex = 0
    if (!TIMESTAMP_MARK_RE.test(node.nodeValue)) continue
    textNodes.push(node)
  }

  for (const textNode of textNodes) {
    const raw = textNode.nodeValue || ''
    const fragment = document.createDocumentFragment()
    let lastIndex = 0
    TIMESTAMP_MARK_RE.lastIndex = 0

    let match = TIMESTAMP_MARK_RE.exec(raw)
    while (match) {
      const full = match[0]
      const index = match.index
      const hh = match[2]
      const mm = match[3]
      const ss = match[4]
      if (!hh || !mm || !ss) {
        match = TIMESTAMP_MARK_RE.exec(raw)
        continue
      }

      if (index > lastIndex) {
        fragment.appendChild(document.createTextNode(raw.slice(lastIndex, index)))
      }

      fragment.appendChild(createTimeChip(hh, mm, ss, options?.videoUrl))
      lastIndex = index + full.length
      match = TIMESTAMP_MARK_RE.exec(raw)
    }

    if (lastIndex < raw.length) {
      fragment.appendChild(document.createTextNode(raw.slice(lastIndex)))
    }

    textNode.replaceWith(fragment)
  }
}
