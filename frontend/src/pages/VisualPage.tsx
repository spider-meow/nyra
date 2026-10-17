import { useLocation, useNavigate, useParams } from "react-router";
import { useOrg } from "../lib/org";
import { Visual } from "./visual/Visual";

/** `/visuals/<id>`: the page of one visual. Back goes where the person came from (the visuals, or the triage); a link opened from elsewhere has no such place, so it goes to the visuals. */
export function VisualPage() {
  const { referenceId = "" } = useParams();
  const { link } = useOrg();
  const navigate = useNavigate();
  const location = useLocation();
  const back = () => (location.key === "default" ? navigate(link("visuals"), { replace: true }) : navigate(-1));
  return <Visual referenceId={referenceId} onBack={back} />;
}
