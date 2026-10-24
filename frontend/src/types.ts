export type Role = "admin" | "client";

export type Brand = { id: string; name: string; slug: string; logo?: string | null };

export type Organization = { org_id: string; name: string; slug: string; role: Role; brands: Brand[] };

export type Site = { id: string; url: string; label: string; images: number; last_crawled_at: string | null };

export type Status = "expire" | "<30j" | "<90j" | "ok" | "inconnue" | "illimite";

export type Confidence = "haut" | "moyen" | "a_verifier";

export type Decision = "retenu" | "ecarte" | "traite";

export type JobKind = "crawl" | "match" | "index" | "report" | "locate" | "check";

export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export type Job = {
  id: string;
  kind: JobKind;
  status: JobStatus;
  message: string;
  params: Record<string, unknown>;
  progress: { phase?: string; done?: number; total?: number; errors?: number; blocked_by_robots?: number; images_new?: number; eta_seconds?: number | null };
  result: Record<string, unknown> | null;
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** Queued, and the organization runs nothing: it only waits for a place on the server. Absent from an older answer. */
  waiting_for_capacity?: boolean;
};

/** How long a read would take: per address (null without usable history), then in total. */
export type CrawlEstimate = {
  sites: Record<string, { pages: number; seconds: number; runs_used: number } | null>;
  compare_seconds: number | null;
};

/** The pages that show one library picture, or a crop of it: `review` images are for a person to check. */
export type Occurrences = {
  pages: { url: string; image_count: number; images: { site_image_id: string; url: string; tier: "same" | "review"; thumb: string }[] }[];
  total_pages: number;
  /** Where this answer starts: the server never answers past the end of a result that shrank. */
  offset: number;
  images_found: number;
  /** Images for a person to check, whatever the filter. */
  review_images: number;
  located_at: string | null;
  last_crawled_at: string | null;
  pages_crawled: number;
};

/** A job of another brand of the organization that holds the queue: `by` is an e-mail for an administrator, « un membre de l'équipe » for a client; `text` is the sentence to show. Absent from an older answer. */
export type OrgJob = Pick<Job, "id" | "kind" | "status" | "message" | "progress" | "created_at" | "started_at"> & { brand_id: string; brand_name: string; by: string; since: string; text: string };

export type CurrentJobs = { active: Job[]; last: Job | null; organization?: OrgJob[] };

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

/** A folder of the library (a shoot, a season); `parent_id` null = top level. */
export type Collection = { id: string; parent_id: string | null; name: string };

export type LibraryItem = {
  id: string;
  filename: string;
  expiry_date: string;
  unlimited_rights: boolean;
  days_left: number | null;
  status: Status;
  credit: string;
  notes: string;
  tags: string[];
  collection_id: string | null;
  width: number | null;
  height: number | null;
  indexed: boolean;
  compared: boolean;
  /** When the visual was added to the library (ISO date and time). */
  created_at: string;
};

export type Removal = "pending" | "online" | "unread" | "suspect" | "gone";

export type Hit = {
  site_image_id: string;
  site_image_ids: string[];
  /** Names the photo: its files (CDN copies, crops, resizes) all carry the same key. */
  photo_key: string;
  file_count: number;
  /** The site versions it is on (their label, or their host). */
  versions: string[];
  /** For a photo marked removed: what the last read of its sites says (see `report._removal`). */
  removal: Removal | null;
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
  /** Unlimited rights found with certainty: nothing to treat. */
  unlimited: MatchGroup[];
  not_found: NotFoundItem[];
  outside_window: number;
  /** The labels of the brand's sites (US, FR, INT...) that show a match: what the country filter offers. */
  countries: string[];
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

export type Member = { user_id: string; email: string; role: "admin" | "client"; you: boolean };

export type ImportRow = {
  line: number;
  filename: string;
  expiry_date: string;
  unlimited_rights: boolean;
  credit: string;
  notes: string;
  /** null when the CSV has no tags column: the tags stay as they are. */
  tags: string[] | null;
  status: "ok" | "unknown_file" | "bad_date" | "bad_tags";
  message: string;
};

/** The monthly read of a brand. `next_run_at` is null when it is off; in the past, the read goes at the next pass of the worker. */
export type BrandSchedule = {
  enabled: boolean;
  day_of_month: number;
  default_day: number;
  next_run_at: string | null;
  last_run_at: string | null;
};

export type Exclusion = { id: string; reason: string; site_url: string; thumb_url: string; created_at: string };

/** One file of a photo (a crop or a size of it), and where it was read: site version and page. */
export type SiteImageVariant = {
  id: string;
  url: string;
  url_count: number;
  width: number | null;
  height: number | null;
  thumb: string;
  image: string;
  where: { site: string; page: string }[];
  where_count: number;
};

export type SiteImage = {
  id: string;
  ids: string[];
  /** Largest first; the first one is the image shown on the card. */
  variants: SiteImageVariant[];
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
  /** Why the image is where it is: found in the library, set aside by hand, recognised as a logo, or still to qualify. */
  status: SiteImageStatus;
  /** What the logo detector says (null when no image of the entry looks like a logo); `kept`: put back into the comparison. */
  logo: { score: number; kept: boolean } | null;
};

export type SiteImageStatus = "qualify" | "found" | "logo" | "ignored";
/** The tabs of "Images en ligne". */
export type SiteImageView = "qualify" | "found" | "logos" | "ignored" | "all";
/** One page of one tab, with the total of every tab and the site versions (US, FR...) of this one. */
export type SiteImagesPage = {
  view: SiteImageView;
  items: SiteImage[];
  total: number;
  counts: Record<SiteImageView, number>;
  sites: { id: string; label: string; count: number }[];
  /** Whether the brand leaves the logos out of the comparison (Réglages > Général > Logos). */
  logos_set_aside: boolean;
};

// --- labels (types of site image, contents) ---

export type LabelKind = "type" | "content";
export type LabelInfo = { id: string; kind: LabelKind; name: string; examples: number };
/** One label of an image: decided by a person (`user`) or proposed by the algorithm (`algo`, with its score). */
export type ImageLabel = { label_id: string; name: string; kind: LabelKind; source: "user" | "algo"; score: number | null };
/** How well the examples of a type predict themselves, each one classified by the others. */
export type LabelQuality = { examples: number; precision: number | null; recall: number | null };
export type LabelsAnswer = { labels: LabelInfo[]; quality: Record<string, LabelQuality>; min_examples: number };
export type LabelSampleItem = { id: string; ids: string[]; url: string; width: number | null; height: number | null; thumb: string; image: string };

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
    unlimited_rights: number;
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
  /** How many brands exist; `brands` holds at most the server's cap of them, the most recently active. */
  total_brands: number;
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

export type StaffBrand = { id: string; name: string; slug: string; created_at: string; created_by: string | null };
export type StaffOrg = { id: string; name: string; slug: string; created_at: string; members: number; brands: StaffBrand[] };
export type StaffUser = {
  user_id: string;
  email: string;
  last_sign_in_at: string | null;
  staff: boolean;
  memberships: { org_id: string; org_name: string; role: Member["role"]; since: string }[];
};
/** One line of usage against a limit (`GET /orgs/{id}/usage`): `level` turns to warning from 80 %. Storage is in bytes. */
export type UsageItem = { key: string; label: string; used: number; limit: number; level: "ok" | "warning" | "reached" };
export type OrgUsage = { month: string; items: UsageItem[]; counters: { images_new: number; bytes_downloaded: number } };
/** `GET /staff/usage`: every organization's usage, its own limits and the defaults of config.yaml. */
export type StaffUsage = {
  month: string;
  defaults: Record<string, number>;
  /** More organizations exist than the list holds. */
  truncated: boolean;
  organizations: { id: string; name: string; slug: string; overrides: Record<string, number>; items: UsageItem[]; counters: OrgUsage["counters"] }[];
};
export type StaffAdmin = { user_id: string; email: string; you: boolean };

/** One alarm of the supervision thresholds (`cloud/alerts.py`), in French. */
export type SupervisionAlert = { code: string; severity: "warning" | "critical"; message: string; /** The container it is about, when there are several. */ instance?: string };
/** `GET /staff/server`: the containers' readings and the alarms on right now. */
export type ServerUsage = { services: ServiceReading[]; alerts: SupervisionAlert[] };

/** One container's reading (`GET /staff/server`): CPU as % of one core, as in `docker stats`. */
export type ServiceReading = {
  instance: string;
  role: "web" | "worker";
  age_seconds: number;
  reading: {
    cpu_percent: number | null;
    /** Cores the container may use, or null without a limit. */
    cpu_limit: number | null;
    memory: number;
    memory_limit: number | null;
    host: { cpus: number; load: number[]; memory_total: number | null; memory_available: number | null; disk_total: number; disk_used: number };
  };
};

/** One line of the activity log (what a person did); `detail` holds counters and codes, never content. */
export type AuditEvent = {
  id: number;
  at: string;
  action: string;
  object_type: string;
  object_id: string | null;
  brand_id: string | null;
  brand: string;
  user_id: string | null;
  email: string;
  detail: Record<string, string | number | boolean | null>;
};
export type AuditPage = { events: AuditEvent[]; has_more: boolean };
