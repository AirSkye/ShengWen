export const formatDuration = (seconds?: number) => {
  if (!seconds || Number.isNaN(seconds) || seconds <= 0) return '--'
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  if (h > 0) return `${pad(h)}:${pad(m)}:${pad(s)}`
  return `${pad(m)}:${pad(s)}`
}

export const formatTranscriptionDuration = (seconds?: number) => {
  if (!seconds || Number.isNaN(seconds) || seconds <= 0) return '--'
  return `${seconds.toFixed(1)}s`
}

export const formatConversionRatio = (audioSeconds?: number, transcriptionSeconds?: number) => {
  if (
    !audioSeconds
    || Number.isNaN(audioSeconds)
    || audioSeconds <= 0
    || !transcriptionSeconds
    || Number.isNaN(transcriptionSeconds)
    || transcriptionSeconds <= 0
  ) {
    return '--'
  }
  return (audioSeconds / transcriptionSeconds).toFixed(2)
}

export const formatDateTime = (date: string | Date) => {
  const d = typeof date === 'string' ? new Date(date) : date
  return d.toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export const countWords = (markdown: string) => {
  if (!markdown) return 0
  const plain = markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]*`/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[>#*_~\-|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  const cjkCount = (plain.match(/[\u4E00-\u9FFF]/g) || []).length
  const latinWords = (plain.replace(/[\u4E00-\u9FFF]/g, ' ').match(/[A-Za-z0-9]+/g) || []).length
  return cjkCount + latinWords
}

export const stripDoubleBracePlaceholders = (text: string) => {
  if (!text) return ''
  return text
    .replace(/\{\{[\s\S]*?\}\}/g, '')
    .replace(/<p>\s*<\/p>/g, '')
    .trim()
}

export const stripSummaryPresentationMarkers = (text: string) => {
  if (!text) return ''
  const cleanedLines: string[] = []
  let insidePresentationBlock = false

  for (const line of text.replace(/\r\n?/g, '\n').split('\n')) {
    const calloutMarker = line.match(
      /^\s*>\s*\[![A-Z][A-Z0-9_-]{0,31}\]\s*(.*)$/i,
    )
    if (calloutMarker) {
      insidePresentationBlock = true
      const inlineContent = (calloutMarker[1] || '').trim()
      if (inlineContent) cleanedLines.push(inlineContent)
      continue
    }

    if (insidePresentationBlock) {
      const quotedLine = line.match(/^\s*>\s?(.*)$/)
      if (quotedLine) {
        cleanedLines.push(quotedLine[1] || '')
        continue
      }
      insidePresentationBlock = false
    }

    const plainMarker = line.match(
      /^\s*\[![A-Z][A-Z0-9_-]{0,31}\]\s*(.*)$/i,
    )
    if (plainMarker) {
      const inlineContent = (plainMarker[1] || '').trim()
      if (inlineContent) cleanedLines.push(inlineContent)
      continue
    }

    if (/^\s*<!--\s*sw:[a-z][a-z0-9_-]{0,31}\s*-->\s*$/i.test(line)) {
      continue
    }
    cleanedLines.push(line)
  }

  return cleanedLines.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

export const isLinkableSource = (value?: string | null) => {
  const source = (value || '').trim()
  return /^https?:\/\//i.test(source) || /^file:\/\//i.test(source)
}
