#!/usr/bin/env python3
"""« Hey Claude » : la conversation qui s'ouvre, qui se ferme, et qui ne doit jamais traîner.

Une conversation laissée ouverte **facture chaque phrase ambiante**. C'est la fuite d'argent
la plus bête de ce service : personne ne parle à l'assistant, et pourtant tout ce qui se dit
dans la pièce part chez Azure. Trois chemins la referment, et les trois sont vérifiés ici :
le silence, le mot de fin, et la notification qu'on clique.

Le reste du fichier tient la comptabilité sur plusieurs jours — celle qui répond à « combien
j'ai dépensé jusqu'à présent » — et le verrou de la synthèse vocale, qui coûte **onze fois**
une transcription et ne peut donc pas contourner le compteur.

Rien ici n'appelle Azure : les deux seules fonctions qui dépensent sont doublées, et un test
vérifie explicitement qu'elles ne sont pas appelées quand elles ne doivent pas l'être.
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
logging.getLogger("heyclaude").setLevel(logging.CRITICAL)

import assistant
_bac = tempfile.TemporaryDirectory()
assistant.ETAT = Path(_bac.name) / "assistant.json"
assistant.COUPE = Path(_bac.name) / "ecoute-coupee"

import config
from assistant import Assistant, Plafond

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


# --- doublures -----------------------------------------------------------------------------

class FausseNotif:
    def __init__(self):
        self.dits = []

    def rien(self, raison):
        self.dits.append(("rien", raison))

    def ecoute(self):
        self.dits.append(("ecoute", ""))

    def repond(self, r):
        self.dits.append(("repond", r))

    def reflechit(self, q):
        self.dits.append(("reflechit", q))


class FausseNotifPersistante:
    def __init__(self, clic_apres=None):
        self.affichees = []
        self.fermee = 0
        self._clic = clic_apres

    async def afficher(self, titre, corps):
        self.affichees.append((titre, corps))
        return None

    async def attendre_clic(self):
        if self._clic is None:
            await asyncio.Event().wait()        # ne revient jamais
        await asyncio.sleep(self._clic)

    async def fermer(self):
        self.fermee += 1


class FauxClient:
    def __init__(self, nom="c"):
        self.nom = nom
        self.deconnecte = False

    async def disconnect(self):
        self.deconnecte = True


def un_assistant(limite=100.0) -> Assistant:
    a = Assistant.__new__(Assistant)
    a.plafond = neuf_plafond(limite)
    a.notif = FausseNotif()
    a.notif_conv = FausseNotifPersistante()
    a.notif_plafond = FausseNotifPersistante()
    a.conversation = False
    a._dernier_echange = 0.0
    a._veille_clic = None
    a._plafond_annonce = False
    a._alerte_80 = False
    a.client = FauxClient("origine")
    a.occupe = False
    a.micro = type("M", (), {"coupe": False})()
    a.sortie = type("S", (), {"active": False})()
    return a


def neuf_plafond(limite=100.0) -> Plafond:
    assistant.ETAT.unlink(missing_ok=True)
    return Plafond(limite)


# --- 1. le verrou de la synthèse ------------------------------------------------------------

def parler_passe_toujours_par_le_compteur():
    print("\n=== parler : la synthèse coûte onze fois une transcription ===")
    appels = []

    async def faux_synthetiser(texte):
        appels.append(texte)
        return b"wav"

    async def faux_jouer(wav):
        appels.append("joue")

    vrai_s, vrai_j = assistant.synthetiser, assistant.jouer
    assistant.synthetiser, assistant.jouer = faux_synthetiser, faux_jouer
    try:
        a = un_assistant()
        dire(asyncio.run(a.parler("bonjour")) is True, "une phrase courte passe")
        dire(appels == ["bonjour", "joue"], "elle est bien synthetisee puis jouee")
        dire(a.plafond.tts_car == len("bonjour"),
             "et ses caracteres sont comptes : la parole ne contourne pas le plafond")

        appels.clear()
        dire(asyncio.run(a.parler("")) is False, "une phrase vide ne coute rien")
        dire(appels == [], "et ne part pas chez Azure")

        # Le refus arrive AVANT l'appel : un segment qui ne tient pas est refuse, jamais
        # tronque — sinon le plafond serait depasse de ce qu'on vient de dire.
        a = un_assistant(limite=0.0001)
        appels.clear()
        dire(asyncio.run(a.parler("une réponse un peu longue")) is False,
             "au plafond, la reponse n'est PAS lue")
        dire(appels == [], "et surtout : rien n'est parti chez Azure, donc rien n'est facture")
        dire(a.plafond.tts_car == 0, "le compteur n'a pas bouge non plus")
        dire(any("plafond" in r for _, r in a.notif.dits),
             "mais on est prevenu, sinon le silence passerait pour une panne")
    finally:
        assistant.synthetiser, assistant.jouer = vrai_s, vrai_j


# --- 2. le cycle de vie d'une conversation --------------------------------------------------

def une_conversation_s_ouvre_en_disant_comment_la_fermer():
    print("\n=== ouvrir une conversation ===")
    async def essai():
        a = un_assistant()
        a.notif_conv = FausseNotifPersistante(clic_apres=None)
        await a.demarrer_conversation()
        await asyncio.sleep(0)
        if a._veille_clic:
            a._veille_clic.cancel()
        return a
    a = asyncio.run(essai())
    dire(a.conversation is True, "la conversation est ouverte")
    titre, corps = a.notif_conv.affichees[0]
    dire("clic pour terminer" in corps and "fin" in corps,
         f"et la notification dit les DEUX façons de la fermer : {corps!r}")
    dire(a._dernier_echange > 0,
         "l'horloge d'inactivite demarre : sans elle, le silence ne fermerait rien")


def le_silence_ferme_la_conversation():
    print("\n=== le silence : une conversation ouverte facture la pièce entière ===")

    async def essai(ecoule, occupe=False):
        """Fait tourner la VRAIE surveillance, sans attendre ses cinq secondes.

        Recopier sa condition ici marcherait aujourd'hui et deriverait demain : c'est le
        chemin reel qu'il faut emprunter, pas une copie qui lui ressemble.
        """
        a = un_assistant()
        a.conversation = True
        a.occupe = occupe
        a._dernier_echange = assistant.time.monotonic() - ecoule

        vrai_sleep = assistant.asyncio.sleep
        tours = []

        async def sans_attendre(d):
            tours.append(d)
            if len(tours) > 2:                  # un tour suffit : on arrete la boucle
                raise asyncio.CancelledError
            await vrai_sleep(0)

        assistant.asyncio.sleep = sans_attendre
        try:
            await a.surveiller_inactivite()
        except asyncio.CancelledError:
            pass
        finally:
            assistant.asyncio.sleep = vrai_sleep
        return a

    a = asyncio.run(essai(config.EVEIL_INACTIVITE_S + 1))
    dire(a.conversation is False, "passe le delai, elle se ferme seule")
    dire(a.notif_conv.fermee == 1, "et la notification disparait avec elle")
    dire(any("silence" in r for _, r in a.notif.dits),
         "la raison est dite : une conversation qui se ferme sans raison inquiete")

    a = asyncio.run(essai(config.EVEIL_INACTIVITE_S + 1, occupe=True))
    dire(a.conversation is True,
         "mais JAMAIS pendant qu'une reponse se fabrique : la, le silence est normal")

    a = asyncio.run(essai(1.0))
    dire(a.conversation is True, "et une pause courte ne ferme rien, evidemment")


def fermer_la_notification_ferme_la_conversation():
    print("\n=== le clic : elle a disparu, donc c'est fini ===")
    async def essai():
        a = un_assistant()
        a.notif_conv = FausseNotifPersistante(clic_apres=0.01)
        await a.demarrer_conversation()
        await asyncio.sleep(0.05)
        return a
    a = asyncio.run(essai())
    dire(a.conversation is False, "la notification fermee termine la conversation")
    dire(any("notification" in r for _, r in a.notif.dits),
         "et on sait pourquoi elle s'est fermee")


def fermer_deux_fois_ne_fait_rien_de_plus():
    print("\n=== fermer une conversation déjà fermée ===")
    async def essai():
        a = un_assistant()
        a.conversation = True
        await a.finir_conversation("premiere")
        avant = len(a.notif.dits)
        await a.finir_conversation("seconde")
        return a, avant
    a, avant = asyncio.run(essai())
    dire(len(a.notif.dits) == avant,
         "la seconde fermeture est muette : deux chemins peuvent fermer en meme temps")
    dire(a.notif_conv.fermee == 1, "et la notification n'est fermee qu'une fois")


def repartir_a_zero_construit_avant_de_lacher():
    print("\n=== le contexte remis à zéro : construire, puis lâcher ===")
    async def essai(construction_rate=False):
        a = un_assistant()
        ancien = a.client

        async def demarrer_client():
            if construction_rate:
                raise RuntimeError("Azure injoignable")
            a.client = FauxClient("neuf")
        a.demarrer_client = demarrer_client
        await a._repartir_a_zero()
        return a, ancien

    a, ancien = asyncio.run(essai())
    dire(a.client.nom == "neuf", "un client neuf prend la place")
    # Deconnecter avant de reconnecter laissait ~2,5 s ou self.client pointait sur une
    # session morte : un segment arrivant la aurait echoue.
    dire(ancien.deconnecte is True, "et l'ancien n'est lache qu'ensuite")

    a, ancien = asyncio.run(essai(construction_rate=True))
    dire(a.client is ancien and ancien.deconnecte is False,
         "si la construction rate, on GARDE l'ancien : son contexte vaut mieux que rien")


# --- 3. la comptabilité sur plusieurs jours -------------------------------------------------

def le_cumul_repond_a_combien_j_ai_depense():
    print("\n=== cumul : la question « combien jusqu'à présent » ===")
    p = neuf_plafond()
    hier = (date.today() - timedelta(days=1)).isoformat()
    vieux = (date.today() - timedelta(days=40)).isoformat()
    p.histoire = {hier: {"stt_s": 600.0, "tts_car": 1000},
                  vieux: {"stt_s": 3600.0, "tts_car": 50000}}
    p.consommer_stt(60.0)

    ct_tout, mn_tout, car_tout, n_tout = p.cumul()
    dire(n_tout == 3, f"sans fenetre, tous les jours comptent ({n_tout})")
    dire(abs(mn_tout - (60 + 600 + 3600) / 60) < 0.01, "les minutes s'additionnent")
    dire(car_tout == 51000, "les caracteres aussi")

    ct_7, _, car_7, n_7 = p.cumul(7)
    dire(n_7 == 2, "sur sept jours, le jour d'il y a quarante jours sort")
    dire(car_7 == 1000, "et ses caracteres avec")
    dire(ct_7 < ct_tout, "donc le total sur la fenetre est plus petit")

    # C'est une ESTIMATION a partir des tarifs configures, pas un releve de facturation.
    p2 = neuf_plafond()
    dire(p2.cumul()[3] == 0, "un service qui n'a rien depense compte zero jour, pas un")


def un_jour_sans_trafic_bascule_quand_meme():
    print("\n=== le compteur ne reste pas bloqué sur hier ===")
    p = neuf_plafond()
    p.consommer_stt(120.0)
    hier = (date.today() - timedelta(days=1)).isoformat()
    p.jour = hier                                # le service a tourne sans basculer
    reste = p.reste()
    dire(p.jour == date.today().isoformat(), "la premiere lecture du reste bascule le jour")
    dire(p.stt_s == 0.0 and reste == p.limite,
         "le plafond du jour est libre : sinon il ne se libererait jamais")
    dire(p.histoire.get(hier, {}).get("stt_s") == 120.0,
         "et hier est archive, pas efface")
    dire(json.loads(assistant.ETAT.read_text())["histoire"].get(hier),
         "l'archive est ecrite sur le disque, donc elle survit au redemarrage")


def les_resumes_se_lisent_sans_planter():
    print("\n=== les résumés affichés dans les logs et à la demande ===")
    p = neuf_plafond()
    p.consommer_stt(300.0)
    p.consommer_tts(2000)
    r = p.resume()
    dire("centime(s) dépensés aujourd'hui" in r and "min transcrites" in r,
         f"le resume du jour dit les deux depenses : {r!r}")

    cumul = p.resume_cumul()
    dire(cumul.count("\n") == 3, "le cumul donne quatre lignes : jour, 7 j, 30 j, total")
    dire("depuis le début" in cumul, "dont le total depuis le debut")

    # La projection depend de reglages d'environnement : elle ne doit jamais lever, quelle
    # que soit leur valeur — c'est un affichage, pas un calcul critique.
    vrai_fin = config.AZURE_CREDIT_FIN
    for valeur in ("", "2027-01-01", "pas-une-date"):
        config.AZURE_CREDIT_FIN = valeur
        try:
            texte = p.projection()
            dire("rythme des 7 derniers jours" in texte,
                 f"projection avec VOIX_CREDIT_FIN={valeur!r} : lisible")
        except Exception as e:
            dire(False, f"projection avec {valeur!r} a leve {type(e).__name__}")
    config.AZURE_CREDIT_FIN = "pas-une-date"
    dire("illisible" in p.projection(),
         "et une date illisible le DIT, au lieu de faire semblant de calculer")
    config.AZURE_CREDIT_FIN = vrai_fin


def main():
    parler_passe_toujours_par_le_compteur()
    une_conversation_s_ouvre_en_disant_comment_la_fermer()
    le_silence_ferme_la_conversation()
    fermer_la_notification_ferme_la_conversation()
    fermer_deux_fois_ne_fait_rien_de_plus()
    repartir_a_zero_construit_avant_de_lacher()
    le_cumul_repond_a_combien_j_ai_depense()
    un_jour_sans_trafic_bascule_quand_meme()
    les_resumes_se_lisent_sans_planter()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
