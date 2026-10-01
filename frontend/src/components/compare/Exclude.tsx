import { useState } from "react";
import { Modal, useToast } from "../feedback";
import { Button, FieldLabel, Input } from "../ui";
import { errorMessage } from "../../lib/api";
import { useExclusionMutations } from "../../lib/queries";

/** Asks why, then excludes the image from every comparison. */
export function ExcludeModal(props: { open: boolean; onClose: () => void; siteImageId: string }) {
  const [reason, setReason] = useState("");
  const exclusions = useExclusionMutations();
  const toast = useToast();

  function exclude() {
    exclusions.add.mutate(
      { siteImageId: props.siteImageId, reason },
      {
        onSuccess: (data) => {
          toast.show({
            tone: "success",
            message: "Image exclue",
            description: data.matches_removed > 1
              ? `${data.matches_removed} correspondances retirées. Vous pouvez la réintégrer depuis les Réglages.`
              : "Elle ne sera plus proposée. Vous pouvez la réintégrer depuis les Réglages.",
          });
          props.onClose();
          setReason("");
        },
        onError: (error) => toast.show({ tone: "error", message: "L'image n'a pas été exclue", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Modal
      open={props.open}
      onClose={props.onClose}
      title="Exclure cette image"
      footer={
        <>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button variant="danger" loading={exclusions.add.isPending} onClick={exclude}>Exclure</Button>
        </>
      }
    >
      <p className="text-sm text-ink-soft">
        Cette image, et ses copies redimensionnées ou recompressées, ne seront plus jamais proposées, pour aucun visuel.
        Ses correspondances actuelles disparaissent. Vous pourrez la réintégrer depuis les Réglages.
      </p>
      <div className="mt-4">
        <FieldLabel htmlFor="exclusion-reason" hint="facultatif">Raison</FieldLabel>
        <Input id="exclusion-reason" data-autofocus placeholder="Logo du site, pictogramme…" value={reason} onChange={(event) => setReason(event.target.value)} />
      </div>
    </Modal>
  );
}
