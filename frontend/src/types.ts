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
  status: "ok" | "unknown_file" | "bad_date";
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
