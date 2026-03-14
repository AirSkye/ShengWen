/**
 * Markdown 主题配置
 *
 * 主题系统说明：
 * 1. 使用 CSS 变量定义样式，支持动态切换
 * 2. cssVariables: 定义 CSS 变量，用于前端显示
 * 3. exportConfig: 定义一键成图时的特殊配置（字号、行高、标题缩放）
 */

export interface MarkdownTheme {
  id: string
  name: string
  description: string
  author?: string
  cssVariables: {
    [key: string]: string
  }
  exportConfig?: {
    fontSize?: number
    lineHeight?: number
    headingScale?: {
      h1?: number
      h2?: number
      h3?: number
      h4?: number
    }
  }
}

export const MARKDOWN_THEMES: Record<string, MarkdownTheme> = {
  default: {
    id: 'default',
    name: '默认风格',
    description: 'ShengWen 原始设计，简洁大方',
    author: 'ShengWen',
    cssVariables: {
      // 基础设置
      '--md-base-font-size': '1rem',
      '--md-base-line-height': '1.7',

      // 文本颜色
      '--md-text-color': '#334155',
      '--md-heading-color': '#1e293b',

      // 标题大小
      '--md-h1-font-size': '1.875rem',
      '--md-h2-font-size': '1.5rem',
      '--md-h3-font-size': '1.25rem',
      '--md-h4-font-size': '1.125rem',

      // 链接
      '--md-link-color': '#4f46e5',
      '--md-link-hover-color': '#312e81',
      '--md-link-decoration': 'none',

      // 行内代码
      '--md-code-bg': '#f1f5f9',
      '--md-code-color': '#475569',
      '--md-code-padding': '0.2em 0.4em',
      '--md-code-radius': '4px',
      '--md-code-font-size': '0.9em',

      // 代码块
      '--md-pre-bg': '#f1f5f9',
      '--md-pre-border': '#e2e8f0',
      '--md-pre-padding': '1rem',
      '--md-pre-radius': '8px',

      // 引用块
      '--md-blockquote-border': '#e2e8f0',
      '--md-blockquote-color': '#64748b',
    },
    exportConfig: {
      fontSize: 35,
      lineHeight: 1.62,
      headingScale: {
        h1: 34,
        h2: 29,
        h3: 24,
        h4: 20,
      },
    },
  },

  github: {
    id: 'github',
    name: 'GitHub 风格',
    description: '模仿 GitHub README 的经典样式',
    author: 'GitHub',
    cssVariables: {
      '--md-base-font-size': '16px',
      '--md-base-line-height': '1.6',

      '--md-text-color': '#24292f',
      '--md-heading-color': '#1f2328',

      '--md-h1-font-size': '2em',
      '--md-h2-font-size': '1.5em',
      '--md-h3-font-size': '1.25em',
      '--md-h4-font-size': '1em',

      '--md-link-color': '#0969da',
      '--md-link-hover-color': '#0550ae',
      '--md-link-decoration': 'none',

      '--md-code-bg': 'rgba(175, 184, 193, 0.2)',
      '--md-code-color': '#24292f',
      '--md-code-padding': '0.2em 0.4em',
      '--md-code-radius': '6px',
      '--md-code-font-size': '0.85em',

      '--md-pre-bg': '#f6f8fa',
      '--md-pre-border': '#d0d7de',
      '--md-pre-padding': '16px',
      '--md-pre-radius': '6px',

      '--md-blockquote-border': '#d0d7de',
      '--md-blockquote-color': '#57606a',
    },
    exportConfig: {
      fontSize: 32,
      lineHeight: 1.6,
      headingScale: {
        h1: 32,
        h2: 28,
        h3: 24,
        h4: 20,
      },
    },
  },

  typographic: {
    id: 'typographic',
    name: '排版风格',
    description: '注重阅读体验，类似 Medium，适合长文阅读',
    author: 'ShengWen',
    cssVariables: {
      '--md-base-font-size': '18px',
      '--md-base-line-height': '1.8',

      '--md-text-color': '#292929',
      '--md-heading-color': '#111111',

      '--md-h1-font-size': '2.5em',
      '--md-h2-font-size': '2em',
      '--md-h3-font-size': '1.5em',
      '--md-h4-font-size': '1.25em',

      '--md-link-color': '#007bff',
      '--md-link-hover-color': '#0056b3',
      '--md-link-decoration': 'none',

      '--md-code-bg': '#f5f5f5',
      '--md-code-color': '#e83e8c',
      '--md-code-padding': '0.25em 0.5em',
      '--md-code-radius': '4px',
      '--md-code-font-size': '0.9em',

      '--md-pre-bg': '#f8f9fa',
      '--md-pre-border': '#dee2e6',
      '--md-pre-padding': '1.25rem',
      '--md-pre-radius': '8px',

      '--md-blockquote-border': '#007bff',
      '--md-blockquote-color': '#6c757d',
    },
    exportConfig: {
      fontSize: 38,
      lineHeight: 1.75,
      headingScale: {
        h1: 36,
        h2: 30,
        h3: 26,
        h4: 22,
      },
    },
  },

  minimal: {
    id: 'minimal',
    name: '极简风格',
    description: '黑白灰配色，专注内容，去除干扰',
    author: 'ShengWen',
    cssVariables: {
      '--md-base-font-size': '1rem',
      '--md-base-line-height': '1.6',

      '--md-text-color': '#374151',
      '--md-heading-color': '#111827',

      '--md-h1-font-size': '2em',
      '--md-h2-font-size': '1.5em',
      '--md-h3-font-size': '1.25em',
      '--md-h4-font-size': '1.1em',

      '--md-link-color': '#4b5563',
      '--md-link-hover-color': '#1f2937',
      '--md-link-decoration': 'underline',

      '--md-code-bg': '#f3f4f6',
      '--md-code-color': '#374151',
      '--md-code-padding': '0.2em 0.4em',
      '--md-code-radius': '3px',
      '--md-code-font-size': '0.9em',

      '--md-pre-bg': '#ffffff',
      '--md-pre-border': '#e5e7eb',
      '--md-pre-padding': '1rem',
      '--md-pre-radius': '4px',

      '--md-blockquote-border': '#d1d5db',
      '--md-blockquote-color': '#6b7280',
    },
    exportConfig: {
      fontSize: 34,
      lineHeight: 1.6,
      headingScale: {
        h1: 32,
        h2: 28,
        h3: 24,
        h4: 20,
      },
    },
  },

  warm: {
    id: 'warm',
    name: '暖色调',
    description: '温暖舒适的配色，适合生活类、情感类内容',
    author: 'ShengWen',
    cssVariables: {
      '--md-base-font-size': '1rem',
      '--md-base-line-height': '1.7',

      '--md-text-color': '#5d4e37',
      '--md-heading-color': '#3d3229',

      '--md-h1-font-size': '1.875rem',
      '--md-h2-font-size': '1.5rem',
      '--md-h3-font-size': '1.25rem',
      '--md-h4-font-size': '1.125rem',

      '--md-link-color': '#d97706',
      '--md-link-hover-color': '#b45309',
      '--md-link-decoration': 'none',

      '--md-code-bg': '#fef3c7',
      '--md-code-color': '#92400e',
      '--md-code-padding': '0.2em 0.4em',
      '--md-code-radius': '4px',
      '--md-code-font-size': '0.9em',

      '--md-pre-bg': '#fffbeb',
      '--md-pre-border': '#fcd34d',
      '--md-pre-padding': '1rem',
      '--md-pre-radius': '8px',

      '--md-blockquote-border': '#fcd34d',
      '--md-blockquote-color': '#b45309',
    },
    exportConfig: {
      fontSize: 35,
      lineHeight: 1.62,
      headingScale: {
        h1: 34,
        h2: 29,
        h3: 24,
        h4: 20,
      },
    },
  },
}

export type MarkdownThemeId = keyof typeof MARKDOWN_THEMES

// 默认主题
export const DEFAULT_THEME: MarkdownThemeId = 'default'

