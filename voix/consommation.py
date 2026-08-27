"""Ce qui a ete consomme sur chaque moteur de reconnaissance, et ce qu'il reste.

Le probleme concret : les paliers gratuits sont la seule raison pour laquelle cette chaine
de repli existe, et aucun fournisseur ne dit combien il en reste sans aller voir sa console.
Resultat vecu avec Azure : le quota s'est epuise en pleine session, la transcription est
devenue muette, et rien — ni dans les logs ni dans le tableau — ne disait pourquoi. On a
diagnostique une panne pendant vingt minutes pour un compteur arrive a zero.

Donc on compte nous-memes. Trois decisions valent d'etre expliquees :

**On mesure le temps micro ouvert, pas le nombre de mots.** C'est ce que facturent les
fournisseurs de temps reel : la duree d'audio poussee dans le flux. LiveKit pousse l'audio
tant que l'entree est active, donc « micro ouvert » et « audio envoye » sont la meme chose.

**On ne compte que le moteur ACTIF, et jamais le local.** Dans une chaine de repli, un seul
moteur transcrit a un instant donne ; les autres ne sont pas connectes. Le local ne consomme
rien du tout — il tourne ici.

**C'est une estimation, et elle le dit.** Notre compteur ignore ce qui a ete consomme depuis
une autre machine, un autre outil, ou avant l'installation de ce fichier. Il sert a voir
venir l'epuisement, pas a tenir une comptabilite. Un compteur qui pretend a l'exactitude
qu'il n'a pas est pire qu'une estimation annoncee comme telle : on lui fait confiance au
mauvais moment.

Le mois de reference est le mois calendaire local. Les fournisseurs remettent a zero sur le
cycle de facturation, qui ne commence pas forcement le 1er — donc un quota mensuel peut
paraitre epuise ici alors qu'il est deja revenu chez eux. Le sens de l'erreur est le bon :
on s'alarme trop tot, jamais trop tard.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

FICHIER = Path(os.environ.get("VOIX_CONSO",
                              Path.home() / ".config" / "claude-talk" / "consommation.json"))

# Sous ce seuil, on n'ecrit pas : un aller-retour disque par coupure de micro pour trois
# secondes d'audio n'apporte rien, et l'ecriture atomique a un cout.
PLANCHER_S = 1.0


def _mois() -> str:
    return datetime.now().strftime("%Y-%m")


def _lire() -> dict:
    try:
        d = json.loads(FICHIER.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        # Fichier absent au premier lancement, ou tronque par un arret brutal. Dans les deux
        # cas repartir de zero est plus utile que de refuser de compter.
        return {}


def _ecrire(d: dict) -> None:
    try:
        FICHIER.parent.mkdir(parents=True, exist_ok=True)
        tmp = FICHIER.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(FICHIER)          # atomique : jamais de fichier a moitie ecrit
    except Exception:
        pass                          # compter est un confort ; ca ne doit jamais casser une session


def ajouter(cle: str, secondes: float) -> None:
    """Impute `secondes` d'audio au moteur `cle`."""
    if not cle or cle == "local" or secondes < PLANCHER_S:
        return
    d = _lire()
    e = d.setdefault(cle, {})
    e["cumul"] = round(e.get("cumul", 0.0) + secondes, 1)
    mois = e.setdefault("mois", {})
    mois[_mois()] = round(mois.get(_mois(), 0.0) + secondes, 1)
    # On garde 13 mois : de quoi voir l'annee ecoulee sans laisser le fichier grossir sans fin.
    for vieux in sorted(mois)[:-13]:
        mois.pop(vieux, None)
    e["dernier"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _ecrire(d)


def constater_epuise(cle: str, motif: str = "") -> None:
    """Marque un moteur comme epuise parce qu'il l'a DIT, pas parce qu'on l'a calcule.

    Notre compteur part de zero le jour de son installation : sur Azure, il annoncait
    « 5 h restantes » alors que le quota etait vide depuis des semaines. Un chiffre faux et
    rassurant est le pire des deux mondes. Quand le fournisseur refuse pour cause de quota,
    ce constat prime sur toute estimation, et il est date : au mois suivant, un quota
    renouvelable redevient credible et le constat s'efface tout seul.
    """
    if not cle:
        return
    d = _lire()
    e = d.setdefault(cle, {})
    e["epuise"] = {"mois": _mois(), "motif": motif[:200],
                   "vu": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _ecrire(d)


def oublier_epuise(cle: str) -> None:
    """Le moteur remarche : on retire le constat, sinon il resterait affiche a tort."""
    d = _lire()
    if d.get(cle, {}).pop("epuise", None) is not None:
        _ecrire(d)


# Ce qui, dans un message d'erreur, designe un quota plutot qu'une panne. Distinguer les deux
# compte : une panne se retente, un quota epuise ne se retentera pas avant le mois prochain,
# et les presenter pareil condamne a rejouer indefiniment un moteur qui ne reviendra pas.
MOTIFS_QUOTA = ("quota", "exceeded", "insufficient", "out of credit", "limit reached",
                "403", "429", "payment", "billing", "subscription", "free tier",
                # « 402 Organization balance exhausted » — le refus de Soniox, mesure. La
                # liste ne s'ecrit pas d'imagination : chaque motif vient d'un refus reel
                # rencontre, parce qu'un mot devine ferait passer une panne pour un quota et
                # condamnerait un moteur qui reviendrait tout seul.
                "402", "balance exhausted", "no credit", "top up", "autopay")


def ressemble_a_un_quota(message: str) -> bool:
    bas = (message or "").lower()
    return any(m in bas for m in MOTIFS_QUOTA)


def etat(moteurs) -> list[dict]:
    """Pour chaque moteur : consomme, palier, restant, et de quoi l'afficher.

    `moteurs` est la table de `moteurs_stt` (injectee plutot qu'importee, pour que les tests
    puissent decrire des moteurs fictifs sans toucher la vraie table).
    """
    d = _lire()
    encours_cle, encours_s = en_cours()
    out = []
    for m in moteurs:
        e = d.get(m.cle, {})
        # Un quota mensuel se juge sur le mois courant ; un credit unique sur le cumul de
        # toujours. Comparer un credit au mois courant ferait croire qu'il se recharge.
        consomme = (e.get("mois", {}).get(_mois(), 0.0) if m.renouvelable
                    else e.get("cumul", 0.0))
        consomme += (encours_s if m.cle == encours_cle else 0.0)
        palier_s = (m.quota_h or 0) * 3600 or None
        reste = max(0.0, palier_s - consomme) if palier_s else None
        # Un constat d'epuisement ne vaut que pour la periode ou il a ete fait : un quota
        # mensuel revient le mois suivant, un credit unique ne revient jamais.
        ep = e.get("epuise") or None
        if ep and m.renouvelable and ep.get("mois") != _mois():
            ep = None
        if ep:
            reste = 0.0
        # Le temps NON ENCORE ECRIT sur disque, moteur par moteur. Sans lui, l'affichage
        # reste sur la derniere ecriture et parait fige pendant qu'on parle.
        vif = encours_s if m.cle == encours_cle else 0.0
        out.append({
            "cle": m.cle,
            "en_cours_s": round(vif, 1),
            "libelle": m.libelle,
            "dispo": m.dispo,
            "consomme_s": round(consomme, 1),
            "palier_s": palier_s,
            "reste_s": None if reste is None else round(reste, 1),
            "part": 1.0 if ep else (None if not palier_s
                                    else min(1.0, consomme / palier_s)),
            "epuise": bool(ep),
            "motif": (ep or {}).get("motif") or None,
            "renouvelable": m.renouvelable,
            "gratuit": m.gratuit,
            "dernier": e.get("dernier"),
        })
    return out


def duree(secondes: float | None) -> str:
    """« 2 h 04 », « 37 min », « 45 s » — lisible d'un coup d'oeil, jamais « 7440.0 »."""
    if secondes is None:
        return "—"
    s = int(secondes)
    if s >= 3600:
        return f"{s // 3600} h {(s % 3600) // 60:02d}"
    if s >= 60:
        return f"{s // 60} min"
    return f"{s} s"


class Chrono:
    """Mesure le temps micro ouvert et l'impute au moteur actif au moment de la fermeture.

    Pourquoi une classe plutot qu'un simple `time.monotonic()` a l'appel : le moteur actif
    peut CHANGER pendant que le micro est ouvert (c'est tout l'objet d'une chaine de repli).
    Imputer toute la fenetre au moteur present a la fermeture attribuerait a Deepgram le
    temps consomme par Azure juste avant sa chute. On decoupe donc a chaque bascule.
    """

    def __init__(self):
        self._depuis: float | None = None
        self._cle: str | None = None

    def ouvrir(self, cle: str | None) -> None:
        if self._depuis is not None:
            return                    # deja ouvert : ne pas redemarrer le compteur
        self._depuis, self._cle = time.monotonic(), cle

    def fermer(self) -> None:
        if self._depuis is None:
            return
        ajouter(self._cle or "", time.monotonic() - self._depuis)
        self._depuis, self._cle = None, None

    def basculer(self, cle: str | None) -> None:
        """Le moteur actif a change : on solde le precedent et on repart sur le nouveau."""
        if self._depuis is None:
            self._cle = cle
            return
        ajouter(self._cle or "", time.monotonic() - self._depuis)
        self._depuis, self._cle = time.monotonic(), cle


# Un processus = une conversation = un seul micro et un seul moteur actif a la fois. Un
# singleton de module dit exactement ca, et evite de faire circuler un chrono a travers
# quatre couches (session, agent, tableau, ordres) juste pour qu'elles s'accordent dessus.
CHRONO = Chrono()


def moteur_actif(cle: str | None) -> None:
    """Declare quel moteur transcrit maintenant. A appeler a chaque bascule de la chaine."""
    CHRONO.basculer(cle)


def micro(ouvert: bool) -> None:
    """Suit l'ouverture du micro : c'est la seule chose qui consomme du quota."""
    CHRONO.ouvrir(CHRONO._cle) if ouvert else CHRONO.fermer()


def en_cours() -> tuple[str | None, float]:
    """Le moteur en train de consommer, et depuis combien de secondes.

    Ce que ça repare : le compteur n'ecrit sur disque qu'a la FERMETURE du micro, parce
    qu'ecrire a chaque trame serait absurde. Mais l'affichage lisait ce disque, donc il
    montrait la valeur du dernier micro ferme et rien d'autre — on parlait dix minutes et le
    chiffre ne bougeait pas. On croit le compteur casse alors qu'il mesure bien ; c'est
    l'ecart entre ce qui est mesure et ce qui est MONTRE qui trompe.

    Avec ce couple, la page ajoute elle-meme les secondes ecoulees, sans une requete de plus.
    """
    if CHRONO._depuis is None:
        return None, 0.0
    return CHRONO._cle, max(0.0, time.monotonic() - CHRONO._depuis)
