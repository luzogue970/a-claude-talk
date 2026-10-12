"""« Hey Claude » : les verrous qui empechent la facture de monter.

Ce fichier couvre le module qui n'avait AUCUN test — 496 instructions, 0 % — alors que
c'est le seul du projet qui depense de l'argent tout seul, en continu, sans que personne
regarde. Un plafond qui ne tient pas se decouvre sur une facture, un mois plus tard.

Ce qui est protege ici, dans l'ordre ou ca coute :

  1. le barrage : les quatre raisons de ne RIEN envoyer, et leur priorite ;
  2. le plafond : le refus arrive AVANT l'appel, donc il ne peut pas etre depasse ;
  3. la comptabilite : transcription ET synthese sont facturees, donc toutes deux comptees,
     et un jour qui tourne archive au lieu d'ecraser ;
  4. le reveil : ce qui reveille l'assistant, et surtout ce qui ne doit pas le reveiller.

Les verrous 1 a 4 de l'en-tete du module sont gratuits ; la ligne d'apres est facturee.
Ces tests vivent exactement sur cette ligne.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
# Le module previent a voix haute quand le plafond tombe — c'est voulu en service, et
# c'est du bruit ici : la suite PROVOQUE ce cas pour le verifier.
logging.getLogger("heyclaude").setLevel(logging.CRITICAL)

# Le fichier d'etat part dans un dossier jetable AVANT toute construction de Plafond : sinon
# la suite lirait — et surtout ECRIRAIT — la comptabilite reelle de la machine.
import assistant
_bac = tempfile.TemporaryDirectory()
assistant.ETAT = Path(_bac.name) / "assistant.json"
assistant.COUPE = Path(_bac.name) / "ecoute-coupee"

import config
from assistant import Assistant, Plafond, fin_demandee, reveil

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def neuf(limite: float = 10.0) -> Plafond:
    """Un plafond vierge : le fichier d'etat est efface, pas seulement l'objet."""
    assistant.ETAT.unlink(missing_ok=True)
    return Plafond(limite)


# --- 1. le reveil ------------------------------------------------------------------------

def on_reveille_sur_le_nom_pas_sur_l_interjection():
    print("\n=== reveil : Azure francise « Hey », mais pas « Claude » ===")
    # Les francisations constatees. Chercher « hey » etait perdu d'avance.
    for dit in ("Hey Claude quelle heure il est", "Aie Claude quelle heure il est",
                "Et Claude quelle heure il est", "Dix Claude quelle heure il est",
                "Claude quelle heure il est"):
        dire(reveil(dit) == "quelle heure il est", f"« {dit} » reveille, question extraite")

    dire(reveil("Et clode, quelle heure il est") == "quelle heure il est",
         "une transcription approximative du nom passe aussi")
    # Le nom DOIT etre tot : sinon toute phrase contenant « Claude » declenche une requete.
    dire(reveil("je me demandais si on pouvait demander a Claude") is None,
         "le nom arrive trop tard dans la phrase : on ne reveille pas")
    dire(reveil("il faudrait relancer les tests ce soir") is None,
         "une phrase ordinaire ne reveille rien")
    dire(reveil("Claude") == "",
         "le nom seul reveille avec une question vide, pas None : c'est un appel, pas du bruit")
    dire(reveil("") is None, "le silence ne reveille rien")


def raccrocher_se_dit_court():
    print("\n=== fin de conversation : court, et au debut ===")
    for dit in ("c'est bon", "merci c'est tout", "au revoir", "termine", "stop",
                "on arrete", "laisse tomber", "à plus"):
        dire(fin_demandee(dit), f"« {dit} » raccroche")
    # Le piege : la formule apparait, mais la phrase parle d'autre chose.
    dire(not fin_demandee("a la fin du fichier ajoute une ligne vide"),
         "« a la fin du fichier » ne raccroche pas")
    dire(not fin_demandee("c'est bon signe que les tests passent tous les trois"),
         "une phrase longue qui commence pareil ne raccroche pas")
    dire(not fin_demandee("stoppe le serveur puis relance-le et verifie le port"),
         "un ordre qui commence par « stop » mais continue n'est pas un adieu")


# --- 2. la comptabilite ------------------------------------------------------------------

def les_deux_depenses_sont_comptees():
    print("\n=== plafond : la synthese est facturee autant que la transcription ===")
    p = neuf()
    dire(p.centimes == 0.0, "un plafond neuf n'a rien depense")
    # Le tarif fait foi, pas un nombre en dur : si config change, le test suit.
    une_heure = Plafond.cout_stt(3600)
    dire(abs(une_heure - config.TARIF_STT_H * 100) < 1e-9,
         f"une heure d'audio coute le tarif horaire en centimes ({une_heure:.2f})")
    un_million = Plafond.cout_tts(1_000_000)
    dire(abs(un_million - config.TARIF_TTS_M * 100) < 1e-9,
         f"un million de caracteres lus coute le tarif au million ({un_million:.2f})")

    p.consommer_stt(60)
    dire(p.centimes > 0, "une minute transcrite se voit")
    avant = p.centimes
    p.consommer_tts(5000)
    dire(p.centimes > avant,
         "et la parole AUSSI : c'est elle qui passait a cote du compteur")


def le_refus_arrive_avant_la_depense():
    print("\n=== plafond : on refuse AVANT d'appeler, donc on ne depasse jamais ===")
    p = neuf(limite=1.0)                      # 1 centime, soit 36 s d'audio au tarif par defaut
    tient = p.tient(Plafond.cout_stt(10))
    dire(tient, "dix secondes tiennent dans le plafond")
    dire(not p.tient(Plafond.cout_stt(100_000)),
         "un segment qui ne tient pas est refuse plutot que tronque")
    # On remplit le plafond, puis on verifie qu'il ne reste rien et que tout est refuse.
    p.consommer_stt(3600 * 10)
    dire(p.reste() == 0.0, f"le reste est borne a zero, jamais negatif ({p.reste()})")
    dire(not p.tient(0.0001), "plafond atteint : meme une requete minuscule est refusee")


def un_jour_qui_tourne_archive_au_lieu_d_ecraser():
    print("\n=== plafond : hier ne disparait pas a minuit ===")
    p = neuf()
    p.consommer_stt(600)                      # dix minutes « hier »
    hier = (date.today() - timedelta(days=1)).isoformat()
    # On force la bascule : l'objet se croit hier, la date du jour a change sous lui.
    p.jour = hier
    p._basculer_si_nouveau_jour()
    dire(p.jour == date.today().isoformat(), "le compteur du jour est reparti a la date du jour")
    dire(p.stt_s == 0.0, "et remis a zero")
    dire(p.histoire.get(hier, {}).get("stt_s") == 600,
         f"mais hier est ARCHIVE, pas efface : {p.histoire.get(hier)}")

    ct, minutes, car, jours = p.cumul(None)
    dire(abs(minutes - 10.0) < 0.01,
         f"le cumul depuis le debut retrouve les dix minutes d'hier ({minutes:.1f} min)")
    dire(jours == 1, f"et compte un seul jour actif ({jours})")

    # La fenetre glissante exclut ce qui est trop vieux.
    vieux = (date.today() - timedelta(days=40)).isoformat()
    p.histoire[vieux] = {"stt_s": 36000, "tts_car": 0}
    ct30, _, _, _ = p.cumul(30)
    cttout, _, _, _ = p.cumul(None)
    dire(cttout > ct30,
         "une depense de quarante jours entre dans « depuis le debut » mais pas dans « 30 jours »")


def la_comptabilite_survit_au_redemarrage():
    print("\n=== plafond : ce qui est depense est ecrit sur le disque ===")
    p = neuf()
    p.consommer_stt(123)
    p.consommer_tts(4567)
    # Un nouveau Plafond, comme apres un redemarrage du service : il relit le fichier.
    q = Plafond(10.0)
    dire(abs(q.stt_s - 123) < 1e-6 and q.tts_car == 4567,
         f"un service qui redemarre retrouve sa depense ({q.stt_s} s, {q.tts_car} car.)")
    dire(abs(q.centimes - p.centimes) < 1e-9, "donc le plafond du jour n'est pas remis a neuf")

    ecrit = json.loads(assistant.ETAT.read_text(encoding="utf-8"))
    dire(ecrit["jour"] == date.today().isoformat() and "histoire" in ecrit,
         "le fichier porte le jour et l'archive des jours passes")


def un_fichier_illisible_ne_tue_pas_le_service():
    print("\n=== plafond : un etat corrompu repart de zero au lieu de planter ===")
    assistant.ETAT.parent.mkdir(parents=True, exist_ok=True)
    assistant.ETAT.write_text("{ ceci n'est pas du json", encoding="utf-8")
    p = Plafond(10.0)
    dire(p.stt_s == 0.0 and p.tts_car == 0,
         "un fichier illisible donne un compteur neuf — et surtout aucune exception")


# --- 3. le barrage -----------------------------------------------------------------------

def un_assistant() -> Assistant:
    a = Assistant.__new__(Assistant)           # sans le __init__ : il charge le VAD et le SDK
    a.occupe = False
    a.micro = type("M", (), {"coupe": False})()
    a.sortie = type("S", (), {"active": False})()
    return a


def les_quatre_raisons_de_se_taire():
    print("\n=== barrage : les quatre verrous gratuits, et leur ordre ===")
    assistant.COUPE.unlink(missing_ok=True)
    a = un_assistant()
    dire(a.barrage() is None, "rien ne bloque : on ecoute")

    a.sortie.active = True
    dire(a.barrage() == "les haut-parleurs jouent quelque chose",
         "les haut-parleurs jouent : on n'envoie pas la visio a Azure")
    a.sortie.active = False

    a.micro.coupe = True
    dire(a.barrage() == "micro coupé au niveau système",
         "micro coupe dans l'OS : ecouter n'aurait aucun sens")
    a.micro.coupe = False

    assistant.COUPE.parent.mkdir(parents=True, exist_ok=True)
    assistant.COUPE.write_text("", encoding="utf-8")
    dire(a.barrage() == "écoute Claude suspendue",
         "le fichier de coupure suspend l'ecoute — et il survit a un redemarrage")

    # La priorite compte : « reponse en cours » doit primer, sinon on empile les requetes.
    a.occupe = True
    dire(a.barrage() == "réponse en cours",
         "une reponse en cours prime sur tout le reste : on n'empile pas les requetes")
    a.occupe = False
    assistant.COUPE.unlink(missing_ok=True)
    dire(a.barrage() is None, "le fichier retire, l'ecoute reprend sans redemarrage")


# --- 4. integration : la chaine complete, sans jamais atteindre Azure --------------------

def rien_n_atteint_azure_quand_rien_ne_doit_partir():
    print("\n=== integration : ce qui ne doit pas etre facture ne part pas ===")
    appels = []

    async def faux_transcrire(pcm):
        appels.append(len(pcm))
        return ""

    vrai = assistant.transcrire
    assistant.transcrire = faux_transcrire
    try:
        a = un_assistant()
        a.plafond = neuf(limite=10.0)
        a.notif_ecoute = type("N", (), {"transitoire": lambda *x, **k: None})()
        a.notif_plafond = None
        a._plafond_annonce = False
        a._alerte_80 = True                    # l'alerte parle, elle n'est pas le sujet ici
        a.conversation = False

        # Trop court : un claquement de porte, un « hm ». Gratuit, donc jamais envoye.
        asyncio.run(a.traiter(b"\0" * 1000, 0.3))
        dire(not appels, "un segment trop court n'est pas transcrit")

        # Trop long : ce n'est plus une question, c'est une conversation captee par erreur.
        asyncio.run(a.traiter(b"\0" * 1000, config.EVEIL_MAX_S + 1))
        dire(not appels, "un segment trop long non plus")

        # Plafond atteint : le refus se fait AVANT l'appel.
        a.plafond.consommer_stt(3600 * 100)
        class _P:
            async def afficher(self, *x): pass
        a.notif_plafond = _P()
        asyncio.run(a.traiter(b"\0" * 1000, 2.0))
        dire(not appels, "plafond atteint : rien ne part, meme pour une duree plausible")
        dire(a._plafond_annonce, "et le refus est ANNONCE — un refus muet passe pour une panne")

        # Et le cas nominal : une duree plausible, du budget, ca part.
        a.plafond = neuf(limite=10.0)
        a._plafond_annonce = False
        asyncio.run(a.traiter(b"\0" * 1000, 2.0))
        dire(len(appels) == 1, "une question de deux secondes avec du budget est transcrite")
        dire(a.plafond.stt_s == 2.0,
             f"et la depense est comptee tout de suite ({a.plafond.stt_s} s)")
        dire(a.occupe is False, "l'assistant se libere meme quand rien n'est compris")
    finally:
        assistant.transcrire = vrai


def main():
    on_reveille_sur_le_nom_pas_sur_l_interjection()
    raccrocher_se_dit_court()
    les_deux_depenses_sont_comptees()
    le_refus_arrive_avant_la_depense()
    un_jour_qui_tourne_archive_au_lieu_d_ecraser()
    la_comptabilite_survit_au_redemarrage()
    un_fichier_illisible_ne_tue_pas_le_service()
    les_quatre_raisons_de_se_taire()
    rien_n_atteint_azure_quand_rien_ne_doit_partir()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
