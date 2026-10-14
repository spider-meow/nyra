import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { BrandMark } from "../../components/BrandMark";
import { useToast } from "../../components/feedback";
import { SettingsBlock, SettingsRow } from "../../components/SettingsBlock";
import { Button, cx, Input } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { brandLink, useOrg } from "../../lib/org";
import { useBrandMutations } from "../../lib/queries";

/** The logo: a picture of any size is shrunk by the server. Click the tile or drop a file on it. */
function LogoRow() {
  const { admin, brand } = useOrg();
  const { setLogo, removeLogo } = useBrandMutations();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function send(picked: File | undefined) {
    if (!picked) return;
    setLogo.mutate(
      { id: brand.id, file: picked },
      {
        onSuccess: () => toast.show({ tone: "success", message: "Logo enregistré", duration: 3000 }),
        onError: (error) => toast.show({ tone: "error", message: "Le logo n'a pas été enregistré", description: errorMessage(error) }),
      },
    );
  }

  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    send(event.target.files?.[0]);
    event.target.value = "";
  };
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    send(event.dataTransfer.files[0]);
  };

  return (
    <SettingsRow label="Logo" hint="Affiché en haut de la barre latérale. PNG, JPEG ou WebP, de préférence carré.">
      <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden aria-label="Choisir un logo" onChange={pick} />
      {admin ? (
        <>
          <button
            type="button"
            aria-label={brand.logo ? "Changer le logo" : "Ajouter un logo"}
            onClick={() => file.current?.click()}
            onDragOver={(event) => (event.preventDefault(), setOver(true))}
            onDragLeave={() => setOver(false)}
            onDrop={drop}
            className={cx("rounded-xl border border-dashed p-1 transition-colors", over ? "border-ink bg-sunk" : "border-line-strong hover:border-ink hover:bg-sunk")}
          >
            <BrandMark brand={brand} size={48} />
          </button>
          <Button size="sm" loading={setLogo.isPending} onClick={() => file.current?.click()}>{brand.logo ? "Changer" : "Ajouter"}</Button>
          {brand.logo ? (
            <Button
              size="sm"
              variant="ghost"
              loading={removeLogo.isPending}
              onClick={() => removeLogo.mutate(brand.id, { onError: (error) => toast.show({ tone: "error", message: "Le logo n'a pas été retiré", description: errorMessage(error) }) })}
            >
              Retirer
            </Button>
          ) : null}
        </>
      ) : (
        <BrandMark brand={brand} size={48} />
      )}
    </SettingsRow>
  );
}

/** The name, which also gives the brand its address: saved with a button, since the address changes with it. */
function NameRow() {
  const { admin, org, brand } = useOrg();
  const { rename } = useBrandMutations();
  const navigate = useNavigate();
  const toast = useToast();
  const [name, setName] = useState(brand.name);

  function save(event: FormEvent) {
    event.preventDefault();
    rename.mutate(
      { id: brand.id, name },
      {
        onSuccess: ({ brand: renamed }) => {
          toast.show({ tone: "success", message: `Marque renommée : ${renamed.name}`, duration: 3000 });
          // The address of the brand on screen follows its new slug.
          navigate(`${brandLink(org, renamed.slug, "reglages")}?section=general`, { replace: true });
        },
        onError: (error) => toast.show({ tone: "error", message: "La marque n'a pas été renommée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <SettingsRow label="Nom" hint="Le nom change aussi l'adresse de la marque.">
      {admin ? (
        <form className="flex items-center gap-2" onSubmit={save}>
          <Input aria-label="Nom de la marque" required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} className="w-56" />
          <Button type="submit" size="sm" variant="primary" loading={rename.isPending} disabled={!name.trim() || name.trim() === brand.name}>Enregistrer</Button>
        </form>
      ) : (
        <span className="text-muted">{brand.name}</span>
      )}
    </SettingsRow>
  );
}

/** What identifies the brand: its logo, its name and the address it is reached at. */
export function Profile() {
  const { admin, platformAdmin, org, brand } = useOrg();
  return (
    <SettingsBlock
      title="Identité"
      description="Chaque marque a sa bibliothèque et ses adresses (un site par marché, par exemple). Ses visuels ne sont comparés qu'aux images de ses propres sites."
    >
      <LogoRow />
      <NameRow />
      <SettingsRow label="Adresse dans Nyra">
        <code className="text-[13px] text-muted">/o/{org.slug}/m/{brand.slug}</code>
      </SettingsRow>
      {admin && !platformAdmin ? <p className="border-t border-line px-5 py-3 text-[13px] text-muted">Pour ajouter une marque, contactez l'équipe Nyra.</p> : null}
    </SettingsBlock>
  );
}
