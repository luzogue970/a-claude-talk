#!/usr/bin/env python3
"""Une conversation qui grossit doit le DIRE, et pouvoir s'alléger sans qu'on la quitte.

La mesure qui a motivé ce fichier, prise sur cette machine le 5 octobre 2026 : six
conversations au-dessus de 850 000 jetons, deux collées au plafond du million, environ
1 190 $ d'API à elles toutes. Aucune alerte, nulle part — et pourtant le chiffre était à
l'écran depuis le début. Il y était sans échelle, et un nombre sans échelle ne se lit pas :
personne ne sait si 400 000 jetons est beaucoup.

Ce que ça coûte vraiment, et c'est de l'arithmétique, pas une impression : une conversation
relit son contexte ENTIER à chaque aller-retour, et un tour avec dix outils en fait dix. À
500 000 jetons, l'aller-retour coûte 0,25 $ en lecture de cache Opus — le même tour, en
début de conversation, en coûte deux centimes. Le compactage automatique du CLI existe, mais
il attend le plafond : on traverse donc toute la zone chère avant qu'il ne se passe quoi que
ce soit.

Trois règles, donc :

1. **Le poids se dit en coût**, parce que c'est la seule forme qui se comprend sans échelle.
2. **Une fois par palier franchi**, jamais à chaque tour — une alerte permanente devient un
   décor, et le jour où elle compte elle ne se distingue plus du reste.
3. **« compacte » allège sans quitter la conversation**, et un compactage — y compris celui
   que le CLI déclenche tout seul — s'annonce, au lieu de ressembler à un trou de mémoire.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class TableauFactice:
    def __init__(self):
        self.lignes = []

    def publier(self, genre, **donnees):
        self.lignes.append((genre, donnees))

    def alertes(self):
        return [d for g, d in self.lignes
                if str(d.get("source")) == "contexte"
                and str(d.get("niveau", "")).upper() in ("WARNING", "ERROR")]


def principal():
    import worker
    from worker import Worker, SEUILS_CONTEXTE

    print("=== le poids se dit en coût, pas en nombre nu ===")
    tab = TableauFactice()
    w = Worker(tableau=tab)
    w.journal.contexte = 150_000
    dire(w.poids() is None, "sous le premier palier, on ne dit rien : ce n'est pas un sujet")
    w.journal.contexte = 500_000
    phrase = w.poids()
    dire("500 k" in phrase, f"le poids est lisible → {phrase}")
    dire("0.25" in phrase or "0,25" in phrase,
         "et il porte son coût par aller-retour, la seule échelle qui parle")

    print("\n=== une alerte par palier franchi, et pas une de plus ===")
    tab2 = TableauFactice()
    w2 = Worker(tableau=tab2)
    for poids in (210_000, 220_000, 260_000, 300_000):
        w2.journal.contexte = poids
        w2._veiller_au_contexte()
    dire(len(tab2.alertes()) == 1,
         f"quatre tours au-dessus du même palier → {len(tab2.alertes())} alerte(s)")
    w2.journal.contexte = 420_000
    w2._veiller_au_contexte()
    dire(len(tab2.alertes()) == 2, "le palier suivant, lui, se dit")
    w2.journal.contexte = 650_000
    w2._veiller_au_contexte()
    dire(any(g == "erreur" for g, _ in tab2.lignes),
         "et au-delà de 600 k ça devient une erreur, pas une note de bas de page")

    print("\n=== un compactage s'annonce et remet les compteurs ===")
    from claude_agent_sdk import SystemMessage
    tab3 = TableauFactice()
    w3 = Worker(tableau=tab3)
    w3.journal.contexte = 700_000
    w3._veiller_au_contexte()
    avant = len(tab3.alertes())
    w3._systeme(SystemMessage(subtype="compact_boundary", data={
        "compact_metadata": {"trigger": "auto", "pre_tokens": 712_345}}))
    dit = " ".join(str(d.get("texte", "")) for _, d in tab3.lignes)
    dire("712 k" in dit, "ce que la conversation pesait avant est dit")
    dire("automatiquement" in dit,
         "et qu'il était automatique — sinon l'oubli soudain reste inexplicable")
    dire(w3.journal.contexte == 0, "le contexte repart de zéro côté affichage")
    w3.journal.contexte = 250_000
    w3._veiller_au_contexte()
    dire(len(tab3.alertes()) > avant,
         "et les paliers se redisent : regrossir après un compactage est une nouvelle")

    print("\n=== le poids d'une conversation se lit sur disque avant de la reprendre ===")
    import journal as j
    with tempfile.TemporaryDirectory() as tmp:
        racine = Path(tmp)
        (racine / "projet").mkdir()
        f = racine / "projet" / "aaaa1111-2222-3333-4444-555555555555.jsonl"
        f.write_text("\n".join(json.dumps(l) for l in [
            {"type": "user", "message": {"role": "user", "content": "bonjour"}},
            {"type": "assistant", "message": {"usage": {
                "input_tokens": 10, "cache_read_input_tokens": 90_000,
                "cache_creation_input_tokens": 1_000}}},
            {"type": "assistant", "message": {"usage": {
                "input_tokens": 2, "cache_read_input_tokens": 300_000,
                "cache_creation_input_tokens": 500}}},
        ]) + "\n", encoding="utf-8")
        ancien, j.SESSIONS = j.SESSIONS, racine
        try:
            poids = j.poids_session("aaaa1111-2222-3333-4444-555555555555")
            dire(poids == 300_502, f"le DERNIER aller-retour fait foi, pas le plus gros → {poids}")
            dire(j.poids_session("inconnue") == 0, "une session inconnue ne pèse rien, sans râler")
        finally:
            j.SESSIONS = ancien

    print("\n=== « compacte » est un ordre, « compacte ce tableau » une tâche ===")
    import intentions
    for phrase in ("compacte", "compacte la conversation", "allège le contexte",
                   "résume la conversation"):
        nom, pourquoi = intentions.reconnaitre(phrase)
        dire(nom == "compacte", f"« {phrase} » → {nom} ({pourquoi})")
    for phrase in ("compacte ce tableau en une ligne",
                   "écris une fonction qui compacte le contexte"):
        nom, _ = intentions.reconnaitre(phrase)
        dire(nom != "compacte", f"« {phrase} » reste une tâche (→ {nom})")

    print("\n=== les seuils restent cohérents ===")
    dire(list(SEUILS_CONTEXTE) == sorted(SEUILS_CONTEXTE) and SEUILS_CONTEXTE[0] > 0,
         f"paliers croissants : {SEUILS_CONTEXTE}")
    dire(abs(worker.PRIX_LECTURE_CACHE_PAR_JETON * 1_000_000 - 0.50) < 1e-9,
         "le prix de lecture de cache Opus est bien 0,50 $ le million")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(principal())
