# Printy — cahier des charges

Add-on Home Assistant pour gérer une petite production d'impressions 3D (lampes) :
bibliothèque de fichiers prêts à imprimer, imprimantes, planning de la semaine,
pilotage de l'alimentation et confirmation « plateau vide » par notification.

## Principe directeur

Une interface **sobre et soignée**. Des réglages par défaut sensés, peu d'options
visibles ; les réglages avancés sont repliés. Chaque écran a une action principale
évidente. L'UX passe avant l'exhaustivité.

## Contexte

| Sujet | Décision |
|---|---|
| Usage | Vente régulière de lampes |
| Hébergement | Add-on Home Assistant OS |
| Stack | Python 3.12 (FastAPI, SQLAlchemy, SQLite) + Vue 3 (Vite, TypeScript) |
| Accès | Ingress (barre latérale HA) + port direct sur le réseau local (envoi depuis le trancheur) |
| Trancheur | Orca Slicer (Creality Print avant, Bambu Studio si une Bambu arrive) |
| Imprimantes | Creality K2 (une bobine, CFS envisagé), Bambu Lab envisagée |
| Prises | Connectées avec mesure de consommation, pilotées par HA |
| Caméras | Caméra intégrée de l'imprimante ou entité `camera.*` de HA, au choix par imprimante |
| Filament | Spoolman (V2) |
| Électricité | Contrat heures pleines / heures creuses |

## Modèle de données

- **Modèle** : un objet à imprimer (ex. « Vague — abat-jour »). Nom, description, tags,
  sources facultatives (STL/3MF non tranchés).
- **Version tranchée** : un fichier prêt à imprimer (`.gcode`, `.gcode.3mf`) rattaché
  à un modèle. Métadonnées extraites : durée, filament (g, m), matière, couleur,
  hauteur de couche, buse, imprimante cible, miniature. Un modèle peut avoir une
  version par imprimante / profil.
- **Imprimante** : nom, pilote (`creality_k2`, `moonraker`, `bambu`…), adresse,
  prise HA (`switch.*`), caméra (intégrée ou `camera.*`), délai d'allumage,
  seuil de refroidissement, état « plateau libéré ».
- **Impression planifiée (job)** : version tranchée + imprimante + heure de début,
  confirmation requise ou non, état, horodatages, événements.
- **Produit / kit** (V3) : une lampe = plusieurs modèles × quantités, variantes de couleur.
- **Bobine** (V2) : référence Spoolman.

## Cycle d'une impression planifiée

```
planifiée
  └─(début − délai d'allumage)→ allumage : prise ON, attente que l'imprimante réponde
        └→ en attente de confirmation : notification avec photo du plateau
              [Lancer] [Reporter 1 h] [Annuler]
              (sautée si le plateau a été déclaré libéré après la dernière impression,
               ou si la confirmation est désactivée pour ce job)
        └─(heure de début, confirmée)→ envoi du fichier → impression
              └→ terminée → notification « Impression terminée » [Plateau vidé]
                    └→ refroidissement : prise OFF quand buse et plateau < seuil,
                       sauf si une autre impression démarre dans les 30 min
```

- Sans réponse à la confirmation après 15 min (réglable) : le job passe « à revoir »,
  la prise est coupée si rien d'autre ne tourne.
- Confirmation activée par défaut ; la désactiver affiche un avertissement.
- « Plateau vidé » à la fin d'une impression vaut pré-confirmation pour la suivante.
- On ne coupe jamais une imprimante chaude ou en cours d'impression.

## Import des fichiers

Trois voies, chacune activable dans les réglages :
1. **Envoi direct depuis Orca** : l'add-on expose une API compatible OctoPrint
   (`/api/version`, `/api/files/local`) protégée par une clé. Le fichier arrive dans
   la bibliothèque, pas sur l'imprimante.
2. **Glisser-déposer** dans l'interface.
3. **Dossier surveillé** : `/share/printy/inbox` (accessible en Samba).

Stockage : `/share/printy/library`. Base de données : `/data/printy.db`.

## Pilotes d'imprimante

Interface commune `PrinterDriver` : `status()`, `upload()`, `start()`, `pause()`,
`resume()`, `cancel()`, `snapshot()`. Ajouter une marque = ajouter un pilote.

- `creality_k2` : WebSocket Creality OS (port 9999) pour l'état et les commandes.
- `moonraker` : API Moonraker (Klipper rooté, autres imprimantes Klipper).
- `bambu` (V4) : MQTT local + FTPS, mode LAN.
- `demo` : imprimante simulée pour le développement.

## À tester (en attente)

- [ ] **Lancement réel d'une impression par Printy sur la K2** (envoi du fichier via
  Moonraker :7125 puis démarrage via le WebSocket :9999). Prévu par l'utilisateur après
  son impression en cours (noté le 23/11/2026). Faire un cube de calibration planifié
  à +15 min, confirmation activée, en restant à côté. Si ça échoue : message affiché
  dans Printy + onglet Journal de l'add-on, et lancer `scripts/k2_probe.py <ip>`.
- [ ] Réception des événements de bouton de notification (Lancer / Plateau vidé) dans
  l'add-on réel.

## Feuille de route

- **V1 — cœur** ✅ : squelette add-on (Ingress, base, réglages), bibliothèque (glisser-déposer,
  miniatures, métadonnées, regroupement par modèle, envoi par morceaux pour les gros
  fichiers), imprimantes (pilotes K2, Moonraker, démo), planning semaine (glisser pour
  déplacer, heures creuses affichées) avec cycle complet (allumage → confirmation photo →
  impression → refroidissement → coupure), envoi direct depuis Orca (API OctoPrint),
  dossier surveillé.
- **V2 — filament** ✅ : Spoolman (bobine chargée par imprimante, bobine associée à chaque
  impression selon la couleur et la matière, consommation déduite après l'impression),
  rappel de changement de bobine (planning, notification, confirmation forcée), alerte
  filament insuffisant, aperçu 3D des STL/3MF, thème sombre.
- **V3 — production** ✅ : suivi des stocks par produit (pièces en stock par couleur, ajout
  automatique à la fin d'une impression réussie, retrait à la vente), kWh moyens par pièce
  (pas de gestion de coût : décision de l'utilisateur), entités HA (MQTT discovery via
  Mosquitto, sinon états REST), vue des bobines et création de bobines dans Spoolman.
- **V4 — extensions** : produits/kits (une lampe = plusieurs pièces, variantes de
  couleur), pilote Bambu, CFS, file d'attente, commandes clients.
