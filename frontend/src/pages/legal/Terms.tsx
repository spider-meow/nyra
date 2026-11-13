import { CONTACT, LegalLayout, List, Section } from "./Layout";

export function Terms() {
  return (
    <LegalLayout title="Conditions d'utilisation">
      <Section title="Accès">
        <p>Nyra est réservé aux organisations et aux personnes qu'AXEL PROJECT a invitées. Il n'y a pas d'inscription ouverte. L'administrateur d'une organisation invite ses membres et reste responsable de leurs accès.</p>
        <p>Chacun garde son mot de passe confidentiel et prévient <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a> en cas de doute sur la sécurité de son compte.</p>
      </Section>
      <Section title="Le service">
        <p>Nyra lit les pages publiques des sites que l'organisation déclare et les rapproche de sa bibliothèque de visuels. Les résultats sont une aide à la décision : ils sont relus par des personnes de l'organisation, qui décident quoi retirer. Nyra ne garantit pas qu'un visuel en ligne sera toujours trouvé.</p>
      </Section>
      <Section title="Engagements de l'organisation">
        <List items={[
          "Ne faire lire que des sites dont elle a la charge ou dont elle est autorisée à demander la lecture.",
          "Ne déposer que des visuels et des dates de droits dont elle a la maîtrise ; elle est seule responsable de l'exactitude des droits qu'elle déclare.",
          "Ne pas tenter de contourner les limites, d'accéder aux données d'une autre organisation ou de perturber le service.",
        ]} />
        <p>Le robot de lecture se présente sous le nom NyraBot et respecte le fichier robots.txt des sites (<a className="underline" href="/robot">détails</a>).</p>
      </Section>
      <Section title="Responsabilité">
        <p>AXEL PROJECT met en œuvre les moyens raisonnables pour que le service soit disponible et sûr, sans garantir l'absence d'interruption. Les décisions prises à partir des résultats (retirer ou conserver un visuel) restent celles de l'organisation. La responsabilité d'AXEL PROJECT ne peut être engagée pour un usage contraire à ces conditions.</p>
      </Section>
      <Section title="Données personnelles et suspension">
        <p>Le traitement des données est décrit dans la <a className="underline" href="/privacy">politique de confidentialité</a>. AXEL PROJECT peut suspendre un accès en cas de manquement à ces conditions.</p>
      </Section>
      <Section title="Droit applicable">
        <p>Ces conditions sont soumises au droit français. En cas de litige, les tribunaux compétents sont ceux du ressort du siège d'AXEL PROJECT, après une tentative de règlement amiable.</p>
      </Section>
    </LegalLayout>
  );
}
