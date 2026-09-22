# Bryton420

Transforme un GPX en fichier de navigation pour **Bryton Rider 420** : tu déposes le `.fit` dans `PlanTrip`, le compteur en tire lui-même ses fichiers de navigation (`.smy`, `.tinfo`, `.track`), et tu roules avec les instructions virage par virage.

Pas d'application Bryton, pas de Google, pas de Bluetooth : un câble USB suffit. Aucune dépendance Python.

## Deux modes

| | Hors ligne | En ligne |
| --- | --- | --- |
| **Pour** | un GPX qui contient déjà les instructions | une trace seule : Komoot, Strava, un GPX enregistré ou fait main |
| **Virages** | ceux du GPX | recalculés par BRouter sur ta trace |
| **Noms de rue** | s'il y en a dans le GPX | ajoutés par Valhalla |
| **Réseau** | rien n'est envoyé | ta trace part vers brouter.de et valhalla1.openstreetmap.de |

Par défaut l'outil choisit tout seul : **hors ligne si le GPX a des virages, en ligne sinon** (un GPX qui ne marque que le départ et l'arrivée passe donc en ligne). `--offline` et `--online` forcent l'un ou l'autre ; `--online` sert aussi à récupérer les noms de rue d'un export BRouter, qui n'en contient pas.

## Quel outil pour tracer ?

| Outil | Où | Export | Mode |
| --- | --- | --- | --- |
| [bikerouter.de](https://bikerouter.de) ou [brouter-web](https://brouter.de/brouter-web) | ordinateur, navigateur du téléphone | GPX avec `turnInstructionMode` = **osmand-style** (voir plus bas) | **hors ligne**, le meilleur choix : rien n'est envoyé |
| [cycle.travel](https://cycle.travel) | ordinateur | **GPX track**, sans cocher « Announce turns in advance » | en ligne |
| [OsmAnd](https://f-droid.org/packages/net.osmand.plus/) (F-Droid) | téléphone, hors connexion | Planifier un itinéraire → Enregistrer en GPX | en ligne |
| VisuGPX, Komoot, Strava… | partout | la trace GPX | en ligne |

Le « GPX route » de cycle.travel et le GPX natif d'OsmAnd contiennent des instructions, mais dans un format que l'outil ne lit pas encore : exporte la trace, le mode en ligne recalcule tout. [onroutemap.de](https://onroutemap.de) trouve des points d'intérêt (eau, ravitaillement, camping) le long d'une trace ; le Rider ne les affiche pas, garde-les à part.

## 1. Préparer le GPX

### Hors ligne : BRouter avec les instructions

[BRouter](https://github.com/abrensch/brouter) est libre, basé sur OpenStreetMap, sans compte ni pistage, avec d'excellents profils vélo. Interface web : [brouter.de/brouter-web](https://brouter.de/brouter-web) ou [bikerouter.de](https://bikerouter.de).

1. Place tes points de passage et choisis un profil vélo (`trekking` convient très bien).
2. **Active les instructions, sinon le GPX n'en contiendra aucune.** Dans l'onglet latéral **Profil**, mets `turnInstructionMode` (« Mode for the generated turn instructions ») sur **osmand-style**. Par défaut il vaut `auto-choose`, et l'export web n'écrit alors que la trace.
3. Exporte en **GPX**. La fenêtre d'export doit indiquer « Includes turn instructions ».

`locus-style` et `gpsies-style` fonctionnent aussi, tout comme les exports « directions » d'[openrouteservice.org](https://openrouteservice.org) et PlotARoute (export waypoints **et** trace), qui donnent en plus les noms de rue.

BRouter ne fournit pas de noms de rue, et signale beaucoup de carrefours : sur un export réel, 87 instructions en 16 km, dont 21 à moins de 20 m de la suivante. `turnInstructionCatchingRange` (onglet Profil, 40 m par défaut) regroupe les instructions plus proches que cette distance si le Rider en annonce trop.

### En ligne : n'importe quelle trace

Exporte le GPX de Komoot, Strava ou autre, et convertis-le directement. L'outil envoie la trace à deux services libres basés sur OpenStreetMap :

- **Valhalla** cale la trace sur la carte et donne le nom de chaque rue empruntée ;
- **BRouter** recalcule l'itinéraire avec un point de passage tous les quelques mètres de ta trace, ce qui l'oblige à la suivre, et en tire les virages.

Une **trace enregistrée** (une sortie Strava, une trace téléchargée) ou tracée à main levée ne colle pas exactement aux routes : le bruit du GPS la fait zigzaguer de quelques mètres. Donnée telle quelle à BRouter, elle produisait 772 instructions sur 11 km, chaque point bruité l'envoyant sur un trottoir ou une piste parallèle et retour. L'outil le détecte (trace en moyenne à plus de 1 m des routes), la **recale sur les routes** grâce à Valhalla avant de la donner à BRouter, et le signale. Il lisse aussi les altitudes bruitées, qui gonflaient le dénivelé et le nombre de points.

Il arrive que BRouter quitte ta trace, sur une route que son profil vélo évite : il y place alors les virages de son propre détour. L'outil repère ces passages (trace à plus de 50 m de l'itinéraire de BRouter), y remplace les instructions de BRouter par celles de Valhalla, garde celles de BRouter partout ailleurs, et indique les kilomètres concernés. Si BRouter refuse carrément la trace (passage interdit aux vélos) ou s'en écarte sur plus de la moitié, Valhalla calcule seul tous les virages. Un refus pour surcharge du serveur est retenté deux fois avant d'en arriver là.

#### Qualité mesurée

Sur un itinéraire vélo de 11,4 km à Lyon dont les 48 manœuvres et 33 noms de rue étaient connus, chaque méthode ne voyant que la trace :

| Méthode | Virages trouvés | Bon côté | Noms de rue |
| --- | --- | --- | --- |
| **BRouter + Valhalla** (mode en ligne) | **48/48** | **46/48** | **30/33** |
| Mode en ligne, même trajet **enregistré** (bruit GPS simulé de 4 m, un point tous les 5 m) | 41/48 | 38/41 | 29/33 |
| Valhalla seul (repli) | 39/48 | 36/39 | 26/33 |
| Forme de la trace seule, sans réseau | 33/48 | 31/33 | 0/33 |

Sur la trace enregistrée, 2 des 7 virages manqués sont annoncés à 30 m de leur place ; les 5 autres manquent vraiment. Une trace dessinée sur un planificateur reste donc le meilleur choix.

Sur 7 km d'un Lyon → Grenoble où BRouter quitte la trace pendant 1,7 km (en y inventant une vingtaine de virages), le mode en ligne trouve les 7 manœuvres réelles, toutes du bon côté, et plus aucun virage fantôme ; Valhalla seul n'en trouve que 4.

Ce sont deux trajets de test : sur tes propres sorties, relis la liste avec `doctor` avant de partir.

#### Vie privée

Le mode en ligne envoie ta trace complète (sans horaires ni altitudes, seulement les positions) :

- à **valhalla1.openstreetmap.de**, opéré par l'association [FOSSGIS](https://www.fossgis.de) pour la communauté OpenStreetMap. Ses [conditions](https://www.fossgis.de/arbeitsgruppen/osm-server/nutzungsbedingungen/) imposent au plus une requête par seconde, et les requêtes sont **enregistrées dans les journaux du serveur** ;
- à **brouter.de**, le serveur public du projet BRouter.

Une longue sortie part en plusieurs requêtes, espacées d'au moins une seconde : compte une à deux minutes pour 150 km enregistrés. L'outil affiche les requêtes au fur et à mesure.

Si une trace ne doit pas sortir de chez toi, utilise `--offline`, ou héberge BRouter et Valhalla toi-même et passe leurs adresses avec `--brouter URL` et `--valhalla URL`.

## 2. Convertir

Python 3.10 ou plus récent. Depuis ce dossier :

```bash
python -m bryton_route doctor ma-sortie.gpx
```

Affiche ce qui sera écrit, sans rien écrire : points, distance, dénivelé, et chaque instruction avec la distance jusqu'à la suivante et le nom de la rue. À lancer en premier : un fichier que le Rider n'aime pas n'apparaît simplement pas, sans message d'erreur.

```bash
python -m bryton_route convert ma-sortie.gpx
python -m bryton_route convert ma-sortie.gpx -o E:\PlanTrip
```

Écrit `ma-sortie.fit` à côté du GPX, ou directement dans `PlanTrip` sur le compteur branché. **Le nom du fichier est le nom de l'itinéraire sur le Rider** : `--name "Tour du Vercors"` en choisit un autre que celui du GPX, pratique pour les `export (3).gpx`.

| Option | Effet |
| --- | --- |
| `--name NOM` | nom de l'itinéraire sur le Rider, donc du `.fit` |
| `--offline` | n'utiliser que les instructions du GPX, ne rien envoyer en ligne |
| `--online` | recalculer virages et noms de rue en ligne même si le GPX a des instructions |
| `--tolerance M` | allègement du tracé, 2 m par défaut ; `0` garde tous les points (les instructions ne bougent jamais) |
| `--brouter URL`, `--valhalla URL` | utiliser d'autres serveurs, par exemple les tiens |
| `--allow-no-cues` | avec `--offline`, convertir un GPX sans instructions en simple ligne |

Pour une commande `bryton-route` utilisable partout : `pip install .`

## 3. Charger sur le Rider

1. Branche le Rider en USB et copie le `.fit` dans le dossier `PlanTrip`.
2. Éjecte puis débranche.
3. **Éteins complètement le Rider et rallume-le.**
4. Menu Itinéraires, choisis la nouvelle route. Le Rider génère alors ses fichiers de navigation dans `Tracks`.

Pour vérifier ce que le compteur a fait, rebranche-le :

```bash
python -m bryton_route rider E:\
```

Pour chaque itinéraire de `PlanTrip`, cette commande dit s'il a été importé : le Rider a créé ses fichiers dans `Tracks`, avec les mêmes instructions et les mêmes points que le `.fit`. Sinon, elle dit « pas encore importé ». Pour relire en détail la table d'instructions du Rider :

```bash
python -m bryton_route inspect "E:\Tracks\ma-sortie.tinfo"
```

## Premier essai sur le vélo

Le format est vérifié octet par octet contre un fichier de l'application Bryton, mais certaines choses ne se voient que sur l'écran du Rider. Avant une vraie sortie, fais un essai court près de chez toi.

1. Sur bikerouter.de (osmand-style), trace une boucle de 3 à 5 km avec au moins un virage à droite, un à gauche, un rond-point et une bifurcation.
2. `doctor boucle.gpx`, puis `convert boucle.gpx -o E:\PlanTrip`, éjecte, éteins, rallume et choisis l'itinéraire. Rebranche : `rider E:\` doit dire « importé … identiques au .fit ».
3. En roulant, pour chaque instruction, note si elle est annoncée, si la flèche est la bonne, si la rue est lisible, si la distance restante est juste et si l'annonce tombe au bon endroit.
4. Observe en particulier ce que personne n'a encore vu sur un Rider :
   - la **flèche des ronds-points** (simple direction, précédée du numéro de sortie) ;
   - les **instructions très rapprochées** : BRouter en met beaucoup en ville (21 à moins de 20 m de la suivante sur 16 km). Si elles gênent, augmente `turnInstructionCatchingRange` sur bikerouter ;
   - ce que fait le Rider si tu **quittes l'itinéraire** puis y reviens.
5. Refais l'essai avec une **trace sans instructions** (Komoot, Strava, une sortie enregistrée) : c'est le mode en ligne.
6. Ensuite seulement, teste :
   - un **long itinéraire** (100 km et plus, plusieurs centaines d'instructions) : le fichier de référence n'en avait que 96 ;
   - le **remplacement d'un itinéraire** : un `.fit` du même nom copié par-dessus l'ancien, le Rider regénère-t-il ses fichiers ? S'il garde l'ancien, supprime l'itinéraire depuis son menu, ou donne un autre nom avec `--name`.

Si une instruction est fausse, note le kilomètre : `doctor` et `inspect` permettent de retrouver ce qui a été écrit à cet endroit.

## Sur le téléphone, sans ordinateur

Sur Android, avec [Termux](https://f-droid.org/packages/com.termux/) (depuis F-Droid : la version du Play Store n'est plus mise à jour) et un adaptateur OTG USB-C mâle → USB-A femelle pour brancher le câble du Rider.

**Installation, une seule fois**, dans Termux :

```bash
pkg install python git
termux-setup-storage
git clone https://github.com/vercors04/Bryton420
cd Bryton420 && pip install .
mkdir -p ~/bin && cp termux/termux-file-editor ~/bin/ && chmod +x ~/bin/termux-file-editor
```

`termux-setup-storage` demande l'accès aux fichiers du téléphone : accepte. La dernière ligne installe le script qui fait la conversion quand on partage un GPX à Termux.

**À chaque sortie** :

1. Dans l'appli ou le site qui a fait l'itinéraire, **partage** le GPX vers **Termux**, puis choisis **Edit** (« Modifier »).
2. Donne un nom à l'itinéraire (ou garde celui proposé) : le `.fit` arrive dans le dossier `Download` du téléphone. Une trace seule demande une connexion, pour le mode en ligne.
3. Branche le Rider avec l'adaptateur OTG. Dans l'appli **Fichiers**, copie le `.fit` de `Download` vers `PlanTrip` sur le Rider : Termux ne peut pas écrire lui-même sur une clé USB.
4. Éjecte le Rider (notification USB, ou Paramètres → Stockage), débranche, éteins-le, rallume-le, et choisis l'itinéraire.

Pour mettre à jour l'outil : `cd Bryton420 && git pull && pip install .`

## Ce qu'il faut savoir

- **Ronds-points** : Bryton a des codes à lui, probablement « sortie 1 à 4 », mais leur affichage n'est pas confirmé. Un rond-point est donc affiché comme une flèche simple, de la route d'arrivée vers la route de sortie, avec le numéro de sortie et la rue de sortie : `(2) Avenue Berthelot`. L'angle fourni par BRouter est ignoré : il indiquait « à gauche » même pour une sortie tout droit. Les demi-tours sont aussi affichés comme un virage serré.
- **Noms de rue** : 32 octets au maximum (≈ 30 caractères). Un nom trop long perd d'abord son numéro de route (`Avenue Jean Moulin, D 306` → `Avenue Jean Moulin`), puis ses derniers mots, jamais un mot coupé en deux. Les accents passent.
- **Plusieurs tracés dans un GPX** : ils sont mis bout à bout s'ils se suivent (les étapes d'un même parcours) ; sinon seul le plus long est converti, et l'outil le dit.
- **Altitudes enregistrées** : lissées quand elles sont bruitées ; l'outil indique le dénivelé avant et après.
- **Points d'intérêt** (cols, ravitaillement) : non gérés.
- Un tracé très espacé (un point tous les 300 m) coupe les virages sur l'écran : `doctor` le signale.

## Le format et le code

Le fichier écrit reproduit, message pour message, la structure de celui que produit l'application Bryton Active. Tout vient de l'analyse d'une paire de référence — un `.fit` écrit par l'application et les fichiers que le Rider 420 en a tirés — conservée dans `tests/data/ex_bon`. Le détail de chaque champ est dans [doc/format.md](doc/format.md).

```
bryton_route/
  route.py       GPX → itinéraire : tracé, placement des instructions, avertissements
  cues.py        lecture des instructions (codes BRouter et ORS, symboles, phrases FR/EN/DE)
  gpx.py         lecture XML du GPX
  online.py      mode en ligne : calage, virages BRouter + noms Valhalla, et replis
  brouterweb.py  recalcul d'une trace par BRouter, et passages qu'il ne suit pas
  valhalla.py    calage Valhalla : noms de rue, trace recalée, virages en repli
  web.py         accès réseau : User-Agent, une requête par seconde, erreurs
  simplify.py    allègement du tracé sans toucher aux instructions
  bryton.py      format d'itinéraire Bryton (écriture et relecture)
  fit.py         conteneur FIT générique
  geo.py         géodésie WGS-84
  cli.py         ligne de commande
termux/          conversion d'un GPX partagé à Termux sur Android
tests/           python -m pytest (sans réseau : les réponses des serveurs sont enregistrées)
```

Ce projet repart des travaux de [matheus0312/BrytonUtilities](https://github.com/matheus0312/BrytonUtilities) et [Edward-Eth/BrytonUtilities24](https://github.com/Edward-Eth/BrytonUtilities24). Domaine public (Unlicense). Données cartographiques © les contributeurs [OpenStreetMap](https://www.openstreetmap.org/copyright).
