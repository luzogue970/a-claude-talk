# claude-talk — conventions de travail


## Versions : une tâche finie monte le numéro, et la montée atteint l'app

Une carte du tableau de bord qui affiche « sans version » ne peut pas dire si l'app
qu'on tient dans la main est la dernière. C'est tout ce qu'on demande à un numéro.
Avant le 4 octobre 2026, trois projets sur sept n'en avaient aucun, et aucun des
sept n'avait jamais été étiqueté : Halo affichait 0.9.1 avec quatre-vingt-un commits
depuis, et le paquet servi au téléphone avait onze jours.

**À chaque tâche aboutie, après le commit :**

```
version-projet fix     # une correction
version-projet min     # une nouveauté
version-projet maj     # une rupture
git push --follow-tags
```

`version-projet` écrit `VERSION`, propage le numéro dans les `package.json` et
`app.json`, régénère le `CHANGELOG` quand le projet sait le faire, commite et pose
une **étiquette Git `vX.Y.Z`**. C'est l'étiquette qui fait foi : un fichier dit ce
qu'on a écrit, une étiquette dit *quand on l'a sorti*.

**Puis la montée doit atteindre l'app.** Un numéro qui monte sans que l'application
change n'a rien réglé — c'était exactement le défaut. Depuis le tableau de bord,
onglet Monitoring, le bouton **« Mettre à jour »** de la carte reconstruit le paquet,
redémarre le service qui le sert, et relance le serveur Expo Go pour que le téléphone
reçoive le nouveau code. La carte affiche ensuite la version, sa date, et le nombre de
commits écrits depuis — en orange quand il y en a. Zéro commit depuis, c'est que ce
qui tourne est bien ce qui est écrit.
