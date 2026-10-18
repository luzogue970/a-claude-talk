"""Le lancement d'un son ne doit JAMAIS echouer — ce qui s'en approche, et ce qui l'empeche.

Ce fichier existe a cause d'une phrase : « si je quitte la conversation et que je reviens,
ça marche, ce qui est le cas quatre-vingt-dix-neuf pour cent du temps ». Un defaut que
RECHARGER repare est un defaut d'etat : quelque chose s'est corrompu en memoire et rien ne
le nettoie. Les tests ci-dessous couvrent la part serveur de ce chemin.

Rien n'appelle Azure : la synthese est doublee. Un test qui depense de l'argent ne se lance
pas, donc ne protege rien.

    .venv/bin/python voix/test_audio.py
"""

import asyncio
import sys
from pathlib import Path

import aiohttp

# La VRAIE classe, capturee avant qu'on ne la remplace : les tests s'en servent pour parler
# au serveur pendant que le serveur, lui, croit parler a Azure.
_VRAIE_SESSION = aiohttp.ClientSession

import config
from tableau import Tableau, _texte_a_dire, _bornes_demandees

ok = True
# Un MP3 credible : ce qui compte est qu'il depasse le plancher sous lequel le serveur
# refuse de croire que c'est du son.
SON = b"\xff\xfb\x90\x64" + b"\x00" * 4000


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class _Reponse:
    def __init__(self, faux):
        self._f = faux

    async def __aenter__(self):
        self._f.appels += 1
        if self._f.leve is not None:
            raise self._f.leve
        if self._f.retard:
            await asyncio.sleep(self._f.retard)
        return self

    async def __aexit__(self, *_):
        return False

    @property
    def status(self):
        return self._f.status

    async def read(self):
        return self._f.corps


class _Session:
    def __init__(self, faux):
        self._f = faux

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def post(self, *_a, **_k):
        return _Reponse(self._f)


class FauxAzure:
    """Azure sans Azure. `appels` dit combien de fois on l'a vraiment derange."""

    def __init__(self, status=200, corps=SON, retard=0.0, leve=None):
        self.status, self.corps, self.retard, self.leve = status, corps, retard, leve
        self.appels = 0

    def __call__(self, *_a, **_k):
        return _Session(self)


class Decor:
    """Un serveur demarre, une cle Azure feinte, et Azure remplace par un double."""

    def __init__(self, azure=None, port=7897):
        self.azure = azure or FauxAzure()
        self.port = port

    async def __aenter__(self):
        self._cle = config.AZURE_KEY
        config.AZURE_KEY = "une-cle-de-test"
        aiohttp.ClientSession = self.azure
        self.t = Tableau(port=self.port, ouvrir=False)
        self.url = await self.t.demarrer()
        return self

    async def __aexit__(self, *_):
        aiohttp.ClientSession = _VRAIE_SESSION
        config.AZURE_KEY = self._cle
        await self.t.arreter()
        return False

    def client(self):
        return _VRAIE_SESSION()


# ---------------------------------------------------------------- TU : le texte a dire

def une_sortie_de_terminal_se_lit_quand_meme():
    """Les codes de couleur d'un terminal faisaient refuser le SSML ENTIER par Azure.

    `xml.sax.saxutils.escape` n'echappe que &, < et > ; les caracteres de controle, eux,
    sont simplement interdits en XML 1.0. Une reponse qui cite une sortie de commande en
    contient, et la page retombait sur la voix du navigateur sans explication."""
    print("\n=== une reponse qui cite un terminal se lit avec la vraie voix ===")
    texte, _ = _texte_a_dire("j'ai lance \x1b[32mles tests\x1b[0m : 1841 vertes", 6000)
    dire("\x1b" not in texte, "plus un seul caractere que XML refuse")
    dire("les tests" in texte and "1841 vertes" in texte, f"et le propos est intact : {texte!r}")
    dire(_texte_a_dire("\x00\x07\x0b", 6000)[0] == "",
         "un texte qui n'est QUE du controle devient vide, et sera refuse proprement")


def une_longue_reponse_s_arrete_sur_une_phrase():
    """Coupee au caractere pres, la lecture s'arretait au milieu d'un mot.

    A l'oreille, c'est indiscernable d'une coupure reseau : on croit que le son a lache,
    alors que c'est nous qui avons coupe."""
    print("\n=== une tres longue reponse s'arrete sur une phrase, et le dit ===")
    long = ("Les cartes sont refaites. " * 300)[:5000] + "Et voici la toute derniere idee qui depasse."
    texte, tronque = _texte_a_dire(long, 5010)
    dire(tronque is True, "la page apprend que la reponse a ete ecourtee")
    dire(texte.endswith("."), f"et la coupe tombe sur une fin de phrase : ...{texte[-30:]!r}")
    dire(len(texte) <= 5010, f"sans depasser le budget ({len(texte)} caracteres)")

    # Un pave sans la moindre ponctuation : mieux vaut tout garder que n'en dire que le debut.
    brut = "a" * 9000
    coupe, tronque = _texte_a_dire(brut, 6000)
    dire(tronque is True and len(coupe) == 6000,
         "un texte sans aucune phrase est coupe net plutot que reduit a presque rien")

    court, tronque = _texte_a_dire("Trois mots.", 6000)
    dire(court == "Trois mots." and tronque is False,
         "et une reponse normale n'est ni touchee ni annoncee comme ecourtee")


def le_meme_propos_n_est_paye_qu_une_fois():
    """Le cache est indexe par le hachage du texte. « lire ici » envoie le flux brut,
    « écouter » envoie le texte relu depuis la page : les memes mots, d'autres blancs.
    Sans normalisation, le meme propos etait synthetise — donc facture — deux fois."""
    print("\n=== la meme reponse, demandee par deux boutons, n'est synthetisee qu'une fois ===")
    a, _ = _texte_a_dire("Les cartes   sont refaites.\n\n\n\nRelis la deuxieme.", 6000)
    b, _ = _texte_a_dire("Les cartes sont refaites.\n\nRelis la deuxieme.  ", 6000)
    dire(a == b, f"les deux formulations donnent le meme texte : {a!r}")


# ------------------------------------------------------- TU : les plages d'octets d'iOS

def la_sonde_d_ios_recoit_ce_qu_elle_attend():
    """Safari demande « bytes=0-1 » avant tout le reste et exige un 206 : un serveur qui
    rend 200 passe pour ne pas savoir servir du son, et l'element se declare en erreur."""
    print("\n=== les plages d'octets, telles qu'un lecteur les demande vraiment ===")
    T = 1000
    dire(_bornes_demandees("bytes=0-1", T) == (0, 1), "la sonde de Safari : les deux premiers octets")
    dire(_bornes_demandees("bytes=0-", T) == (0, 999), "« tout, depuis le debut »")
    dire(_bornes_demandees("bytes=500-", T) == (500, 999), "une reprise en cours de fichier")
    dire(_bornes_demandees("bytes=-500", T) == (500, 999), "les cinq cents derniers octets")
    dire(_bornes_demandees("bytes=0-99999", T) == (0, 999), "une fin au-dela du fichier se ramene au fichier")
    dire(_bornes_demandees("", T) is None, "sans en-tete, on sert tout")
    dire(_bornes_demandees("bytes=-0", T) == "refus", "« les zero derniers octets » ne se sert pas")
    dire(_bornes_demandees("bytes=5000-6000", T) == "refus", "une plage entierement hors du fichier non plus")


def un_en_tete_abime_ne_fait_plus_echouer_la_lecture():
    """Un Range qu'on ne sait pas lire DOIT etre ignore (RFC 9110 §14.2).

    On rendait 416. Un en-tete parfaitement legal — « bytes=0-1,100-200 », deux morceaux
    d'un coup — suffisait donc a faire declarer l'element audio en erreur et a perdre la
    lecture, alors que le son etait la, entier, pret a partir."""
    print("\n=== un lecteur qui demande mal reçoit quand meme son son ===")
    T = 1000
    dire(_bornes_demandees("bytes=0-1,100-200", T) == (0, 1),
         "deux morceaux demandes d'un coup : on sert le premier, on ne refuse pas")
    for abime in ("bytes=abc-", "bytes=-xyz", "bytes=", "bytes=12", "bytes=--"):
        dire(_bornes_demandees(abime, T) is None, f"« {abime} » est ignore, et tout le son part")


# ------------------------------------------------------------ TI : les routes, de bout en bout

async def un_enregistrement_vide_n_empoisonne_pas_la_reponse():
    """LE defaut durable, et le seul etat serveur qu'un redemarrage reparait.

    Azure rend parfois 200 avec un corps vide. Le seul controle etant `status != 200`, ce
    vide etait mis en cache : la cle est le hachage du texte, donc CETTE reponse-la restait
    muette a chaque essai, jusqu'a ce que quarante autres syntheses l'evincent. Les autres
    reponses marchaient — d'ou « ça rate une fois sur cent »."""
    print("\n=== une reponse muette ne reste pas muette pour toujours ===")
    azure = FauxAzure(status=200, corps=b"")
    async with Decor(azure) as d:
        async with d.client() as s:
            async with s.post(d.url + "/parler/preparer", json={"texte": "Les cartes sont refaites."}) as r:
                dire(r.status == 502, f"aucune cle n'est donnee pour du vide ({r.status})")
        dire(len(d.t._voix_cache) == 0, "et surtout : rien n'est garde en cache")

        # Azure se remet. La MEME phrase doit repartir chez lui, pas resservir le vide.
        azure.status, azure.corps = 200, SON
        async with d.client() as s:
            async with s.post(d.url + "/parler/preparer", json={"texte": "Les cartes sont refaites."}) as r:
                dire(r.status == 200, "la meme phrase redemandee repart chez Azure")
                corps = await r.json()
        dire(corps.get("octets") == len(SON), f"et cette fois il y a du son ({corps.get('octets')} octets)")


async def le_son_prepare_se_sert_par_morceaux():
    """Le chemin nominal, de bout en bout : preparer rend une cle, l'element la joue."""
    print("\n=== preparer puis jouer : le chemin que suit vraiment un iPhone ===")
    async with Decor() as d:
        async with d.client() as s:
            async with s.post(d.url + "/parler/preparer", json={"texte": "Trois mots."}) as r:
                corps = await r.json()
            cle = corps["cle"]
            dire(bool(cle), "une cle est rendue, sans un octet de son")
            dire(corps["tronque"] is False, "et la reponse n'a pas ete ecourtee")

            # La sonde que Safari envoie AVANT de jouer quoi que ce soit.
            async with s.get(d.url + "/parler/" + cle, headers={"Range": "bytes=0-1"}) as r:
                dire(r.status == 206, f"la sonde de Safari obtient un 206 ({r.status})")
                dire(r.headers.get("Content-Range") == f"bytes 0-1/{len(SON)}",
                     f"avec la taille totale : {r.headers.get('Content-Range')}")
                dire(len(await r.read()) == 2, "et exactement les deux octets demandes")

            # Puis l'en-tete abime, qui faisait tout echouer.
            async with s.get(d.url + "/parler/" + cle, headers={"Range": "bytes=0-1,100-200"}) as r:
                dire(r.status in (206, 200), f"un en-tete a deux morceaux ne fait plus 416 ({r.status})")

            async with s.get(d.url + "/parler/" + cle) as r:
                dire(r.status == 200 and len(await r.read()) == len(SON), "et le son entier se sert aussi")

            async with s.get(d.url + "/parler/" + "0" * 64) as r:
                dire(r.status == 404, "une cle inconnue se dit clairement (404)")


async def deux_appuis_coup_sur_coup_ne_paient_qu_une_fois():
    """Deux appareils sur la meme conversation, ou un double appui : les deux passaient le
    test du cache avant que l'un ait ecrit, et appelaient Azure chacun."""
    print("\n=== deux demandes simultanees du meme texte n'appellent Azure qu'une fois ===")
    azure = FauxAzure(retard=0.15)
    async with Decor(azure) as d:
        async def demander():
            async with d.client() as s:
                async with s.post(d.url + "/parler/preparer", json={"texte": "Les cartes sont refaites."}) as r:
                    return r.status, await r.json()

        a, b = await asyncio.gather(demander(), demander())
        dire(azure.appels == 1, f"Azure n'a ete derange qu'une fois ({azure.appels})")
        dire(a[0] == 200 and b[0] == 200, "et les deux demandes sont servies")
        dire(a[1]["cle"] == b[1]["cle"], "avec le meme enregistrement")


async def une_panne_d_azure_se_dit_sans_rien_casser():
    """Trois pannes, trois codes — et rien qui reste casse apres."""
    print("\n=== Azure tombe, se tait, ou manque : la session reste saine ===")
    for azure, attendu, quoi in (
        (FauxAzure(leve=asyncio.TimeoutError()), 504, "Azure ne repond pas a temps"),
        (FauxAzure(leve=OSError("reseau coupe")), 502, "Azure est injoignable"),
        (FauxAzure(status=401), 502, "Azure refuse la cle"),
    ):
        async with Decor(azure) as d:
            async with d.client() as s:
                async with s.post(d.url + "/parler/preparer", json={"texte": "Trois mots."}) as r:
                    dire(r.status == attendu, f"{quoi} : {r.status}")
            dire(len(d.t._voix_cache) == 0, "  et rien de casse n'est garde")
            # Azure revient : la MEME phrase doit marcher tout de suite, sans rien recharger.
            azure.leve, azure.status, azure.corps = None, 200, SON
            async with d.client() as s:
                async with s.post(d.url + "/parler/preparer", json={"texte": "Trois mots."}) as r:
                    dire(r.status == 200, "  et des qu'Azure revient, le son revient aussi")


async def sans_cle_azure_la_page_sait_qu_il_faut_l_autre_voix():
    print("\n=== sans cle Azure, la page l'apprend au lieu de rester muette ===")
    async with Decor() as d:
        config.AZURE_KEY = ""
        async with d.client() as s:
            async with s.post(d.url + "/parler/preparer", json={"texte": "Trois mots."}) as r:
                dire(r.status == 503, f"un 503 explicite ({r.status})")
                dire("navigateur" in (await r.json()).get("erreur", ""),
                     "qui nomme le repli, pour que la page n'ait pas a le deviner")
            async with s.post(d.url + "/parler/preparer", json={}) as r:
                pass
        config.AZURE_KEY = "une-cle-de-test"
        async with d.client() as s:
            async with s.post(d.url + "/parler/preparer", json={"texte": "   "}) as r:
                dire(r.status == 400, f"et « rien a dire » se distingue d'une panne ({r.status})")


async def le_cache_ne_grossit_pas_jusqu_a_faire_fermer_la_session():
    """Quarante entrees de sept minutes d'audio tiendraient cent megaoctets en memoire. Le
    socle ferme les sessions sous pression memoire bien avant d'en arriver la."""
    print("\n=== garder des enregistrements ne fait pas fermer la conversation ===")
    gros = b"\xff\xfb" + b"\x00" * (3 * 1024 * 1024)
    async with Decor(FauxAzure(corps=gros)) as d:
        async with d.client() as s:
            servies = 0
            for i in range(12):
                async with s.post(d.url + "/parler/preparer", json={"texte": f"reponse numero {i}"}) as r:
                    servies += 1 if r.status == 200 else 0
        dire(servies == 12, f"douze longues reponses preparees sans une erreur ({servies})")
        poids = sum(len(m) for m, _ in d.t._voix_cache.values())
        dire(poids <= 20 * 1024 * 1024, f"le cache reste sous vingt megaoctets ({poids // (1024 * 1024)} Mo)")
        dire(len(d.t._voix_cache) >= 1, "sans jamais jeter celui qu'on vient tout juste de preparer")


async def principal():
    une_sortie_de_terminal_se_lit_quand_meme()
    une_longue_reponse_s_arrete_sur_une_phrase()
    le_meme_propos_n_est_paye_qu_une_fois()
    la_sonde_d_ios_recoit_ce_qu_elle_attend()
    un_en_tete_abime_ne_fait_plus_echouer_la_lecture()
    await un_enregistrement_vide_n_empoisonne_pas_la_reponse()
    await le_son_prepare_se_sert_par_morceaux()
    await deux_appuis_coup_sur_coup_ne_paient_qu_une_fois()
    await une_panne_d_azure_se_dit_sans_rien_casser()
    await sans_cle_azure_la_page_sait_qu_il_faut_l_autre_voix()
    await le_cache_ne_grossit_pas_jusqu_a_faire_fermer_la_session()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
