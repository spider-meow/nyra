import { CONTACT, LegalLayout, List, Section } from "./Layout";

export function Mentions() {
  return (
    <LegalLayout title="Mentions légales">
      <Section title="Éditeur">
        <p>Le site <a className="underline" href="https://nyra.axelproject.fr">nyra.axelproject.fr</a> (Nyra) est édité par :</p>
        <List items={[
          "AXEL PROJECT, société par actions simplifiée au capital de 100 €",
          "Siège social : 5 rue des Iris, 92160 Antony",
          "SIREN 989 841 911, SIRET 989 841 911 00013, RCS Nanterre",
          "TVA intracommunautaire : FR46 989841911",
          <>Contact : <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a></>,
        ]} />
      </Section>
      <Section title="Directeur de la publication">
        <p>Arthur Reichard.</p>
      </Section>
      <Section title="Objet du service">
        <p>Nyra surveille les sites web d'une marque pour retrouver les visuels dont les droits ont expiré ou vont expirer. L'organisation fournit sa bibliothèque de visuels de référence avec leurs dates de droits ; Nyra lit les pages publiques de ses sites et signale les visuels trouvés, que des personnes de l'organisation relisent ensuite. L'accès se fait sur invitation.</p>
      </Section>
      <Section title="Hébergement">
        <List items={[
          <><strong>Application et traitements :</strong> DigitalOcean, LLC, 101 Avenue of the Americas, 10th Floor, New York, NY 10013, États-Unis.</>,
          <><strong>Base de données, comptes et fichiers :</strong> Supabase, Inc.</>,
        ]} />
      </Section>
      <Section title="Propriété intellectuelle">
        <p>L'interface, les textes et le code de Nyra appartiennent à AXEL PROJECT. Les visuels et les contenus déposés ou lus par une organisation restent la propriété de leurs titulaires.</p>
      </Section>
      <Section title="Données personnelles">
        <p>Voir la <a className="underline" href="/privacy">politique de confidentialité</a> et les <a className="underline" href="/terms">conditions d'utilisation</a>.</p>
      </Section>
    </LegalLayout>
  );
}
