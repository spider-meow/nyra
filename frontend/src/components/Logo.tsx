const DARK = "#936225";
const LIGHT = "#FDCBAC";

/** The Nyra mark: two rounded triangles split by a diagonal gap. */
export function Logo(props: { size?: number; className?: string }) {
  const size = props.size ?? 24;
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" className={props.className} aria-hidden="true">
      <path
        d="M 23.99,13.59 L 73.18,11.58 Q 83.17,11.17 76.10,18.24 L 18.24,76.10 Q 11.17,83.17 11.58,73.18 L 13.59,23.99 Q 14,14 23.99,13.59 Z"
        fill={DARK}
      />
      <path
        d="M 86.41,76.01 L 88.42,26.82 Q 88.83,16.83 81.76,23.90 L 23.90,81.76 Q 16.83,88.83 26.82,88.42 L 76.01,86.41 Q 86,86 86.41,76.01 Z"
        fill={LIGHT}
      />
    </svg>
  );
}
