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
# Mesure sur Soniox : « 402 - Organization balance exhausted ». La liste des motifs ne
# s'ecrit pas d'imagination — chaque entree vient d'un refus reel rencontre.
dire(C.ressemble_a_un_quota("402 - Organization balance exhausted. Please add funds"),
     "le refus 402 de Soniox est reconnu comme un quota, pas comme une panne")
# Et le piege inverse, que j'ai introduit en ajoutant « balance » tout court : un mot trop
# large fait passer une panne pour un quota, donc condamne un moteur qui reviendrait seul.
for msg in ("connection reset by peer", "websocket closed unexpectedly",
            "invalid audio format", "balance sheet parsing failed",
            "the load balancer refused the connection", ""):
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

# --- l'ordre de la chaine suit ce qu'il reste -------------------------------------------
# C'est le choix explicite : le meilleur moteur en tete tant que son credit est abondant,
# puis il recule pour garder sa reserve. Une regle qui ne se verifie pas est une regle qui
# derive : ces tests sont la pour qu'un renommage de champ ou un palier corrige ne la casse
# pas en silence.
print("\n=== l'ordre de la chaine reagit aux quotas ===")
import os as _os
_os.environ.pop("VOIX_STT", None)
_os.environ["VOIX_STT_PREF"] = ""            # pas de preference enregistree qui gagnerait
import moteurs_stt as MS


def sans_cles(f):
    """Fait comme si TOUTES les cles etaient presentes : l'ordre ne doit pas dependre de
    ce qui est installe sur la machine de celui qui lance les tests."""
    vraies = {}
    for m in MS.MOTEURS:
        if m.cle_env:
            vraies[m.cle_env] = _os.environ.get(m.cle_env)
            _os.environ[m.cle_env] = "test"
    try:
        return f()
    finally:
        for k, v in vraies.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v


vider()
ordre = sans_cles(MS.ordre_auto)
# La regle et sa raison : un quota MENSUEL se perd s'il n'est pas consomme, un credit attend
# sans rien perdre. Le mensuel passe donc devant, meme quand un credit fait un peu mieux au
# banc — le petit ecart de qualite coute moins cher que des heures gratuites jetees chaque mois.
# Deux regles, dans cet ordre, et la premiere gagne.
#
# 1. Ce qu'on a VU rendre une phrase complete passe devant. Un quota mensuel ne vaut rien si
#    le moteur rend la moitie de la phrase : on n'economise pas, on se fait mal comprendre —
#    et une transcription tronquee ne ressemble pas a une panne, elle ressemble a une
#    instruction, donc on agit dessus.
# 2. A demonstration egale, le MENSUEL passe devant : c'est le seul qui se perd s'il n'est
#    pas consomme, un credit attend le mois prochain sans rien perdre.
dire(MS.PAR_CLE[ordre[0]].demontre,
     f"la chaine ouvre sur un moteur demontre (ici {ordre[0]})")
dire(all(ordre.index(c) < ordre.index(d)
         for c in ordre for d in ordre
         if MS.PAR_CLE[c].demontre and not MS.PAR_CLE[d].demontre
         and MS.PAR_CLE[c].direct and MS.PAR_CLE[d].direct),
     "TOUS les moteurs demontres passent avant les autres")
# Sur une table vierge c'est Azure qui ouvre, et c'est juste : il est demontre ET mensuel,
# donc les deux regles le placent en tete. Il ne passe au bout que dans l'etat REEL de cette
# machine, ou son palier est constate epuise — verifie plus bas. L'assertion porte donc sur la
# regle, pas sur ce que la machine affiche aujourd'hui.
dire(MS.PAR_CLE[ordre[0]].demontre and MS.PAR_CLE[ordre[0]].renouvelable,
     f"le premier est demontre ET mensuel — les deux regles vont dans le meme sens (ici {ordre[0]})")
dire(ordre.index("assemblyai") < ordre.index("speechmatics"),
     "un credit demontre passe devant un mensuel qui n'a rien prouve")
# La regle du mensuel reste vraie ENTRE moteurs demontres — verifie sur une table ou tout
# est demontre, sinon on ne teste que l'effet de la premiere regle.
import dataclasses
tous_demontres = tuple(dataclasses.replace(m, demontre=True) for m in MS.MOTEURS)
sauve = MS.MOTEURS, MS.PAR_CLE
MS.MOTEURS, MS.PAR_CLE = tous_demontres, {m.cle: m for m in tous_demontres}
ordre2 = sans_cles(MS.ordre_auto)
dire(MS.PAR_CLE[ordre2[0]].renouvelable,
     f"a demonstration egale, un mensuel ouvre : c'est lui qui se perd (ici {ordre2[0]})")
premier_credit = next(c for c in ordre2 if not MS.PAR_CLE[c].renouvelable and c != "local")
dire(all(ordre2.index(c) < ordre2.index(premier_credit)
         for c in ordre2 if MS.PAR_CLE[c].renouvelable and MS.PAR_CLE[c].direct),
     "et tous les mensuels passent avant le premier credit")
MS.MOTEURS, MS.PAR_CLE = sauve
dire(ordre[-1] == "local", "le local ferme toujours la marche : il ne peut pas manquer")
dire(ordre.index("groq") > ordre.index("azure"),
     "un moteur sans texte en direct passe derriere tous ceux qui en ont")

# Sous sa reserve, le credit recule derriere les mensuels : c'est a ça que sert la reserve.
# Un credit passe SOUS sa reserve recule derriere les autres credits : il se garde pour les
# mois ou les mensuels seront epuises tot, ce qui est exactement sa raison d'etre.
C.ajouter("deepgram", (430 - 90) * 3600)
ordre = sans_cles(MS.ordre_auto)
dire(ordre.index("deepgram") > ordre.index("assemblyai"),
     "sous sa reserve, un credit recule derriere les credits encore abondants")
dire(ordre.index("deepgram") < ordre.index("gladia"),
     "et il reste devant un moteur non demontre, credit ou pas : la preuve compte d'abord")
dire(ordre.index("deepgram") < ordre.index("local"),
     "mais devant le local : un credit garde vaut mieux que 7 s de latence")

# Tous les mensuels epuises : les credits prennent le relais, dans l'ordre de qualite.
for c in ("speechmatics", "gladia", "azure", "google"):
    C.constater_epuise(c, "quota")
ordre = sans_cles(MS.ordre_auto)
dire(not MS.PAR_CLE[ordre[0]].renouvelable,
     f"mensuels a sec : un credit prend la tete (ici {ordre[0]})")
dire(ordre[0] == "assemblyai",
     f"et c'est un credit ABONDANT, pas celui passe sous reserve (ici {ordre[0]})")
dire(ordre.index("deepgram") < ordre.index("local"),
     "mais il reste devant le local : un credit garde vaut mieux que 7 s de latence")

# Un constat d'epuisement l'envoie a la fin, quelle que soit sa qualite. On repart d'un
# etat propre : les blocs precedents ont epuise plusieurs moteurs, et comparer deux moteurs
# tous les deux epuises ne prouverait rien.
vider()
C.constater_epuise("speechmatics", "Quota exceeded")
ordre = sans_cles(MS.ordre_auto)
dire(ordre.index("speechmatics") > ordre.index("gladia"),
     "un moteur constate epuise part a la fin, meme s'il etait le meilleur")
dire(ordre.index("speechmatics") > ordre.index("local"),
     "derriere le local meme : mieux vaut 7 s de latence qu'un moteur qui refusera")

# Le pire cas : tout est epuise. La chaine doit rester non vide, sinon on devient sourd.
for c in ("deepgram", "speechmatics", "gladia", "azure", "assemblyai", "soniox", "google"):
    C.constater_epuise(c, "quota")
ordre = sans_cles(lambda: MS.chaine("auto"))
dire("local" in ordre, "tous les paliers vides : le local reste, on ne devient jamais sourd")

# Un ordre impose a la main gagne sur tout le calcul : c'est une decision de l'utilisateur.
impose = sans_cles(lambda: MS.chaine("azure,local"))
dire(impose[0] == "azure",
     "un ordre impose gagne sur le calcul, meme sur un palier constate epuise")

vider()

# --- le fichier de secrets, tel que l'APPLICATION le lit --------------------------------
# Le piege vecu : la cle AssemblyAI etait bien dans le fichier, ecrite « export CLE="..." »,
# et le tableau affichait « pas de cle ». `partition("=")` fabriquait une variable nommee
# « export ASSEMBLYAI_API_KEY ». Une cle ignoree en silence est le pire des echecs — on la
# cherche partout sauf la ou elle est.
#
# Et la lecon de methode, plus importante que le correctif : ma verification l'avait masque
# parce que je chargeais le fichier avec `source` en bash, qui comprend `export`. Verifier
# autrement que l'application ne verifie rien.
print("\n=== le fichier de secrets se lit dans tous ses formats ===")
import tempfile as _tf
_boite = Path(_tf.mkdtemp(prefix="secrets-"))
_f = _boite / "secrets.env"
_f.write_text("# un commentaire\n"
              "NU=valeur-nue\n"
              "export AVEC=valeur-export\n"
              'export GUILL="entre guillemets"\n'
              "AVEC_ESPACES = espaces autour \n"
              "VIDE=\n", encoding="utf-8")
import config as _cfg
_vrai = _cfg.SECRETS
_cfg.SECRETS = _f
for _k in ("NU", "AVEC", "GUILL", "AVEC_ESPACES", "VIDE"):
    os.environ.pop(_k, None)
_cfg._load_secrets()
_cfg.SECRETS = _vrai
for _k, _attendu in (("NU", "valeur-nue"), ("AVEC", "valeur-export"),
                     ("GUILL", "entre guillemets"), ("AVEC_ESPACES", "espaces autour"),
                     ("VIDE", "")):
    dire(os.environ.get(_k) == _attendu,
         f"« {_k} » lu correctement : {os.environ.get(_k)!r}")
dire("export AVEC" not in os.environ,
     "et aucune variable fantome nommee « export ... » n'est creee")

print(f"\n  {ok} ok, {ko} echec(s)")
sys.exit(1 if ko else 0)
