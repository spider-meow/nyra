/** The Nyra mark. */
export function Logo(props: { size?: number; className?: string }) {
  const size = props.size ?? 24;
  return <img src="/nyra-logo.png" alt="" width={size} height={size} className={props.className} />;
}
