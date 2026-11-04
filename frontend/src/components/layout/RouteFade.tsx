import { Outlet, useLocation } from "react-router";

/**
 * The page enters with a short fade instead of replacing the previous one at once. Keyed by the path: a change of the query (a
 * filter) keeps the page, another page or another brand starts afresh. Opacity only: a `transform` or a `filter` here would
 * become the container of every `position: fixed` inside (dialogs, the selection bar).
 */
export function RouteFade() {
  const { pathname } = useLocation();
  return (
    <div key={pathname} className="fade-in">
      <Outlet />
    </div>
  );
}
