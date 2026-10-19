"""Le quota : des pourcentages qu'on lit d'un coup d'œil, et qu'on entend sans grincer.

Ce module appelle un endpoint INTERNE et non documenté (`/api/oauth/usage`). Il peut changer
de forme ou disparaître sans prévenir. La règle posée dans son en-tête est donc : « tout
dégrade vers le silence plutôt que de casser la voix ». Les tests ci-dessous vérifient
exactement cela — qu'aucune forme de réponse, même absurde, ne fasse tomber l'agent — et
vérifient aussi le reste, qui est du français lu à voix haute : « 1 h 09 » s'entend
« un h zéro neuf », et ça, seul un test l'attrape.
"""
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

import quota as mq
from quota import ECART_MINIMUM, REPLI_MAX, Quota, _quand, fenetres_du_partage

# Aucun test ne doit lire le vrai relevé partagé du socle : il est frais ou périmé selon
# l'heure, et un test qui change d'avis selon l'heure ne teste rien. Ceux qui en veulent un
# le posent eux-mêmes.
mq.PARTAGE = Path("/ce-fichier-n-existe-pas/socle-usage.json")

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def dans(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


class FauxTableau:
    def __init__(self):
        self.publies = []

    def publier(self, genre, **kw):
        self.publies.append((genre, kw))

    def genres(self):
        return [g for g, _ in self.publies]


# --- 1. le temps restant, ecrit ----------------------------------------------------------

def le_temps_restant_s_ecrit_court():
    print("\n=== reste : ce qu'on lit dans l'en-tete ===")
    dire(Quota.reste(None) == "" and Quota.reste("") == "",
         "pas d'echeance, pas de texte")
    dire(Quota.reste("pas une date") == "",
         "une date illisible ne plante pas : l'endpoint est interne, il peut tout renvoyer")
    dire(Quota.reste(dans(-5)) == "à l'instant", "une fenetre deja echue le dit")
    dire(Quota.reste(dans(42)) == "41 min", f"moins d'une heure en minutes : {Quota.reste(dans(42))}")
    dire(Quota.reste(dans(70)) == "1 h 09", f"au-dela, en heures : {Quota.reste(dans(70))}")
    dire(Quota.reste(dans(60 * 30)) == "1 j 5 h", f"au-dela d'un jour, en jours : {Quota.reste(dans(60*30))}")


def le_temps_restant_se_dit_autrement():
    print("\n=== reste_parle : le MEME delai, mais prononçable ===")
    # Mesure : l'en-tete affiche « 1 h 09 » ; la synthese vocale lisait « un h zero neuf ».
    dire(Quota.reste_parle(dans(70)) == "une heure et 9 minutes",
         f"« 1 h 09 » se dit « une heure et 9 minutes » : {Quota.reste_parle(dans(70))!r}")
    dire(Quota.reste_parle(dans(121)) == "2 heures", f"pluriel : {Quota.reste_parle(dans(121))!r}")
    dire(Quota.reste_parle(dans(1.5)) == "1 minute", "singulier d'une minute")
    dire(Quota.reste_parle(dans(3.5)) == "3 minutes", "pluriel des minutes")
    dire(Quota.reste_parle(dans(60 * 49.5)) == "2 jours et 1 heure",
         f"jours et heures : {Quota.reste_parle(dans(60*49.5))!r}")
    dire(Quota.reste_parle(dans(60 * 48.5)) == "2 jours",
         "et pile deux jours ne traine pas un « et 0 heure »")
    dire(Quota.reste_parle(dans(-3)) == "" and Quota.reste_parle(None) == "",
         "une fenetre echue ne se dit pas : on se tait plutot que de dire une absurdite")
    dire(Quota.reste_parle("n'importe quoi") == "", "une date illisible se tait aussi")


def la_date_de_reset_se_lit_selon_le_jour():
    print("\n=== _quand : aujourd'hui l'heure, demain le jour ===")
    # Une heure FIXE du jour courant, pas « dans 30 minutes » : lance a 23h52 le 19 octobre
    # 2026, la suite tombait parce que l'echeance debordait sur le lendemain. Un test qui
    # echoue selon l'heure a laquelle on le lance n'apprend rien sur le code.
    midi = datetime.now().astimezone().replace(hour=12, minute=30, second=0, microsecond=0)
    aujourdhui = _quand(midi.isoformat())
    dire(aujourdhui == "12:30",
         f"une echeance du jour donne juste l'heure : {aujourdhui!r}")
    plus_tard = _quand(dans(60 * 72))
    dire("/" in plus_tard and plus_tard[:4] in [j[:4] for j in mq.JOURS],
         f"au-dela, le jour et la date : {plus_tard!r}")
    dire(_quand(None) == "" and _quand("zut") == "", "et rien d'illisible ne passe")


# --- 2. ce qu'un tour a coute ------------------------------------------------------------

def l_ecart_d_un_tour_se_dit_meme_quand_il_est_nul():
    print("\n=== dire_ecarts : zero est une information ===")
    dire(Quota.dire_ecarts([]) == "", "pas d'ecart, pas de texte")
    dire(Quota.dire_ecarts([{"cle": "session", "delta": 1, "pct": 31}])
         == "session +1 pt (31 %)", "une hausse porte son signe")
    # Un tour qui n'a rien coute doit le DIRE : un vide laisserait croire a une panne de mesure.
    dire(Quota.dire_ecarts([{"cle": "session", "delta": 0, "pct": 31}])
         == "session ±0 pt (31 %)", "un tour qui n'a rien coute l'affiche quand meme")
    dire(Quota.dire_ecarts([{"cle": "semaine", "delta": -2, "pct": 10}])
         == "semaine -2 pt (10 %)",
         "et une BAISSE est possible : la fenetre glisse, elle ne fait pas que monter")
    dire(" · " in Quota.dire_ecarts([{"cle": "a", "delta": 1, "pct": 2},
                                     {"cle": "b", "delta": 0, "pct": 3}]),
         "plusieurs fenetres se separent lisiblement")


def mesurer_compare_avant_et_apres():
    print("\n=== mesurer : de combien la fenetre a bouge pendant le tour ===")
    q = Quota()
    q._lu_a = mq.time.monotonic()           # lecture fraiche : pas de relecture reseau
    q.fenetres = [{"cle": "session", "pct": 31.0}, {"cle": "semaine", "pct": 12.0}]
    dire(q.instantane() == {"session": 31.0, "semaine": 12.0}, "l'instantane rend les pourcentages")

    ecarts = asyncio.run(q.mesurer({"session": 30.0, "semaine": 12.0}))
    dire([e["delta"] for e in ecarts] == [1.0, 0.0],
         f"les deltas sont justes : {[e['delta'] for e in ecarts]}")
    dire(asyncio.run(q.mesurer({})) == [], "sans point de depart, rien a mesurer")

    # Une fenetre apparue EN COURS de tour n'a pas de point de depart : on l'ignore plutot
    # que d'annoncer un bond de 31 points qui n'a pas eu lieu.
    ecarts = asyncio.run(q.mesurer({"session": 30.0, "disparue": 5.0}))
    dire([e["cle"] for e in ecarts] == ["session"],
         "une fenetre sans equivalent a l'arrivee est ignoree, pas inventee")


# --- 3. les seuils et les resumes --------------------------------------------------------

def un_palier_franchi_laisse_une_trace_une_seule_fois():
    print("\n=== _alerter : une ligne par palier, pas une par lecture ===")
    t = FauxTableau()
    q = Quota(tableau=t)
    q._alerter("session", 30)
    dire(t.publies == [], "sous 50 %, rien : l'en-tete suffit")
    q._alerter("session", 51)
    dire(len(t.publies) == 1 and t.publies[0][0] == "quota_seuil", "a 51 % le palier 50 est marque")
    q._alerter("session", 52)
    q._alerter("session", 60)
    dire(len(t.publies) == 1,
         "rester dans le meme palier ne republie rien — sinon le flux serait noye")
    q._alerter("session", 91)
    dire(len(t.publies) == 2 and "91" in t.publies[1][1]["texte"],
         "mais franchir 90 marque une nouvelle ligne, en sautant 75 au passage")
    q._alerter("semaine", 76)
    dire(len(t.publies) == 3, "et chaque fenetre a son propre compteur de paliers")


def le_resume_se_lit_et_se_dit():
    print("\n=== resume / resume_parle ===")
    q = Quota()
    dire(q.resume() == "indisponible", "sans lecture, l'en-tete le dit")
    dire(q.resume_parle() == "je n'arrive pas à lire le quota",
         "et la voix aussi, plutot que de se taire sans raison")

    q.fenetres = [{"cle": "session", "pct": 31.0, "reset_iso": dans(70)},
                  {"cle": "semaine", "pct": 12.0, "reset_iso": ""}]
    ecrit = q.resume()
    dire("session 31 %" in ecrit and "1 h 09" in ecrit, f"l'ecrit est compact : {ecrit!r}")
    dire("semaine 12 %" in ecrit and ecrit.count("(") == 1,
         "une fenetre sans echeance n'affiche pas de parenthese vide")

    parle = q.resume_parle()
    dire("31 pour cent" in parle, f"« % » se dit « pour cent » : {parle!r}")
    dire("elle se renouvelle dans une heure et 9 minutes" in parle,
         "et le temps restant est dit, parce que c'est lui qui decide de lever le pied")
    dire(parle.endswith("."), "la phrase est ponctuee, sinon la voix ne descend pas")


# --- 4. integration : l'endpoint interne peut tout renvoyer, rien ne doit tomber ----------

class FausseReponse:
    def __init__(self, status, charge):
        self.status = status
        self._charge = charge

    async def json(self):
        if isinstance(self._charge, Exception):
            raise self._charge
        return self._charge

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FausseSession:
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.appels = 0
        self.closed = False

    def get(self, url, **kw):
        self.appels += 1
        r = self.reponses.pop(0) if self.reponses else self.reponses
        if isinstance(r, Exception):
            raise r
        return r

    async def close(self):
        self.closed = True


def avec(q, reponses, jeton="jeton"):
    mq._jeton = lambda: jeton
    q._session = FausseSession(reponses)
    return q._session


CHARGE = {
    "five_hour": {"utilization": 31.0, "resets_at": dans(70)},
    "seven_day": {"utilization": 12.0, "resets_at": dans(60 * 48)},
    "seven_day_opus": None,          # mesure : null sur un siege Team
}


def une_lecture_qui_marche():
    print("\n=== rafraichir : le cas nominal ===")
    t = FauxTableau()
    q = Quota(tableau=t)
    avec(q, [FausseReponse(200, CHARGE)])
    dire(asyncio.run(q.rafraichir(force=True)) is True, "une reponse valide rend True")
    dire([f["cle"] for f in q.fenetres] == ["session", "semaine"],
         "les deux fenetres servies sont gardees, celle a null est ignoree")
    dire(q.fenetres[0]["reset_iso"] and q.fenetres[0]["reset"],
         "l'horodatage ABSOLU part aussi : la page decompte seule, sans un appel de plus")
    dire("quota" in t.genres(), "et le tableau est prevenu")
    dire(q._echecs == 0 and q._lu_a is not None, "le compteur d'echecs est remis a zero")


def aucune_forme_de_reponse_ne_fait_tomber_l_agent():
    print("\n=== rafraichir : l'endpoint est interne, il peut tout renvoyer ===")
    cas = [
        ("une liste au lieu d'un objet", [1, 2, 3]),
        ("des fenetres a null", {"five_hour": None, "seven_day": None}),
        ("une fenetre sans utilization", {"five_hour": {"resets_at": dans(5)}}),
        ("une utilization a null", {"five_hour": {"utilization": None}}),
        ("un objet vide", {}),
    ]
    for nom, charge in cas:
        q = Quota()
        avec(q, [FausseReponse(200, charge)])
        try:
            r = asyncio.run(q.rafraichir(force=True))
            dire(r is False and q.fenetres == [], f"{nom} : silence, pas d'exception")
        except Exception as e:
            dire(False, f"{nom} : a leve {type(e).__name__} — l'agent serait tombe")

    # Une charge illisible (JSON casse) est le cas le plus probable d'un endpoint qui change.
    q = Quota()
    avec(q, [FausseReponse(200, ValueError("json casse"))])
    dire(asyncio.run(q.rafraichir(force=True)) is False, "un JSON casse ne remonte pas")

    q = Quota()
    avec(q, [ConnectionError("reseau coupe")])
    dire(asyncio.run(q.rafraichir(force=True)) is False, "le reseau coupe non plus")


def un_echec_ne_vide_pas_ce_qu_on_savait():
    print("\n=== rafraichir : un pourcentage un peu vieux vaut mieux qu'un blanc ===")
    q = Quota()
    avec(q, [FausseReponse(200, CHARGE), FausseReponse(500, {})])
    asyncio.run(q.rafraichir(force=True))
    connu = list(q.fenetres)
    dire(asyncio.run(q.rafraichir(force=True)) is False, "la seconde lecture echoue")
    dire(q.fenetres == connu,
         "et la derniere lecture connue est GARDEE : l'en-tete ne se vide pas")


def le_repli_protege_l_endpoint_qui_nous_sert():
    print("\n=== rafraichir : 429, et le repli qui double ===")
    t = FauxTableau()
    q = Quota(tableau=t)
    avec(q, [FausseReponse(429, {}), FausseReponse(429, {}), FausseReponse(429, {})])
    depart = mq.time.monotonic()
    asyncio.run(q.rafraichir(force=True))
    premier = q._pas_avant - depart
    asyncio.run(q.rafraichir(force=True))
    second = q._pas_avant - depart
    dire(q._echecs == 2, "les echecs se comptent")
    dire(second > premier * 1.8,
         f"et l'attente DOUBLE ({premier:.0f}s puis {second:.0f}s) : on ne harcele pas l'endpoint")
    # La plainte est publiee une seule fois : le flux est fait pour du signal.
    dire(t.genres().count("log") == 1,
         "le tableau est prevenu UNE fois, pas a chaque reessai")
    q._echecs = 50
    asyncio.run(q.rafraichir(force=True))
    dire(q._pas_avant - mq.time.monotonic() <= REPLI_MAX + 1,
         f"et le repli est plafonne a {REPLI_MAX:.0f}s : on finit toujours par reessayer")


def sans_jeton_on_ne_sort_pas_sur_le_reseau():
    print("\n=== rafraichir : pas de jeton, pas d'appel ===")
    q = Quota()
    s = avec(q, [FausseReponse(200, CHARGE)], jeton=None)
    dire(asyncio.run(q.rafraichir(force=True)) is False, "sans jeton, on rend False")
    dire(s.appels == 0, "et surtout : AUCUN appel reseau n'est parti")
    dire(q._pas_avant > mq.time.monotonic(), "la prochaine tentative est reportee")


def le_garde_fou_vaut_aussi_pour_les_appelants_presses():
    print("\n=== rafraichir : s'auto-limite, quoi qu'on lui demande ===")
    q = Quota()
    s = avec(q, [FausseReponse(200, CHARGE)])
    asyncio.run(q.rafraichir(force=True))
    dire(s.appels == 1, "premier appel : il part")
    asyncio.run(q.rafraichir())          # sans force, juste apres
    dire(s.appels == 1, f"un appel non force juste apres NE part pas (ecart min {ECART_MINIMUM:.0f}s)")
    dire(q._pas_avant > mq.time.monotonic(), "le report est bien pose")


def main():
    le_temps_restant_s_ecrit_court()
    le_temps_restant_se_dit_autrement()
    la_date_de_reset_se_lit_selon_le_jour()
    l_ecart_d_un_tour_se_dit_meme_quand_il_est_nul()
    mesurer_compare_avant_et_apres()
    un_palier_franchi_laisse_une_trace_une_seule_fois()
    le_resume_se_lit_et_se_dit()
    une_lecture_qui_marche()
    aucune_forme_de_reponse_ne_fait_tomber_l_agent()
    un_echec_ne_vide_pas_ce_qu_on_savait()
    le_repli_protege_l_endpoint_qui_nous_sert()
    sans_jeton_on_ne_sort_pas_sur_le_reseau()
    le_garde_fou_vaut_aussi_pour_les_appelants_presses()
    on_lit_ce_que_le_tableau_a_deja_paye()
    sans_tableau_allume_l_agent_se_debrouille()

def on_lit_ce_que_le_tableau_a_deja_paye():
    """Le point d'usage du compte tolere une poignee d'appels par quart d'heure.

    Mesure du 17 octobre 2026 : cinq processus l'interrogeaient toutes les cinq minutes
    — le tableau de bord et quatre agents — soit soixante appels par heure. Le tableau
    vivait en 429 permanent et affichait « 100 % utilise » alors que le compte etait a
    6 %. Chaque agent avait raison tout seul ; l'ensemble avait tort.
    """
    maintenant = datetime.now(timezone.utc)
    ms = maintenant.timestamp() * 1000
    plus_tard = (maintenant + timedelta(hours=3)).isoformat()
    frais = {"horodatage": ms - 60_000, "limites": [
        {"genre": "session", "pourcent": 6, "remiseAZero": plus_tard, "modele": None},
        {"genre": "weekly_scoped", "pourcent": 96, "remiseAZero": plus_tard, "modele": "Fable"},
    ]}

    f = fenetres_du_partage(frais, ms)
    dire(f is not None and len(f) == 2,
             "un releve frais du tableau evite un appel de plus au compte")
    dire(f[0]["cle"] == "session" and f[0]["pct"] == 6,
             "le pourcentage lu est bien celui que le tableau a releve")
    dire(f[1]["cle"] == "semaine Fable",
             "une limite qui ne vaut que pour un modele le dit dans son nom")

    vieux = dict(frais, horodatage=ms - 20 * 60_000)
    dire(fenetres_du_partage(vieux, ms) is None,
             "un releve de vingt minutes ne sert plus : on va le chercher soi-meme")

    # Le defaut du 17 octobre, vu de l'autre bout : afficher un chiffre dont la fenetre
    # est deja retombee a zero fait croire le forfait epuise et arreter de travailler.
    retombe = {"horodatage": ms - 60_000, "limites": [
        {"genre": "session", "pourcent": 100,
         "remiseAZero": (maintenant - timedelta(hours=2)).isoformat(), "modele": None}]}
    dire(fenetres_du_partage(retombe, ms) is None,
             "une fenetre remise a zero depuis deux heures n'affiche plus son ancien 100 %")

    for absurde in (None, [], "", {"limites": []}, {"horodatage": "hier"},
                    {"horodatage": ms, "limites": [None, 3, {"genre": "inconnu"}]},
                    {"horodatage": ms, "limites": [{"genre": "session", "pourcent": None,
                                                    "remiseAZero": "pas une date"}]}):
        try:
            fenetres_du_partage(absurde, ms)
        except Exception as e:
            dire(False, f"un releve partage abime fait tomber l'agent : {absurde!r} -> {e}")
            break
    else:
        dire(True, "un releve partage abime ou vide ne fait jamais tomber l'agent")


def sans_tableau_allume_l_agent_se_debrouille():
    """Le partage est un raccourci, pas une dependance : seul, l'agent doit s'en sortir."""
    q = Quota()
    mq.PARTAGE = Path("/nulle/part/socle-usage.json")
    dire(q._lire_partage() is None,
             "sans releve partage sur le disque, on repart sur l'appel direct")


    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
