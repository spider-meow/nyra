/** Stroke icons, drawn on a 24px grid and sized by the caller. */
const paths = {
  dashboard: "M4 13h6V4H4zM14 20h6v-9h-6zM14 4h6v4h-6zM4 20h6v-3H4z",
  review: "M4 7h16M4 12h10M4 17h6M16 16l2 2 4-4",
  library: "M3 6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM21 16l-5-5-9 9M9 8.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  scan: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
  report: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4",
  plus: "M12 5v14M5 12h14",
  upload: "M12 16V4M7 9l5-5 5 5M5 20h14",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  search: "M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM20 20l-4-4",
  check: "M5 12l5 5 9-10",
  close: "M6 6l12 12M18 6L6 18",
  arrow: "M5 12h14M13 6l6 6-6 6",
  chevrons: "M8 9l4-4 4 4M8 15l4 4 4-4",
  alert: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v6M12 16.5v.5",
  menu: "M4 7h16M4 12h16M4 17h16",
} as const;

export type IconName = keyof typeof paths;

export function Icon(props: { name: IconName; size?: number; className?: string; strokeWidth?: number }) {
  const size = props.size ?? 18;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={props.strokeWidth ?? 1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={props.className}
    >
      <path d={paths[props.name]} />
    </svg>
  );
}
