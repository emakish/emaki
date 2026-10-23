<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# French glossary

Use natural French, vous, sentence case and concise infinitive button labels.
Translate whole messages in context. Preserve placeholders, markup, line breaks,
command names, identifiers and Qt date-pattern field layout.
French has two plural forms: `nplurals=2; plural=(n > 1);` (zero uses singular).

| English | French |
| --- | --- |
| Programs & Updates | Programmes et mises à jour |
| Settings | Paramètres |
| Notes | Notes |
| Emaki Notes | Notes Emaki |
| Welcome | Bienvenue |
| Clocks | Horloges |
| What's new | Nouveautés |
| clock shade | volet de l’horloge |
| Change language | Changer de langue |
| update reminder | rappel de mise à jour |
| computer | ordinateur |
| file / folder | fichier / dossier |
| home folder | dossier personnel |
| password | mot de passe |
| download | télécharger / téléchargement |
| upload | téléverser |
| update / upgrade | mise à jour / mise à niveau |
| install / uninstall | installer / désinstaller |
| software / program / app | logiciel / programme / application |
| Graphics (app category) | Graphisme |
| Graphics (GPU hardware) | Carte graphique |
| Installed (program list) | Installés |
| Installed (one program) | Installé |
| Rich (application set) | Complet |
| display / screen | écran |
| workspace | espace de travail |
| overview | vue d’ensemble |
| desktop | bureau |
| notification | notification |
| restore point | point de restauration |
| snapshot | instantané |
| backup | sauvegarde |
| rollback | restauration |
| sign in / sign out | ouvrir la session / fermer la session |
| lock / unlock | verrouiller / déverrouiller |
| Lock screen (action) | Verrouiller l’écran |
| restart / shut down | redémarrer / éteindre |
| suspend / sleep | mettre en veille / veille |
| hibernate | mettre en hibernation |
| power | alimentation |
| network / wireless | réseau / sans fil |
| proxy | serveur mandataire |
| keyboard layout / shortcut | disposition du clavier / raccourci clavier |
| Left / Right / Up / Down (keys) | Gauche / Droite / Haut / Bas |
| Shift / Space (keys, in prose) | Maj / Espace — never inside a typed shortcut example; keep accepted input names such as Super, Ctrl, Alt, Shift and Space |
| Enter (key, in prose) | Entrée |
| Caps Lock is on | Verr. Maj activé |
| switching order | ordre de basculement |
| wallpaper | fond d’écran |
| mouse / trackpad | souris / pavé tactile |
| pin / unpin | épingler / détacher |
| login name | identifiant |
| sudo key | clé sudo (temporary authorization, never a stored password) |
| report a problem | signaler un problème |
| GitHub issue | signalement |
| review (program rating) | avis |
| speakers / headphones | haut-parleurs / casque |
| Command key (Mac) | Commande |
| default | par défaut |
| enabled / disabled | activé / désactivé |
| Apply / Cancel / Close | Appliquer / Annuler / Fermer |
| Save / Delete | Enregistrer / Supprimer |
| Remove (uninstall a program) | Supprimer |
| Remove (from a list or picture selection) | Retirer |
| Back / Next / Done | Retour / Suivant / Terminé |
| Retry / Continue | Réessayer / Continuer |
| repository / package | dépôt / paquet |
| build | compiler / compilation |
| firmware | micrologiciel |
| clipboard | presse-papiers |
| Picture (capture) | Image |
| Kept (plural) | Conservés |
| New / Deleted (partition) | Nouvelle / Supprimée |
| Elapsed (time) | Temps écoulé |
| launcher | lanceur |
| Night Light | Éclairage nocturne |
| Lock & Login | Verrouillage et connexion |

Keep Emaki, niri, Arch, AUR, Flathub, Secure Boot, GRUB and snapper unchanged.
Keep literal firmware menu choices (Enroll MOK, Continue, Yes, Reboot), command
syntax and accepted setting values unchanged. Human-readable CLI labels are
translated; JSON keys and protocol values are not. Use « … » around prose and
curly apostrophes, while preserving literal input syntax. Use U+00A0 before a
colon and inside guillemets, and U+202F before semicolons, exclamation marks and
question marks and within grouped thousands (1 024). Use U+00A0 between a number,
%1 or %n and %: 82 %, %1 %, %n %. Preserve literal tokens
such as `:cancel`, Qt date/time patterns and gettext metadata. Numeric input
hints may use a decimal comma where the input accepts it; preserve numeric format
placeholders. Translate size units inside French catalogue strings: B → octets,
KB → Ko, MB → Mo, GB → Go, KiB → Kio, MiB → Mio and GiB → Gio. Use U+00A0
between a number and its unit. Literal command syntax, JSON values and units
generated outside catalogues retain their source format until corrected in code.
The notes date-entry hint is `AAAA-MM-JJ`. Invariant terms and
French cognates are documented individually in `fr-unchanged.json`.
