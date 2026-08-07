export const TaskStatus = {
  PENDING: "PENDING",
  DOWNLOADING: "DOWNLOADING",
  UPLOADING: "UPLOADING",
  TRANSCRIBING: "TRANSCRIBING",
  SUMMARIZING: "SUMMARIZING",
  COMPLETED: "COMPLETED",
  FAILED: "FAILED"
} as const;

export type TaskStatus = typeof TaskStatus[keyof typeof TaskStatus];
export type SummaryMode = 'standard' | 'agent' | 'auto';
export type SummaryStyle = 'classic' | 'report';

export interface TranscriptionMeta {
  provider?: string;
  stage?: string;
  message?: string;
  upload_progress?: number;
  remote_progress?: number;
  remote_status?: string | number;
  remote_task_id?: string;
  subtitle_path?: string;
  text_path?: string;
  subtitle_count?: number;
  elapsed_seconds?: number;
  poll_count?: number;
  uploaded_bytes?: number;
  total_bytes?: number;
}

export interface Task {
  id: string;
  video_url: string;
  status: TaskStatus;
  created_at: string;
  latest_modified_at?: string;
  progress: number;
  title?: string;
  topic?: string;
  transcript?: string;
  summary?: string;
  error_message?: string;
  transcription_time?: number;
  audio_duration?: number;
  author_name?: string;
  author_url?: string;
  summary_mode?: SummaryMode;
  summary_style?: SummaryStyle;
  summary_chunk_total?: number;
  summary_chunk_done?: number;
  summary_meta?: string;
  transcription_meta?: string;
  folder_id?: string | null;
}

export interface CreateTaskRequest {
  video_url: string;
  quality: string;
  summary_mode?: Exclude<SummaryMode, 'auto'> | SummaryMode;
  summary_style?: SummaryStyle;
}

export interface RawTextTaskRequest {
  text: string;
  title?: string;
  summary_mode?: Exclude<SummaryMode, 'auto'> | SummaryMode;
  summary_style?: SummaryStyle;
}

export interface MarkdownHeadingItem {
  id: string;
  text: string;
  level: number;
}

export interface LLMProvider {
  id: string;
  label: string;
  default_base_url: string;
  default_model_id: string;
  description: string;
}

export interface LLMProfile {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  model_id: string;
  temperature: number;
  context_window_size: number;
  has_api_key: boolean;
  api_key_hint: string;
}

export interface LLMSettings {
  active_profile_id: string;
  profiles: LLMProfile[];
}

export interface CreateProfileRequest {
  name: string;
  provider: string;
  base_url?: string;
  api_key?: string;
  model_id?: string;
  temperature?: number;
}

export interface UpdateProfileRequest {
  profile_id: string;
  name?: string;
  provider?: string;
  base_url?: string;
  api_key?: string;
  model_id?: string;
  temperature?: number;
  context_window_size?: number;
}

export interface SwitchActiveProfileRequest {
  profile_id: string;
}

export interface TranscriptionSettings {
  transcription_provider: "tingwu" | "fast_whisper";
  tingwu_enabled: boolean;
  tingwu_config_path: string;
  tingwu_config_resolved: string;
  tingwu_configured: boolean;
  tingwu_poll_interval_sec: number;
  tingwu_timeout_sec: number;
  tingwu_fallback_to_whisper: boolean;
  device: "cpu" | "cuda";
  model_source: "auto_download" | "manual_path";
  model_size: "tiny" | "base" | "small" | "medium" | "large";
  model_path: string;
  model_path_valid: boolean;
  model_path_message: string;
  model_path_resolved: string;
  required_model_files: string[];
  cuda_available: boolean;
  available_devices: string[];
  has_nvidia_gpu: boolean;
  torch_installed: boolean;
  torch_cuda_built: boolean;
  ctranslate2_installed: boolean;
  ctranslate2_cuda_device_count: number;
  cuda_reason: string;
  cuda_message: string;
  enable_bilibili_subtitle_fetch: boolean;
  has_bilibili_sessdata: boolean;
  bilibili_cookie_source: string;
  bilibili_sessdata_masked: string;
}

export interface UpdateTranscriptionSettingsRequest {
  tingwu_enabled?: boolean;
  tingwu_config_path?: string;
  tingwu_poll_interval_sec?: number;
  tingwu_timeout_sec?: number;
  tingwu_fallback_to_whisper?: boolean;
  device?: "cpu" | "cuda";
  model_source?: "auto_download" | "manual_path";
  model_size?: "tiny" | "base" | "small" | "medium" | "large";
  model_path?: string;
  enable_bilibili_subtitle_fetch?: boolean;
  bilibili_sessdata?: string;
  clear_bilibili_sessdata?: boolean;
}

export interface TingwuAuthCheckResult {
  configured: boolean;
  valid: boolean;
  message: string;
  updated?: boolean;
}

export interface SummarizationSettings {
  mode: SummaryMode;
  auto_chunk_min_audio_duration_sec: number;
  auto_chunk_min_transcript_lines: number;
  auto_chunk_min_plain_text_chars: number;
  chunk_target_duration_sec: number;
  chunk_min_duration_sec: number;
  chunk_max_duration_sec: number;
  boundary_jump_sec: number;
  prev_tail_timestamp_lines_m: number;
  prev_summary_tail_chars_j: number;
  summary_detail_level: number;
  llm_call_retry_max: number;
  max_agent_value_chars: number;
  fallback_to_standard_on_agent_error: boolean;
}

export interface UpdateSummarizationSettingsRequest {
  mode?: SummaryMode;
  auto_chunk_min_audio_duration_sec?: number;
  auto_chunk_min_transcript_lines?: number;
  auto_chunk_min_plain_text_chars?: number;
  chunk_target_duration_sec?: number;
  chunk_min_duration_sec?: number;
  chunk_max_duration_sec?: number;
  boundary_jump_sec?: number;
  prev_tail_timestamp_lines_m?: number;
  prev_summary_tail_chars_j?: number;
  summary_detail_level?: number;
  llm_call_retry_max?: number;
  max_agent_value_chars?: number;
  fallback_to_standard_on_agent_error?: boolean;
}

export interface BilibiliCookieFromBrowserResult {
  success: boolean;
  sessdata?: string;
  sessdata_masked?: string;
  source_browser?: string;
  error?: string;
}

export interface BilibiliVideoPartInfo {
  index: number;
  cid: number;
  title: string;
  duration: number;
}

export interface BilibiliVideoInfo {
  is_multi_part: boolean;
  title: string;
  bvid: string;
  duration: number;
  parts?: BilibiliVideoPartInfo[];
}

export interface BilibiliPartsConfig {
  mode: 'merge' | 'separate';
  indices: number[];
}

export interface LocalFolderFile {
  name: string;
  path: string;
  size: number;
}

export interface LocalFolderScanResult {
  folder_path: string;
  files: LocalFolderFile[];
  total: number;
}

export interface LocalPathCheckResult {
  type: 'file' | 'folder' | 'not_found';
  path: string;
}

export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  folder_type: 'auto' | 'manual';
  source_video_url: string | null;
  sort_order: number;
  created_at: string;
  task_ids?: string[];
}

export interface FolderTreeNode extends Folder {
  children: FolderTreeNode[];
}

export type FolderNode = FolderTreeNode
