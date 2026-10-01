/** The Nyra mark: a red panda asleep on a branch. `size` is its height in pixels, the width follows. */
export function Logo(props: { size?: number; className?: string }) {
  const height = props.size ?? 32;
  return <img src="/nyra-logo.svg" alt="" width={Math.round((height * 843) / 433)} height={height} className={props.className} />;
}
