"""Coverage of the local orders across formulations, and above all the non-regressions:
a coding instruction that happens to contain an order's words must reach Claude."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from intentions import reconnaitre, modele_demande

CAS = {
    "micro": [
        "coupe le micro", "coupe mon micro", "micro coupé", "coupez le micro",
        "tu peux couper le micro s'il te plaît", "ferme le micro",
        "arrête d'écouter", "n'écoute plus", "mute", "éteins le micro",
        "désactive le microphone", "je veux couper le micro", "coupe-moi le micro",
        "s'il te plaît coupe le micro", "arrête de m'entendre", "vire le micro",
    ],
    "silence": [
        "chut", "tais-toi", "silence", "arrête de parler", "coupe ta voix",
        "cesse de parler", "arrête de causer",
    ],
    "arret": [
        "stop", "arrête tout", "laisse tomber", "annule", "oublie ça",
        "arrête le travail", "interromps la tâche", "abandonne ça",
    ],
    "statut": [
        "t'en es où ?", "où tu en es", "ça avance ?", "tu fais quoi",
        "quoi de neuf", "il reste combien",
    ],
    "repete": [
        "répète", "pardon ?", "j'ai pas compris", "j'ai pas entendu",
        "qu'est-ce que tu as dit", "redis ça",
    ],
    "quota": [
        "quota", "combien il me reste", "il reste combien de jetons",
        "où en est la fenêtre", "rate limit", "j'ai consommé combien de tokens",
    ],
    "modele": [
        "passe en haiku", "bascule sur sonnet", "utilise opus", "passe sur fable",
        "utilise fable", "mets-toi en fable", "prends un modèle plus rapide",
        "change de modèle",
    ],
}

# Le nom entendu -> la cle de config.MODELES. Fable compte double : c'est le seul modele qui
# porte un mot francais courant, donc le seul ou une reconnaissance trop large ferait basculer
# de modele au milieu d'une phrase qui parle d'autre chose (voir les PASSANTS).
MODELE_VOULU = {
    "passe en haiku": "haiku",
    "bascule sur sonnet": "sonnet",
    "utilise opus": "opus",
    "passe sur fable": "fable",
    "mets-toi en fable": "fable",
    "prends un modèle plus rapide": "haiku",
    "reviens au modèle normal": "opus",
    "change de modèle": None,
}

# Doit partir chez Claude, malgre les mots-cles.
PASSANTS = [
    "ajoute un bouton pour couper le micro dans le front",
    "crée une fonction qui coupe le micro quand on clique",
    "implémente l'arrêt du travail dans le tableau",
    "écris un test qui vérifie qu'on peut couper le micro proprement",
    "renomme la variable qui gère le silence",
    "le micro de mon casque est cassé, tu peux regarder le driver",
    "corrige le quota affiché dans l'en-tête",
    "supprime le bouton qui arrête le travail",
    "explique-moi comment fonctionne la détection de fin de tour dans le module",
    "commute la branche et lance les tests",
    # « fable » est un nom de modele ET un mot francais : ces six phrases sont exactement
    # celles qui basculaient de modele quand le mot etait un objet d'intention comme les
    # autres. Elles gardent la porte etroite.
    "écris une fable sur un agent vocal dans le README",
    "le test s'appelle fable_test, corrige-le",
    "utilise une fable comme exemple dans la doc",
    "prends la fable du corbeau et mets-la dans les fixtures",
    "mets la fable en annexe du rapport",
    "change la fable de place dans le fichier",
]

ok = rate = 0
for attendu, phrases in CAS.items():
    for p in phrases:
        trouve, pourquoi = reconnaitre(p)
        bon = trouve == attendu
        ok += bon; rate += not bon
        if not bon:
            print(f"  RATE   {p!r} -> {trouve} (attendu {attendu})")
print(f"  commandes reconnues : {ok}/{ok+rate}")

fuites = 0
for p in PASSANTS:
    trouve, pourquoi = reconnaitre(p)
    if trouve:
        fuites += 1
        print(f"  FUITE  {p!r} -> {trouve} ({pourquoi})")
print(f"  instructions preservees : {len(PASSANTS)-fuites}/{len(PASSANTS)}")

# Reconnaitre l'ordre ne suffit pas : encore faut-il en tirer LE BON modele. « passe sur
# fable » qui atterrit sur opus se voit au debrief, pas au moment du basculement.
mauvais = 0
for phrase, attendu in MODELE_VOULU.items():
    trouve = modele_demande(phrase)
    if trouve != attendu:
        mauvais += 1
        print(f"  MODELE {phrase!r} -> {trouve} (attendu {attendu})")
print(f"  modeles vises : {len(MODELE_VOULU)-mauvais}/{len(MODELE_VOULU)}")

sys.exit(1 if (rate or fuites or mauvais) else 0)
