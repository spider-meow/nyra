import { useState } from "react";
import { Modal } from "../../components/feedback";
import { Section } from "../../components/insights";
import { Button, EmptyState, PageHeader, Segmented, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useEmailPreview, useEmailTest, useStaffEmails } from "../../lib/staff";
import type { MailEntry } from "../../types";

type View = "html" | "text";

/** The e-mail as it will look: the subject, then the HTML in a frame with no script and no active link, or the text version. */
function PreviewDialog(props: { entry: MailEntry; onClose: () => void }) {
  const preview = useEmailPreview(props.entry.key);
  const [view, setView] = useState<View>("html");
  return (
    <Modal open wide title={`Aperçu : ${props.entry.label}`} onClose={props.onClose}>
      {preview.isLoading ? <Skeleton className="h-96" /> : null}
      {preview.error ? (
        <div role="alert" className="grid gap-3 text-sm">
          <p>{errorMessage(preview.error)}</p>
          <div><Button size="sm" onClick={() => void preview.refetch()}>Réessayer</Button></div>
        </div>
      ) : null}
      {preview.data ? (
        <div className="grid gap-3">
          <p className="text-sm"><span className="text-muted">Objet : </span><span className="font-medium">{preview.data.subject}</span></p>
          <div><Segmented label="Version" value={view} onChange={setView} options={[{ value: "html", label: "HTML" }, { value: "text", label: "Texte" }]} /></div>
          {view === "html" ? (
            <iframe title={`Aperçu HTML : ${props.entry.label}`} sandbox="" srcDoc={preview.data.html} className="h-[28rem] w-full rounded-md border border-line bg-white" />
          ) : (
            <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-md border border-line bg-sunk p-3 text-[13px]">{preview.data.text}</pre>
          )}
        </div>
      ) : null}
    </Modal>
  );
}

/** "M'envoyer un test", and what came of it, under the button. */
function TestButton(props: { entry: MailEntry; configured: boolean }) {
  const test = useEmailTest();
  const reason = props.entry.can_send_test ? (props.configured ? "" : "L'envoi n'est pas configuré sur le serveur.") : "Cet e-mail est envoyé par Supabase : aperçu seulement.";
  return (
    <div className="grid justify-items-end gap-1.5">
      <span title={reason || undefined}>
        <Button size="sm" disabled={reason !== ""} loading={test.isPending} onClick={() => test.mutate(props.entry.key)}>M'envoyer un test</Button>
      </span>
      {test.data ? <p role="status" className="max-w-72 text-right text-[13px] text-muted">Test envoyé à votre adresse ({test.data.to}).</p> : null}
      {test.error ? <p role="alert" className="max-w-72 text-right text-[13px] text-expired">{errorMessage(test.error)}</p> : null}
    </div>
  );
}

function MailRow(props: { entry: MailEntry; configured: boolean; onPreview: () => void }) {
  const { entry } = props;
  return (
    <li className="flex flex-wrap items-start gap-3 py-3 text-sm">
      <div className="min-w-56 flex-1">
        <p className="font-medium">{entry.label}</p>
        <p className="text-muted">{entry.description}</p>
        <p className="mt-0.5 text-xs text-muted">{entry.sender === "nyra" ? "Envoyé par Nyra" : "Envoyé par Supabase (aperçu seulement)"}</p>
      </div>
      <Button size="sm" onClick={props.onPreview}>Aperçu</Button>
      <TestButton entry={entry} configured={props.configured} />
    </li>
  );
}

/** /back-office/emails — every e-mail of the product: see it, and send yourself a test. */
export function Emails() {
  const list = useStaffEmails();
  const [open, setOpen] = useState<MailEntry | null>(null);
  return (
    <>
      <PageHeader title="E-mails" />
      <Section title="E-mails du produit" description={list.data ? (list.data.configured ? `Envoi : configuré (expéditeur ${list.data.sender})` : "Envoi non configuré : ajoutez RESEND_API_KEY et NYRA_MAIL_FROM au serveur") : undefined}>
        {list.isLoading ? <Skeleton className="h-40" /> : null}
        {list.error ? (
          <div role="alert">
            <EmptyState title="Impossible de charger la liste" body={errorMessage(list.error)} action={<Button size="sm" onClick={() => void list.refetch()}>Réessayer</Button>} />
          </div>
        ) : null}
        {list.data ? (
          <ul className="divide-y divide-line">
            {list.data.emails.map((entry) => <MailRow key={entry.key} entry={entry} configured={list.data.configured} onPreview={() => setOpen(entry)} />)}
          </ul>
        ) : null}
      </Section>
      {open ? <PreviewDialog entry={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}
