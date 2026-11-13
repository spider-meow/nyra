import { CONTACT, LegalLayout, List, Section } from "./Layout";

export function Privacy() {
  return (
    <LegalLayout title="Politique de confidentialité">
      <Section title="Qui est responsable de quoi">
        <p>La marque (l'organisation qui utilise Nyra) est responsable de traitement de ses visuels de référence, des pages de ses sites qu'elle fait lire et des décisions de ses équipes. AXEL PROJECT, éditeur de Nyra, agit comme sous-traitant : il traite ces données pour son compte et sur ses instructions.</p>
        <p>Pour les comptes des utilisateurs (adresse e-mail, rôle), AXEL PROJECT et l'organisation qui a invité la personne les utilisent pour donner l'accès au service.</p>
      </Section>
      <Section title="Données traitées">
        <List items={[
          "Compte : adresse e-mail, mot de passe (stocké chiffré par le service d'authentification), rôle dans l'organisation.",
          "Bibliothèque : les images de référence de la marque et leurs dates de droits.",
          "Lectures : les adresses des pages lues sur les sites de la marque et une copie des images publiques qu'on y trouve.",
          "Revue : les décisions des équipes (à retirer, faux positif, retiré) et le journal de ce que chaque personne a fait.",
        ]} />
      </Section>
      <Section title="Finalités">
        <p>Retrouver en ligne les visuels dont les droits expirent, classer les résultats par urgence, permettre leur relecture, envoyer les invitations, les alertes d'expiration et le résumé mensuel, assurer la sécurité et le bon fonctionnement du service. Rien n'est utilisé pour de la publicité, du profilage ou de la revente.</p>
        <p>La comparaison des images se fait par calculs internes (empreintes visuelles et modèle CLIP exécuté sur nos propres serveurs). Aucune image ni aucun texte n'est envoyé à un service d'intelligence artificielle tiers.</p>
      </Section>
      <Section title="Sous-traitants">
        <List items={[
          "Supabase : base de données, authentification et stockage des fichiers (originaux, vignettes, empreintes).",
          "DigitalOcean : serveur qui fait tourner l'application et les lectures de pages.",
          "Resend : envoi des e-mails (invitations, alertes d'expiration, résumés mensuels).",
          "Sentry : suivi des erreurs techniques, sans adresse e-mail, en-têtes, cookies, contenus de requêtes ni paramètres d'adresse.",
        ]} />
        <p>Les sites de la marque ne sont pas des sous-traitants : leur lecture est la finalité du service.</p>
      </Section>
      <Section title="Durées de conservation">
        <List items={[
          "Compte : tant qu'il est actif. Il est supprimé à la demande de l'organisation ou de la personne.",
          "Bibliothèque de référence : tant que l'organisation la garde ; l'organisation peut supprimer ses visuels.",
          "Images lues sur les sites : conservées même quand elles disparaissent du site, car c'est une preuve utile à la marque. Seule la copie de travail d'une image qui n'a pas été revue depuis deux mois, qui ne correspond à aucun visuel de la bibliothèque et qui n'a pas été jugée est supprimée ; son adresse, ses empreintes et sa vignette restent.",
          "Décisions de revue et journal des actions : tant que l'organisation existe.",
        ]} />
      </Section>
      <Section title="Cookies et stockage du navigateur">
        <p>Nyra ne dépose aucun traceur de mesure d'audience ni de publicité, et n'affiche donc pas de bandeau de consentement.</p>
        <p>Le navigateur garde uniquement ce qui est nécessaire au service : la session de connexion (Supabase Auth), le choix « Rester connecté » et des copies temporaires de la bibliothèque et du tableau de bord pour que ces pages s'ouvrent vite. Ces données restent sur votre appareil et sont effacées à la déconnexion ou par vos réglages de navigateur.</p>
      </Section>
      <Section title="Vos droits">
        <p>Vous pouvez demander l'accès, la rectification, l'effacement, la limitation ou la portabilité de vos données, et vous opposer à un traitement, en écrivant à <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>. Pour les données d'une marque, nous transmettons la demande à la marque, responsable de traitement. Vous pouvez aussi saisir la CNIL (cnil.fr).</p>
      </Section>
    </LegalLayout>
  );
}
