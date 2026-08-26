#!/usr/bin/env python3
"""Le suivi de consommation des moteurs : ce qui est compte, et ce qui prime sur le compte.

Ce que ces tests protegent, concretement : le jour ou Azure s'est vide, le tableau annonçait
toujours des heures restantes. Un chiffre faux et rassurant est pire qu'aucun chiffre — on
lui fait confiance au mauvais moment. Donc on verifie surtout les cas ou le compteur DOIT
s'effacer devant la realite.
"""

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = tempfile.mkdtemp(prefix="conso-")
os.environ["VOIX_CONSO"] = str(Path(_tmp) / "consommation.json")

import consommation as C   # noqa: E402  (apres VOIX_CONSO : il lit le chemin a l'import)

ok = ko = 0


def dire(vrai, quoi):
    global ok, ko
    if vrai:
        ok += 1
        print(f"  ok    {quoi}")
    else:
        ko += 1
        print(f"  ECHEC {quoi}")


@dataclass(frozen=True)
class Faux:
    """Un moteur fictif : les tests ne doivent pas dependre des paliers reels, qui changent."""
    cle: str
    libelle: str
    renouvelable: bool
    quota_h: float | None
    gratuit: str = "palier de test"
    dispo: bool = True


MENSUEL = Faux("mensuel", "Mensuel", True, 1)          # 1 h/mois
CREDIT = Faux("credit", "Credit", False, 2)            # 2 h une fois
ILLIMITE = Faux("illimite", "Illimite", True, None)
TABLE = (MENSUEL, CREDIT, ILLIMITE)


def par_cle(etats):
    return {e["cle"]: e for e in etats}


def vider():
    Path(os.environ["VOIX_CONSO"]).unlink(missing_ok=True)


# --- ce qui se compte, et ce qui ne se compte pas ---------------------------------------
print("\n=== ce qui se compte ===")
vider()
C.ajouter("mensuel", 600)
e = par_cle(C.etat(TABLE))
dire(e["mensuel"]["consomme_s"] == 600, "10 min imputees se retrouvent")
dire(e["mensuel"]["reste_s"] == 3000, "le reste est le palier moins le consomme")
dire(abs(e["mensuel"]["part"] - 600 / 3600) < 1e-6, "la part remplit la jauge")

C.ajouter("mensuel", 300)
dire(par_cle(C.etat(TABLE))["mensuel"]["consomme_s"] == 900, "les ajouts s'accumulent")

# Le local ne consomme rien : il tourne ici. Le compter afficherait un quota imaginaire.
C.ajouter("local", 9999)
dire("local" not in Path(os.environ["VOIX_CONSO"]).read_text(),
     "le local n'est jamais compte — il ne coute rien")

# Un aller-retour disque pour 0,2 s d'audio n'apporte rien.
avant = par_cle(C.etat(TABLE))["mensuel"]["consomme_s"]
C.ajouter("mensuel", 0.2)
dire(par_cle(C.etat(TABLE))["mensuel"]["consomme_s"] == avant,
     "sous le plancher, on n'ecrit pas")

# --- mensuel contre credit : deux natures, deux lectures ---------------------------------
print("\n=== un credit unique ne se recharge pas ===")
vider()
C.ajouter("credit", 3600)
C.ajouter("mensuel", 1800)
mois_suivant = C._mois
C._mois = lambda: "2099-01"      # on se projette au mois prochain
e = par_cle(C.etat(TABLE))
dire(e["mensuel"]["consomme_s"] == 0, "un quota mensuel repart a zero le mois suivant")
dire(e["credit"]["consomme_s"] == 3600, "un credit unique garde son cumul pour toujours")
C._mois = mois_suivant

# --- le constat prime sur l'estimation --------------------------------------------------
print("\n=== le constat d'epuisement prime ===")
vider()
C.ajouter("mensuel", 60)          # le compteur croit qu'il reste 59 min
dire(par_cle(C.etat(TABLE))["mensuel"]["reste_s"] == 3540, "avant constat : le reste est calcule")
C.constater_epuise("mensuel", "Quota exceeded for this month")
e = par_cle(C.etat(TABLE))["mensuel"]
dire(e["epuise"] and e["reste_s"] == 0 and e["part"] == 1.0,
     "apres constat : plus rien, quoi que dise le compteur")
dire("Quota exceeded" in (e["motif"] or ""), "le motif du refus est conserve et affichable")

C.oublier_epuise("mensuel")
dire(not par_cle(C.etat(TABLE))["mensuel"]["epuise"],
     "le moteur remarche : le constat s'efface")

# Un constat date : mensuel il expire, credit jamais.
C.constater_epuise("mensuel", "quota")
C.constater_epuise("credit", "out of credit")
garde = C._mois
C._mois = lambda: "2099-02"
e = par_cle(C.etat(TABLE))
dire(not e["mensuel"]["epuise"], "un mensuel epuise redevient credible le mois suivant")
dire(e["credit"]["epuise"], "un credit epuise ne revient jamais")
C._mois = garde

# --- un palier absent ne s'invente pas --------------------------------------------------
print("\n=== pas de palier, pas de jauge ===")
e = par_cle(C.etat(TABLE))["illimite"]
dire(e["palier_s"] is None and e["reste_s"] is None and e["part"] is None,
     "sans palier chiffre : aucune jauge, aucun reste invente")

# --- reconnaitre un quota d'une panne ---------------------------------------------------
print("\n=== quota ou panne ===")
for msg in ("Quota exceeded", "HTTP 429 Too Many Requests", "insufficient credits",
            "403 Forbidden", "your free tier has been used"):
    dire(C.ressemble_a_un_quota(msg), f"quota reconnu : « {msg[:34]} »")
for msg in ("connection reset by peer", "websocket closed unexpectedly",
            "invalid audio format", ""):
    dire(not C.ressemble_a_un_quota(msg), f"panne, pas quota : « {msg[:34] or 'vide'} »")

# --- le chrono decoupe a la bascule ------------------------------------------------------
print("\n=== le chrono impute au bon moteur ===")
vider()
import time as _t
faux_t = {"v": 1000.0}
vrai = _t.monotonic
C.time.monotonic = lambda: faux_t["v"]

ch = C.Chrono()
ch.ouvrir("mensuel")
faux_t["v"] += 120            # 2 min sur mensuel
ch.basculer("credit")         # Azure tombe, on passe au suivant
faux_t["v"] += 60             # 1 min sur credit
ch.fermer()
e = par_cle(C.etat(TABLE))
dire(e["mensuel"]["consomme_s"] == 120 and e["credit"]["consomme_s"] == 60,
     "a la bascule, chacun paie SON temps — pas celui du precedent")

# Deux ouvertures de suite ne doivent pas perdre le premier compteur.
vider()
ch = C.Chrono()
ch.ouvrir("mensuel")
faux_t["v"] += 30
ch.ouvrir("mensuel")          # deja ouvert : ne redemarre pas
faux_t["v"] += 30
ch.fermer()
dire(par_cle(C.etat(TABLE))["mensuel"]["consomme_s"] == 60,
     "une deuxieme ouverture ne remet pas le compteur a zero")

# Fermer sans avoir ouvert n'impute rien : le micro peut etre coupe deux fois.
vider()
ch = C.Chrono()
ch.fermer()
dire(par_cle(C.etat(TABLE))["mensuel"]["consomme_s"] == 0,
     "fermer sans avoir ouvert n'impute rien")
C.time.monotonic = vrai

# --- l'affichage ne rend jamais un nombre brut ------------------------------------------
print("\n=== duree lisible ===")
for s, attendu in ((None, "—"), (0, "0 s"), (45, "45 s"), (60, "1 min"),
                   (2220, "37 min"), (3600, "1 h 00"), (7440, "2 h 04")):
    dire(C.duree(s) == attendu, f"{s} → « {attendu} »")

# --- un fichier corrompu ne casse pas la session ----------------------------------------
print("\n=== robustesse ===")
Path(os.environ["VOIX_CONSO"]).write_text("{ceci n'est pas du json")
dire(len(C.etat(TABLE)) == 3, "un fichier tronque repart a zero au lieu de lever")
C.ajouter("mensuel", 60)
dire(par_cle(C.etat(TABLE))["mensuel"]["consomme_s"] == 60, "et il se reecrit proprement")

# La vraie table doit rester lisible par ce module : un champ renomme se verrait ici.
import moteurs_stt
reels = C.etat(moteurs_stt.MOTEURS)
dire(len(reels) == len(moteurs_stt.MOTEURS), "la vraie table de moteurs passe sans erreur")
dire(all(m["palier_s"] for m in reels if m["cle"] not in ("local", "groq")),
     "chaque moteur payant a un palier chiffre — sinon aucune jauge n'est possible")

print(f"\n  {ok} ok, {ko} echec(s)")
sys.exit(1 if ko else 0)
