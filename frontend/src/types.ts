export type Role = "admin" | "client";

export type Brand = { id: string; name: string; slug: string };

export type Organization = { org_id: string; name: string; slug: string; role: Role; brands: Brand[] };

export type Site = { id: string; url: string; label: string; images: number; last_crawled_at: string | null };

export type Status = "expire" | "<30j" | "<90j" | "ok" | "inconnue";

export type Confidence = "haut" | "moyen" | "a_verifier";

export type Decision = "retenu" | "ecarte" | "traite";

export type JobKind = "crawl" | "match" | "index" | "report";

export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export type Job = {
  id: string;
  kind: JobKind;
  status: JobStatus;
  message: string;
  params: Record<string, unknown>;
  progress: { phase?: string; done?: number; total?: number; errors?: number; blocked_by_robots?: number; images_new?: number };
  result: Record<string, unknown> | null;
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type CurrentJobs = { active: Job[]; last: Job | null };

export type Upcoming = { reference_id: string; filename: string; expiry_date: string; days_left: number; online: boolean };

export type Overview = {
  organization: { id: string; name: string; slug: string };
  brand: { id: string; name: string };
  role: Role;
  stats: {
    reference_images: number;
    pages_crawled: number;
    site_images: number;
    matches: number;
    references_pending_index: number;
  };
  dashboard: {
    expired_online: number;
    urgent_online: number;
    pending_review: number;
    references_online: number;
    upcoming: Upcoming[];
    /** Images read on the sites that match nothing in the library (rights unknown). */
    unreferenced_online: number;
  };
  jobs: CurrentJobs;
  last_crawl: null | {
    site_url: string;
    site_label: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    pages_visited: number;
    images_new: number;
  };
  sites: Site[];
  defaults: { max_pages: number; max_pages_limit: number; within_days: number };
};

export type LibraryItem = {
  id: string;
  filename: string;
  expiry_date: string;
  days_left: number | null;
  status: Status;
  credit: string;
  notes: string;
  tags: string[];
  width: number | null;
  height: number | null;
  indexed: boolean;
  compared: boolean;
  thumb_url: string;
  url: string;
};

export type Hit = {
  site_image_id: string;
  site_image_ids: string[];
  site_url: string;
  site_image: string;
  site_thumb: string;
  pages: string[];
  page_count: number;
  level: string;
  score: number;
  confidence: Confidence;
  decision: Decision | null;
};

export type MatchGroup = {
  reference_id: string;
  filename: string;
  expiry_date: string | null;
  days_left: number | null;
  status: Status;
  credit: string;
  notes: string;
  ref_image: string;
  ref_thumb: string;
  hits: Hit[];
};

export type NotFoundItem = {
  reference_id: string;
  filename: string;
  expiry_date: string | null;
  days_left: number | null;
  status: Status;
  credit: string;
  compared: boolean;
  ref_thumb: string;
};

export type Matches = {
  within_days: number;
  confirmed: MatchGroup[];
  to_verify: MatchGroup[];
  later: MatchGroup[];
  not_found: NotFoundItem[];
  outside_window: number;
};

export type Scan = {
  id: string;
  site_id: string;
  site_url: string;
  site_label: string;
  status: "running" | "done" | "error" | "cancelled";
  started_at: string;
  finished_at: string | null;
  pages_visited: number;
  images_found: number;
  images_new: number;
  blocked_by_robots: number;
  errors: string[];
  error_count: number;
};

export type Report = {
  id: string;
  within_days: number;
  generated_at: string;
  stats: Record<string, number>;
  files: { "report.html": string; "matches.csv": string; "not_found.csv": string };
};

export type SettingsSection = Record<string, number | boolean | string | string[]>;

export type Settings = {
  overrides: { crawl?: SettingsSection; match?: SettingsSection; report?: SettingsSection };
  defaults: { crawl: SettingsSection; match: SettingsSection; report: SettingsSection };
  effective: { crawl: SettingsSection; match: SettingsSection; report: SettingsSection };
};

export type ImportRow = {
  line: number;
  filename: string;
  expiry_date: string;
  credit: string;
  notes: string;
  /** null when the CSV has no tags column: the tags stay as they are. */
  tags: string[] | null;
  status: "ok" | "unknown_file" | "bad_date" | "bad_tags";
  message: string;
};

export type Exclusion = { id: string; reason: string; site_url: string; thumb_url: string; created_at: string };

export type SiteImage = {
  id: string;
  ids: string[];
  url: string;
  urls: string[];
  url_count: number;
  filename: string;
  pages: string[];
  page_count: number;
  site_ids: string[];
  sites: string[];
  width: number | null;
  height: number | null;
  compared: boolean;
  first_seen: string;
  thumb: string;
  image: string;
};

// --- insights (Statistiques, back office) ---

export type CrawlRun = {
  id: string;
  org_name: string;
  brand_name: string | null;
  site_url: string;
  status: JobStatus;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  pages_visited: number;
  images_found: number;
  images_stored: number;
  images_new: number;
  blocked_by_robots: number;
  error_count: number;
  sitemap_urls: number | null;
  pages_failed: number | null;
  images_known: number | null;
  images_duplicate: number | null;
  images_rejected: number | null;
  downloads: number | null;
  downloads_failed: number | null;
  bytes_downloaded: number | null;
  bytes_new: number | null;
  embedded: number | null;
  embed_seconds: number | null;
  pages_per_minute: number | null;
  images_scanned_per_second: number | null;
  clip_images_per_second: number | null;
  seconds_per_page: number | null;
  avg_new_image_bytes: number | null;
  images_per_page: number | null;
};

export type Phases = {
  render_seconds: number;
  download_seconds: number;
  process_seconds: number;
  embed_seconds: number;
  store_seconds: number;
};

export type CrawlSummary = {
  runs: number;
  finished: number;
  failed: number;
  avg_duration_seconds: number | null;
  max_duration_seconds: number | null;
  avg_pages: number | null;
  avg_pages_per_minute: number | null;
  avg_images_scanned_per_second: number | null;
  avg_seconds_per_page: number | null;
  avg_images_per_page: number | null;
  clip_images_per_second: number | null;
  avg_new_image_bytes: number | null;
  images_found: number;
  images_new: number;
  bytes_downloaded: number;
  bytes_new: number;
  phases_seconds: Phases;
  http_statuses: Record<string, number>;
  formats_new: Record<string, number>;
};

export type JobKindStats = {
  kind: JobKind;
  total: number;
  done: number;
  failed: number;
  cancelled: number;
  avg_run_seconds: number | null;
  p95_run_seconds: number | null;
  avg_wait_seconds: number | null;
};

export type CompareStats = {
  full: boolean;
  references: number;
  site_images: number;
  excluded: number;
  pairs: number;
  hits: number;
  hits_by_level: Record<string, number>;
  verified_candidates?: number;
  compare_seconds?: number;
  verify_seconds?: number;
  seconds: number;
  pairs_per_second: number | null;
  finished_at: string;
};

export type MatchBucket = {
  matches: number;
  reviewed: number;
  to_remove: number;
  removed: number;
  false_positives: number;
  false_positive_rate: number | null;
};

export type MatchingStats = {
  total: MatchBucket & { review_progress: number | null };
  by_confidence: Partial<Record<Confidence, MatchBucket>>;
  by_level: Record<string, MatchBucket>;
};

export type OrgInsights = {
  site: {
    image_urls: number;
    distinct_files: number;
    files_with_size: number;
    total_bytes: number | null;
    avg_bytes: number | null;
    median_bytes: number | null;
    max_bytes: number | null;
    avg_width: number | null;
    avg_height: number | null;
    avg_megapixels: number | null;
    stored_bytes: number | null;
    pages_read: number;
    image_page_links: number;
    sites: number;
  };
  formats: { format: string; files: number; total_bytes: number | null; avg_bytes: number | null }[];
  library: {
    references_total: number;
    references_indexed: number;
    expired: number;
    expiring_30_days: number;
    expiring_90_days: number;
    without_expiry: number;
    total_bytes: number | null;
    avg_bytes: number | null;
    avg_megapixels: number | null;
  };
  crawls: { summary: CrawlSummary; last: CrawlRun | null; history: CrawlRun[] };
  jobs: JobKindStats[];
  compare: CompareStats | null;
  matching: MatchingStats;
};

export type PlatformBrand = {
  brand_id: string;
  name: string;
  slug: string;
  org_id: string;
  org_name: string;
  org_slug: string;
  sites: number;
  site_stored_bytes: number | null;
  distinct_files: number;
  site_bytes: number | null;
  avg_image_bytes: number | null;
  pages_read: number;
  references_total: number;
  expired: number;
  expiring_90_days: number;
  library_bytes: number | null;
  matches: number;
  members: number;
  storage_bytes: number;
  crawls_90d: number;
  last_crawl_at: string | null;
  last_crawl_status: JobStatus | null;
  last_crawl_seconds: number | null;
  avg_crawl_seconds: number | null;
  avg_pages_per_minute: number | null;
  clip_images_per_second: number | null;
  false_positive_rate: number | null;
  jobs_30d: number;
  failed_jobs_30d: number;
};

export type PlatformInsights = {
  totals: {
    organizations: number;
    brands: number;
    members: number;
    references: number;
    site_files: number;
    pages_read: number;
    matches: number;
    storage_bytes: number;
  };
  brands: PlatformBrand[];
  crawls: {
    summary: CrawlSummary;
    history: Pick<
      CrawlRun,
      | "org_name"
      | "brand_name"
      | "started_at"
      | "status"
      | "duration_seconds"
      | "pages_visited"
      | "images_found"
      | "images_new"
      | "pages_per_minute"
      | "images_scanned_per_second"
      | "clip_images_per_second"
      | "avg_new_image_bytes"
    >[];
  };
  jobs: JobKindStats[];
  compare: CompareStats | null;
  matching: MatchingStats;
  queue: {
    queued: number;
    running: number;
    oldest_queued_seconds: number | null;
    last_heartbeat_seconds: number | null;
    jobs_24h: number;
    failed_24h: number;
    last_finished_at: string | null;
  };
  running: { id: string; org_name: string; brand_name: string | null; kind: JobKind; message: string; started_at: string; heartbeat_age_seconds: number | null }[];
  failures: { id: string; org_name: string; brand_name: string | null; kind: JobKind; error: string | null; finished_at: string | null }[];
};
