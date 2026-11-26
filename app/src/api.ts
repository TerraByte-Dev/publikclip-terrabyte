import { invoke, convertFileSrc } from '@tauri-apps/api/core'
import type {
  CaptionStyle,
  GamePreset,
  JobResults,
  JobSummary,
  LoopOverview,
  OllamaLibrary,
  SetupState,
  SyncSummary
} from './types'

export const api = {
  runJob: (source: string, llm: string, captions: string, preset: string, game: string | null) =>
    invoke<void>('run_job', { source, llm, captions, preset, game }),
  resumeJob: (jobId: string, llm?: string, captions?: string, camera?: string) =>
    invoke<void>('resume_job', { jobId, llm, captions, camera }),
  jobResults: (jobId: string) => invoke<JobResults>('job_results', { jobId }),
  listJobs: () => invoke<JobSummary[]>('list_job_dirs'),
  deleteJob: (jobId: string) => invoke<void>('delete_job', { jobId }),
  presetList: () => invoke<GamePreset[]>('preset_list'),
  presetSave: (preset: GamePreset) => invoke<string>('preset_save', { preset }),
  presetDelete: (name: string) => invoke<void>('preset_delete', { name }),
  allowMedia: (path: string) => invoke<string>('allow_media', { path }),
  saveGeminiKey: (key: string) => invoke<boolean>('save_gemini_key', { key }),
  setupState: () => invoke<SetupState>('get_setup_state'),
  markOnboarded: () => invoke<void>('mark_onboarded'),
  checkOllama: () => invoke<{ running: boolean; models: string[] }>('check_ollama'),
  /** The local model library and which one would judge. `pin` sets the stored
   *  choice; '' clears it and returns to the automatic pick. */
  ollamaLibrary: (pin?: string, hardware?: boolean) =>
    invoke<OllamaLibrary>('ollama_library', { pin, hardware }),
  /** Caption styles, each with a still rendered by the real caption renderer. */
  /** The paste-anywhere page explaining how to add a game. */
  presetBrief: (game?: string) => invoke<string>('preset_brief', { game }),
  captionStyles: (force?: boolean) =>
    invoke<{ styles: CaptionStyle[]; dir: string }>('caption_styles', { force }),
  exportClip: (path: string, title?: string) =>
    invoke<string>('export_clip', { path, title }),
  igStatus: () => invoke<{ connected: boolean; username?: string }>('ig_status'),
  igSync: () => invoke<SyncSummary>('ig_tool', { args: ['sync'] }),
  igOverview: () => invoke<LoopOverview>('ig_tool', { args: ['overview'] }),
  igLink: (jobId: string, clip: number, mediaId: string, source: 'manual' | 'match_confirmed') =>
    invoke<{ ok: boolean }>('ig_tool', {
      args: ['link', jobId, String(clip), mediaId, '--source', source]
    }),
  igUnlink: (mediaId: string) =>
    invoke<{ ok: boolean }>('ig_tool', { args: ['unlink', mediaId] }),
  igReject: (mediaId: string, jobId: string, clip: number) =>
    invoke<{ ok: boolean }>('ig_tool', { args: ['reject', mediaId, jobId, String(clip)] }),
  fileUrl: (path: string) => convertFileSrc(path)
}
