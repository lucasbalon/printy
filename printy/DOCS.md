# Printy

Bibliothèque de fichiers prêts à imprimer, suivi des imprimantes et planning de la
semaine. Printy allume l'imprimante avant l'heure prévue, demande une photo du
plateau sur ton téléphone, lance l'impression, puis coupe la prise une fois
l'imprimante refroidie.

## Premiers pas

1. Ouvre **Printy** dans la barre latérale de Home Assistant.
2. **Imprimantes → Ajouter une imprimante** : nom, modèle, adresse IP, prise
   connectée et caméra.
3. **Réglages → Notifications** : coche le ou les téléphones (application
   compagnon Home Assistant) qui recevront la demande de confirmation.
4. **Bibliothèque** : glisse-dépose tes fichiers tranchés (`.gcode`,
   `.gcode.3mf`). Miniature, durée, filament et couleur sont lus automatiquement.
5. **Planning** : clique sur un créneau ou sur **Planifier**.

## Le cycle d'une impression

| Moment | Ce que fait Printy |
|---|---|
| Début − délai d'allumage (10 min par défaut, réglé par imprimante) | Allume la prise, attend que l'imprimante réponde |
| Début − délai de confirmation (10 min par défaut, dans les réglages) | Envoie une notification avec photo : **Lancer**, **Reporter** (1 h par défaut), **Annuler** |
| Heure prévue, confirmée | Envoie le fichier et lance l'impression |
| Fin | Notification « Impression terminée » avec le bouton **Plateau vidé** |
| Refroidissement | Coupe la prise quand buse et plateau sont sous 45 °C, sauf si une autre impression démarre dans les 30 min |

- La confirmation peut partir avant l'allumage (par exemple la veille d'une impression
  de nuit) : la photo n'est alors possible qu'avec une caméra Home Assistant.
- Sans réponse, la demande est renvoyée une fois à l'heure prévue (au plus tôt à
  mi-délai), au cas où le téléphone était hors ligne.
- Sans réponse 15 min après l'heure prévue, l'impression passe **à revoir**, n'est pas
  lancée et la prise est coupée une fois l'imprimante froide. Une notification le
  signale, comme pour toute impression qui passe à revoir.
- Toucher **Plateau vidé** à la fin d'une impression vaut confirmation pour la suivante.
- La confirmation peut être désactivée par impression ; un avertissement le rappelle.
- Printy ne coupe la prise qu'après une impression qu'il a lui-même lancée,
  jamais après une impression démarrée depuis l'écran ou le trancheur.
- Avant de couper, il relit l'état de la prise et interroge l'imprimante à
  l'instant : si elle ne répond pas, est chaude ou imprime, la prise reste allumée.
  La carte de l'imprimante affiche la coupure prévue, avec **Laisser allumée**.
- **Pièce oubliée** (désactivable dans les réglages) : si une pièce terminée n'a pas
  été déclarée retirée 2 h avant la prochaine impression (réglable), Printy envoie un rappel.
- **Photo du plateau** : juste avant chaque impression confirmée, Printy garde une
  photo du plateau vide. Les photos suivantes lui sont comparées et la notification
  indique si le plateau semble vide ou occupé. Ce n'est qu'une indication : la
  décision reste la tienne.

## Réglages chiffrés

Toutes les valeurs que Printy utilise sont visibles et modifiables dans **Réglages** :
délais de confirmation, de rappel et de report à côté de « Avant chaque impression »,
marge et réserve de filament et réglages de fin de bobine dans « Spoolman », horizon
des heures creuses dans « Heures creuses », et dans **Réglages avancés** les délais
techniques (démarrage de l'imprimante, chauffe et calibrage tolérés, envoi du fichier,
imprimante muette). Les valeurs par défaut conviennent à un K2.

## Notifications sur iPhone

- Les boutons (**Lancer**, **Reporter**, **Annuler**) s'affichent avec un appui long
  sur la notification : c'est le fonctionnement d'iOS. Un simple appui ouvre Printy,
  qui propose les mêmes choix.
- La demande de confirmation est « urgente » : elle passe même en mode Concentration.
- La photo est publiée dans Home Assistant comme une caméra (`camera.printy_…_photo`,
  via l'add-on Mosquitto), lisible par l'application où que tu sois. Sans photo
  disponible, la notification part sans image plutôt qu'avec une image en erreur.

## Planifier

- **Commencer à** ou **Prête pour** : donne l'heure de début, ou l'heure à laquelle
  la pièce doit être prête ; Printy calcule l'autre à partir de la durée.
- **Heures creuses** : le bouton place l'impression dans le premier créneau libre
  qui en profite le plus (dans les 3 jours, réglable).
- Le changement de bobine et le filament insuffisant sont signalés dès la
  planification, en tenant compte des impressions déjà prévues avant. Le filament
  insuffisant ne bloque pas.
- **Supprimer** une impression pas encore lancée l'efface sans trace (aussi depuis le
  bouton « Annuler » de la notification) ; si Printy avait allumé l'imprimante pour
  elle, la prise est recoupée une fois l'imprimante froide. Une impression envoyée à
  l'imprimante s'annule ou s'arrête, et reste dans l'historique.

## Sauvegarde

Printy est inclus dans les sauvegardes de Home Assistant, sans s'arrêter : une copie
cohérente de la base est faite juste avant. **Réglages → Sauvegarde → Exporter la base
de données** télécharge cette base. Les fichiers de la bibliothèque sont dans
`share/printy` : coche « Share » dans la sauvegarde pour les inclure.

## Impressions en cours

Le planning affiche en haut chaque impression en cours, avec sa progression, le temps
restant et l'heure de fin donnés par l'imprimante, y compris une impression lancée
depuis Orca ou l'écran de l'imprimante (« lancée hors Printy »). Elle apparaît aussi
dans la grille en pointillés, de son début à sa fin prévue.

- Planifier une impression sur ce créneau est refusé avec l'heure de fin prévue.
- Une impression planifiée qui démarrerait avant la fin porte une horloge : elle
  attendra que l'imprimante soit libre.

## Importer des fichiers

- **Glisser-déposer** n'importe où dans l'interface.
- **Depuis Orca Slicer** : dans Orca, ajoute une imprimante physique de type
  *OctoPrint* avec l'adresse `http://<ip-de-home-assistant>:5300` et la clé API
  affichée dans **Réglages → Import des fichiers**. Le bouton d'envoi d'Orca dépose
  alors le fichier dans la bibliothèque (pas sur l'imprimante).
- **Dossier surveillé** : copie les fichiers dans `share/printy/inbox` (partage
  Samba). Les fichiers non pris en charge vont dans `share/printy/inbox/refusés`.

Formats : fichiers tranchés `.gcode` et `.gcode.3mf` ; sources `.stl`, `.3mf`, `.obj`,
`.step` et `.blend` (les STL et 3MF ont un aperçu 3D).

Les fichiers d'un même objet sont regroupés en un **modèle** d'après leur nom
(`lampe_vague_K2_PLA.gcode` et `lampe_vague_X1C_PLA.gcode` → « Lampe vague »).

## Bobines (Spoolman)

Dans **Réglages → Spoolman**, indique l'adresse de Spoolman (par exemple
`http://homeassistant.local:7912` avec l'add-on Spoolman) puis **Tester et enregistrer**.

- **Bobine chargée** : sur la carte de chaque imprimante, choisis la bobine en place.
- **À la planification**, Printy propose la bobine dont la couleur et la matière
  correspondent au fichier ; tu peux en choisir une autre ou ne pas suivre la bobine.
- **Changement de bobine** : si une impression demande une autre bobine que celle
  chargée, le bloc du planning l'indique (« Mettre … »). Les rappels :
  - **au lancement** (toujours) : si l'imprimante (Spoolman via Moonraker) a encore
    une autre bobine, Printy demande de confirmer (« Bobine changée, lancer ») et
    attend, même si la confirmation est désactivée ou l'impression lancée tout de suite ;
  - **à la fin de l'impression précédente** (réglable) : dans la notification
    « Impression terminée », avec un bouton « Bobine changée » ;
  - **chaque matin** (réglable, 8 h par défaut) : un résumé des bobines à préparer
    pour les 24 h suivantes, envoyé seulement s'il y a quelque chose à faire.

  « Bobine changée » (notification ou bouton sur l'impression) met à jour la bobine
  chargée : l'impression partira ensuite sans redemander le changement.
- **Filament insuffisant** : signalé dans le planning et sur l'impression quand la
  bobine n'aura plus assez de filament, en tenant compte des impressions planifiées
  avant.
- **Consommation** : à la fin de l'impression, les grammes estimés par le trancheur
  sont déduits dans Spoolman (au prorata si l'impression est interrompue). Si Spoolman
  ne répond pas, Printy réessaie plus tard.
- **Imprimante reliée à Spoolman par Klipper** (module `[spoolman]` de Moonraker) :
  Printy le détecte et travaille avec. La bobine active de Klipper et la bobine chargée
  dans Printy restent synchronisées dans les deux sens (un changement fait dans Fluidd
  ou sur l'écran apparaît dans Printy, et inversement) ; Printy active la bobine de
  l'impression juste avant de la lancer. La consommation est alors déduite par Klipper,
  au gramme près, et Printy ne déduit rien lui-même pour éviter le double comptage.

## Stock

Chaque modèle de la bibliothèque est un **produit**.

- Les pièces arrivent dans le **Stock** toutes seules à la fin d'une impression réussie,
  rangées par couleur de bobine (ou couleur du fichier). Indique le nombre de pièces par
  impression dans la table des fichiers tranchés du modèle (par exemple 4 diffuseurs sur
  un plateau).
- Boutons **Vendue**, **+** et **−** pour les ventes et corrections, avec annulation ;
  **Impression ratée** sur une impression terminée retire ses pièces du stock.
- Un seuil d'alerte par produit signale le stock bas.
- **Électricité par pièce** : la prise mesure la consommation pendant chaque impression ;
  la fiche du modèle affiche la moyenne en kWh par pièce (5 dernières impressions).

## Entités Home Assistant

Avec l'add-on **Mosquitto** (MQTT), Printy crée de vraies entités, regroupées dans un
appareil « Printy <imprimante> » et un appareil « Printy » :

| Entité | Valeur |
|---|---|
| `sensor.printy_<imprimante>_etat` | État de l'imprimante |
| `sensor.printy_<imprimante>_progression` | Progression en % |
| `sensor.printy_<imprimante>_temps_restant` | Minutes restantes |
| `sensor.printy_<imprimante>_fin_impression` | Heure de fin prévue |
| `sensor.printy_<imprimante>_prochaine_impression` | Prochaine impression planifiée (modèle en attribut) |
| `sensor.printy_a_traiter` | Impressions qui attendent une action |
| `sensor.printy_stock` | Pièces en stock (détail par produit en attribut) |
| `sensor.printy_stock_bas` | Produits sous leur seuil d'alerte |

Sans Mosquitto, les mêmes capteurs sont publiés directement : ils n'apparaissent alors
que dans **Outils de développement → États** (pas d'appareil, pas de réglages). L'état
de la publication et la dernière erreur éventuelle sont affichés dans **Réglages → Stock
et Home Assistant**.

## Bobines

L'onglet **Bobines** montre ce qu'il y a dans Spoolman, regroupé par filament : quantité
restante de chaque bobine, emplacement, bobine montée sur l'imprimante, filament déjà
prévu par les impressions planifiées. **Ouvrir Spoolman** l'ouvre à part.

- **Nouvelle bobine** : choisis un filament connu, ou décris-en un nouveau (couleur,
  nom de la couleur, matière, marque ; poids et diamètre ont des valeurs par défaut).
  Plusieurs bobines d'un coup, un emplacement, et « mettre tout de suite sur » une
  imprimante.
- **Une bobine de plus** sur un filament : la même, en un clic.
- **Vide** : archive la bobine dans Spoolman.
- **Finir** (aussi « Finir cette bobine » sur la carte de l'imprimante) : une bobine ne
  se change pas en cours d'impression, sa fin est donc perdue si les dernières
  impressions ne tombent pas juste. Choisis une étiquette (par exemple « abat-jour ») :
  Printy cherche les combinaisons d'impressions de ces modèles qui laissent le moins de
  filament, en tenant compte de ce qui est déjà prévu sur la bobine, d'une marge par
  impression (5 % par défaut) et d'une réserve en fin de bobine (0 g par défaut), toutes
  deux réglables dans **Réglages → Spoolman** et appliquées aussi aux alertes de
  filament insuffisant. Deux propositions : **le moins de reste**, et **le plus utile
  pour le stock** (modèles sous leur seuil d'alerte, ou les moins en stock dans cette
  couleur). **Planifier ces impressions** les place l'une après l'autre, en heures
  creuses si possible. L'étiquette peut être retenue pour un type de bobine (matière
  et couleur, pas une bobine précise) : elle sera proposée d'office, sans rien
  empêcher d'autre. La liste se gère dans **Réglages → Spoolman**.

**Quelle bobine Printy choisit** pour une impression, parmi celles de la bonne couleur et
de la bonne matière : celle déjà montée sur l'imprimante si elle a assez de filament ;
sinon la bobine entamée qui a le moins de filament tout en suffisant (on finit les
bobines ouvertes avant d'en ouvrir une neuve) ; sinon la plus pleine, avec une alerte.
Tu peux toujours en choisir une autre à la planification. Les notifications désignent
la bobine précisément : « la bobine n° 12 Elegoo Blanc mat (PLA, 820 g, Étagère A) ».

## Aperçu 3D et apparence

- Les sources STL et 3MF d'un modèle s'affichent en 3D (bouton **Aperçu 3D** sur la
  fiche du modèle). Au-delà de 300 000 triangles, l'aperçu est simplifié pour rester
  fluide ; le bouton **Détaillée** affiche le modèle complet.
- Thème clair ou sombre : automatique selon l'appareil, ou forcé dans **Réglages →
  Apparence** (réglage propre à chaque appareil).

## Imprimantes prises en charge

| Modèle | État |
|---|---|
| Creality K2 / K1 (Creality OS d'origine) | État et commandes via le WebSocket local (port 9999). **À valider sur ta machine** : le protocole n'est pas documenté par Creality. L'envoi des fichiers passe par l'API Moonraker (port 7125). |
| Klipper (Moonraker) | API documentée : K2 rootée, Voron, etc. |
| Imprimante de démonstration | Simule une impression accélérée, pour essayer Printy sans matériel. |
| Bambu Lab | Prévu (MQTT local + FTPS, mode LAN). |

## Réseau et sécurité

- L'interface n'est servie que via l'Ingress de Home Assistant, qui gère
  l'authentification.
- Le port 5300 n'accepte que l'envoi de fichiers depuis le trancheur, protégé par
  la clé API. Tu peux le désactiver dans la configuration réseau de l'add-on si tu
  n'utilises pas l'envoi depuis Orca.

## Données

- Base de données : `/data/printy.db` (incluse dans les sauvegardes de l'add-on).
- Fichiers : `/share/printy/library`.
- Photos des notifications : `/config/www/printy` (servies par Home Assistant).
