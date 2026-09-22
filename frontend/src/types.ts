export type View = "library" | "explore" | "results";

export type JobStatus = "running" | "done" | "error";

export type Job = {
  id: string;
  kind: string;
  status: JobStatus;
  message: string;
  progress: {
    done?: number;
    total?: number;
    pages_visited?: number;
    errors?: number;
    blocked_by_robots?: number;
  };
  error: string | null;
  result: { matches?: number; errors?: string[]; blocked_by_robots?: number } | null;
};

export type LibraryItem = {
  filename: string;
  expiry_date: string;
  credit: string;
  notes: string;
  indexed: boolean;
  url: string;
};

export type Overview = {
  stats: {
    reference_images: number;
    pages_crawled: number;
    pages_pending: number;
    site_images: number;
    matches: number;
  };
  library: LibraryItem[];
  job: Job | null;
  defaults: {
    max_pages: number;
    within_days: number;
  };
};

export type Decision = "" | "retenu" | "ecarte" | "traite";

export type Hit = {
  site_image_id: number;
  site_image_ids?: number[];
  site_image: string;
  site_url: string;
  pages: string[];
  page_count?: number;
  level: string;
  score: number;
  confidence: string;
  decision: Decision | null;
};

export type MatchGroup = {
  reference_id: number;
  filename: string;
  days_left: number | null;
  status: string;
  ref_image: string;
  hits: Hit[];
};

export type NotFoundItem = {
  reference_id: number;
  filename: string;
  days_left: number | null;
  status: string;
  ref_image: string;
  compared: boolean;
};

export type Matches = {
  within_days: number;
  confirmed: MatchGroup[];
  to_verify: MatchGroup[];
  later: MatchGroup[];
  not_found: NotFoundItem[];
  outside_window: number;
};
