export interface PipelineEvent {
  event: string
  stage?: string
  fraction?: number
  message?: string
  job_id?: string
  ok?: boolean
  error?: string
  stderr?: string
  [key: string]: unknown
}

export interface Adjustment {
  rule: string
  factor: number
  reason: string
}

export interface MusicBrief {
  genre: string
  instruments: string[]
  mood: string
  theme: string
  energy: string
  bpm_range: string
  duck_intensity: string
  mood_prior?: string
  alternatives: { genre: string; mood: string; bpm_range: string }[]
}

export interface Clip {
  start: number
  end: number
  score: number
  best_platform: string
  platform_scores: Record<string, number>
  subscores: Record<string, number>
  adjustments: Adjustment[]
  signals_fired: string[]
  signals_missing: string[]
  confidence: string
  summary: string
  arousal_pct: number
  heatmap_pct: number | null
  curve_score: number
  music: MusicBrief | null
  t1_raw?: Record<string, unknown>
}

export interface RenderOutput {
  clip: number
  path: string
  score: number
  best_platform: string
  duration: number
  words: number
  event_tags: number
  /* EBU R128 of the delivered file. THREE states, all real and all reachable:
   *  absent - every render.json written before this shipped, and it stays
   *           reachable on new jobs because render_clip.py replaces the entry;
   *  null   - the measurement ran and failed (no audio stream, ffmpeg gone);
   *  number - a reading.
   * api.ts's invoke<JobResults> is an unchecked assertion, so typing these
   * `number` compiles clean and then throws TypeError on .toFixed() for every
   * clip above, blanking the audit panel. `?: number | null` is what makes tsc
   * demand the guard, and `!= null` is the one comparison catching both. */
  lufs?: number | null
  true_peak?: number | null
}

export interface JobResults {
  job_id: string
  dir: string
  ingest: {
    title: string
    heatmap: unknown[] | null
    probe: { duration_sec: number; width: number; height: number }
  } | null
  score: { clips: Clip[]; llm_mode: string; model: string; scored_count: number } | null
  render: { outputs: RenderOutput[]; emoji_ok: boolean; caption_preset: string; lufs_target?: number } | null
  events: { counts: Record<string, number>; timeline: unknown[]; arousal_source: string } | null
  candidates: { count: number; effective_weights: Record<string, number>; heatmap_present: boolean } | null
}

/** One HUD element worth keeping, normalized to the screenshot it was drawn
 *  on. Normalization survives a resolution change; it does NOT survive an
 *  aspect change, because games re-lay-out their HUD instead of rescaling it —
 *  which is why GamePreset stamps the aspect it was drawn at. */
export interface HudRegion {
  name: string
  /** 'watch' is read by the scan but NEVER laid out. A players-alive box is
   *  169x41px of information that costs 262px of a 1920px frame as a margin
   *  band, because a margin is scaled to the full 1080 width. render/layout
   *  only looks at margin_top/margin_bottom/main, so anything else is
   *  invisible to it by construction. */
  role: 'margin_top' | 'margin_bottom' | 'main' | 'watch'
  x: number
  y: number
  w: number
  h: number
  /** What the watch stage should ASK about this box, per sample, all night.
   *  Undefined means "layout only, never read" — which is free. This is the
   *  field that makes selection game-specific: the box says where to look and
   *  the tell says what the answer means. Values must exist in
   *  terraclip_pipeline/vision.py TELLS. */
  tell?: string
}

/** One number the model is allowed to answer with. `max`/`step` become the
 *  enum: a bounded field is the only thing that stopped a 0-6 counter coming
 *  back as 49. */
export interface TellField {
  name: string
  max: number
  step: number
}

/** A MODULE: one question asked of one box, and what the answer means. The
 *  editor's shape; see tells.ts for the pipeline's. */
export interface Tell {
  id: string
  label: string
  prompt: string
  fields: TellField[]
  /** '' | 'count' | 'gap' */
  score: { kind: string; field: string; saturate: number }
  /** '' | 'drop' | 'rise' */
  events: { kind: string; field: string; saturate: number; lead: number; trail: number }
  curve_weight: number
  /** Seconds between asks. 0 = every sample. */
  interval: number
  upscale: number
  /** Keys a newer pipeline understands and this editor does not; carried
   *  through a save rather than silently deleted. */
  extra?: Record<string, unknown>
}

export interface GamePreset {
  name: string
  /** The judgement profile this game inherits (presets.py). */
  base: string
  /** width/height of the screenshot the boxes were drawn on. */
  aspect: number
  /** Absolute path to the footage or screenshot the boxes were drawn on, so
   *  they can be re-opened and redrawn later. Null once the file moves. */
  shot: string | null
  /** Seconds into `shot` when it is a video — reopening seeks back here, so a
   *  redraw starts from the same frame the boxes were drawn against. */
  shot_t: number
  regions: HudRegion[]
  /** Modules this preset defines for itself, keyed by tell id, in the
   *  pipeline's own JSON shape. A preset is not limited to the questions that
   *  happened to be useful for the games already shipped. */
  tells?: Record<string, unknown>
  /** The in-game name a `{player}` module matches against. Personal, not part
   *  of the map — `terraclip preset share` strips it. */
  player?: string
  /** Which side is yours, for `{friendly}`/`{enemy}` in a field name. */
  friendly?: string
  /** Provenance, in the file, for whoever inherits it. A box invented from
   *  memory of a default HUD and a box dragged onto a real frame produce
   *  identical JSON; the difference is four hours of scanning. Shipped presets
   *  say PLACEHOLDER or CHECKED and the pipeline's test suite enforces it. */
  _note?: string
}

export interface JobSummary {
  id: string
  title: string | null
  ingested: boolean
  rendered: boolean
}

export interface SetupState {
  has_gemini_key: boolean
  onboarded: boolean
}

/* ---------- the Instagram loop ---------- */

export interface LoopMetrics {
  views?: number | null
  reach?: number | null
  likes?: number | null
  comments?: number | null
  saved?: number | null
  shares?: number | null
  reposts?: number | null
  total_interactions?: number | null
  ig_reels_avg_watch_time?: number | null
  ig_reels_video_view_total_time?: number | null
  reels_skip_rate?: number | null
}

export interface LoopLinked {
  job_id: string
  clip_index: number
  media_id: string | null
  link_source: string
  linked_at: number
  score: number
  reels_score: number
  config_version: number
  subscores: Record<string, number> | null
  adjustments: Adjustment[] | null
  signals_fired: string[] | null
  signals_missing: string[] | null
  summary: string
  clip_duration: number | null
  clip_thumb: string | null
  ig_thumb: string | null
  permalink: string | null
  caption: string | null
  posted_at: number | null
  media_deleted: boolean
  media_age_hours: number | null
  settling: boolean
  metrics: LoopMetrics | null
  snapshots: { age_hours: number | null; views: number | null }[]
  snapshot_count: number
}

export interface LoopSuggestion {
  media_id: string
  job_id: string
  clip_index: number
  confidence: number
  clip_summary: string
  clip_duration: number | null
  clip_thumb: string | null
  clip_reels_score: number | null
}

export interface LoopUnlinked {
  media_id: string
  thumb: string | null
  permalink: string | null
  caption: string
  posted_at: number | null
  duration_s: number | null
  copyright_flagged: boolean
  suggestion: LoopSuggestion | null
}

export interface LoopClip {
  job_id: string
  clip_index: number
  summary: string
  duration: number | null
  reels_score: number | null
  thumb: string | null
  linked: boolean
}

export interface CalibrationVersion {
  version: number
  constants: Record<string, number>
  fitted_from_n?: number | null
  spearman_rho?: number | null
  pairwise_acc?: number | null
  note?: string | null
  created_at?: number | null
}

export interface LoopReport {
  pairs: number
  ready: boolean
  note?: string
  spearman_rho?: number | null
  pairwise_accuracy?: number | null
  kendall_tau?: number | null
}

export interface LoopOverview {
  connected: boolean
  username: string | null
  last_synced_at: number | null
  linked: LoopLinked[]
  unlinked: LoopUnlinked[]
  clip_library: LoopClip[]
  report: LoopReport
  calibration: {
    active: CalibrationVersion
    history: CalibrationVersion[]
    qualifying_outcomes: number
    recomputable_outcomes: number
    threshold: number
  }
}

export interface SyncSummary {
  ok: boolean
  error?: string
  username?: string
  new_media?: number
  thumbs_cached?: number
  snapshots_pulled?: number
  tombstoned?: number
  fit?: { applied: boolean; version?: number; reason?: string }
}


/** One model Ollama has pulled, as the pipeline sees it. */
export interface OllamaModel {
  name: string
  bytes: number
  params: string | null
  quant: string | null
  family: string | null
  chat: boolean
  coder: boolean
  /** Can it look at an image? The watch stage reads HUD CROPS, so a model
   *  without this cannot do gameplay at all however good its prose is —
   *  which is invisible in a list of names. */
  vision: boolean
  /** Bigger than the manifest bound the picker will consider resident. */
  oversized: boolean
}

/** What the machine can run, read off the hardware rather than benchmarked. */
export interface Hardware {
  gpu: string | null
  vram_gb: number | null
  ram_gb: number | null
  /** True only when the figure came from nvidia-smi. Windows' AdapterRAM is a
   *  32-bit field, so every card 4 GB and over reads identically. */
  trusted: boolean
  vram_note?: string | null
  advice: {
    budget_gb: number | null
    headline: string
    body: string
    start_with: string
  }
}

export interface OllamaLibrary {
  running: boolean
  error?: string
  url?: string
  /** What a run would actually use right now. */
  chosen?: string | null
  /** What the picker would choose on its own. */
  auto?: string | null
  auto_error?: string | null
  pinned?: string | null
  /** TERRACLIP_OLLAMA_MODEL, which outranks a pinned choice. */
  env?: string | null
  max_manifest_bytes?: number
  models: OllamaModel[]
  /** Only present when asked for — probing costs a second or two. */
  hardware?: Hardware
}


/** A caption style, named for what it looks like rather than for a person, and
 *  carrying a still rendered by the pipeline's own renderer. Two of the three
 *  caption faces are not loaded in the webview, so a CSS mock-up would show a
 *  style nobody ever gets. */
export interface CaptionStyle {
  name: string
  blurb: string
  /** Absolute path under ~/.terraclip — inside the asset-protocol scope. */
  preview: string | null
}
