"""Rate-limit windows as percentages, the way Claude Code's /usage shows them.

Dollars are meaningless on a company seat, so the header reports what actually constrains
the work: how much of the 5-hour window and of the week is spent.

The SDK's own RateLimitEvent does not carry a percentage — measured: `utilization` comes
back None and the payload holds only status, resetsAt and overage flags. The numbers live
behind `/api/oauth/usage`, which the CLI itself calls with the OAuth token from
~/.claude/.credentials.json.

That endpoint is internal and undocumented: it can change shape or disappear without
notice. Everything here therefore degrades to silence rather than breaking the voice — a
missing percentage is an inconvenience, a crashed agent is not.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"

# Only the windows worth a glance. seven_day_opus is null on a Team seat — the weekly
# bucket covers every model there — so it is shown only when the account reports it.
FENETRES = [
    ("five_hour", "5h"),
    ("seven_day", "semaine"),
    ("seven_day_opus", "semaine Opus"),
]
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
PALIERS = (50, 75, 90)


def _jeton() -> str | None:
    """Re-read every poll: Claude Code refreshes this file, so a cached token goes stale."""
    try:
        return json.loads(CREDENTIALS.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    except Exception:
        return None


def _quand(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return ""
    maintenant = datetime.now(timezone.utc).astimezone()
    if t.date() == maintenant.date():
        return t.strftime("%H:%M")
    return f"{JOURS[t.weekday()]} {t.strftime('%d/%m')}"


# Five minutes: the windows move slowly, and this endpoint has a rate limit of its own —
# polling it every minute earned a 429 during development, which is exactly the failure that
# leaves the header blank when you most want the number.
INTERVALLE = 300.0
ECART_MINIMUM = 60.0   # even a post-turn refresh will not call more often than this
REPLI_MAX = 1800.0


class Quota:
    def __init__(self, tableau=None, intervalle: float = INTERVALLE):
        self.tableau = tableau
        self.intervalle = intervalle
        self.fenetres: list[dict] = []
        self._franchi: dict[str, int] = {}
        self._session: aiohttp.ClientSession | None = None
        self._pas_avant = 0.0
        self._echecs = 0
        self._plainte = False
        # Quand la derniere lecture reussie a eu lieu. Mesurer le cout d'un tour avec une
        # lecture vieille de cinq minutes donnerait un chiffre qui ne veut rien dire.
        self._lu_a: float | None = None

    def _reporter(self, secondes: float):
        self._pas_avant = time.monotonic() + secondes

    async def rafraichir(self, force: bool = False) -> bool:
        """Self-throttling: callers may ask as often as they like. On failure the last known
        reading is kept rather than cleared — a slightly stale percentage beats a blank."""
        if not force and time.monotonic() < self._pas_avant:
            return bool(self.fenetres)
        jeton = _jeton()
        if not jeton:
            self._reporter(ECART_MINIMUM)
            return False
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.get(URL, timeout=aiohttp.ClientTimeout(total=20), headers={
                "Authorization": f"Bearer {jeton}",
                "anthropic-beta": "oauth-2025-04-20",
                "anthropic-version": "2023-06-01",
                "Accept": "application/json",
            }) as r:
                if r.status == 429:
                    self._echecs += 1
                    self._reporter(min(ECART_MINIMUM * 2 ** self._echecs, REPLI_MAX))
                    self._signaler("quota limité par l'API, nouvelle tentative plus tard")
                    return False
                if r.status != 200:
                    self._echecs += 1
                    self._reporter(min(ECART_MINIMUM * 2 ** self._echecs, REPLI_MAX))
                    self._signaler(f"quota indisponible (HTTP {r.status})")
                    return False
                donnees = await r.json()
        except Exception:
            self._echecs += 1
            self._reporter(min(ECART_MINIMUM * 2 ** self._echecs, REPLI_MAX))
            return False

        self._echecs = 0
        self._plainte = False
        self._lu_a = time.monotonic()
        self._reporter(ECART_MINIMUM)

        fenetres = []
        for cle, libelle in FENETRES:
            bloc = donnees.get(cle)
            if not isinstance(bloc, dict) or bloc.get("utilization") is None:
                continue
            pct = float(bloc["utilization"])
            fenetres.append({"cle": libelle, "pct": pct, "reset": _quand(bloc.get("resets_at"))})
            self._alerter(libelle, pct)
        self.fenetres = fenetres
        if self.tableau and fenetres:
            self.tableau.publier("quota", fenetres=fenetres)
        return bool(fenetres)

    def _signaler(self, message: str):
        """Complain once, not on every retry: the dashboard is for signal."""
        if self.tableau and not self._plainte:
            self._plainte = True
            self.tableau.publier("log", niveau="WARNING", source="quota", texte=message)

    def _alerter(self, libelle: str, pct: float):
        """One line in the stream each time a window crosses a threshold, so a run that ate
        the quota leaves a trace instead of only a header that silently moved."""
        atteint = max((p for p in PALIERS if pct >= p), default=0)
        if atteint > self._franchi.get(libelle, 0):
            self._franchi[libelle] = atteint
            if self.tableau:
                self.tableau.publier(
                    "quota_seuil", texte=f"{libelle} : {pct:.0f} % de la fenêtre consommés"
                )

    # --- ce qu'un tour a coute -----------------------------------------------
    # L'utilisation revient en POINTS ENTIERS (mesure : 30.0, 11.0 — jamais 30.4). La
    # granularite est donc d'un point de pourcentage, ce qui a deux consequences qu'il faut
    # assumer plutot que masquer :
    #
    # - un tour court affichera souvent « ±0 » : ce n'est pas une panne de mesure, c'est que
    #   le tour a coute moins d'un point ;
    # - la fenetre est PARTAGEE. Une autre session Claude Code, ou l'extension VS Code,
    #   consomme la meme. Ce qu'on mesure est donc « de combien la fenetre a bouge pendant ce
    #   tour », pas « ce que ce tour a coute ». Le libelle dit « fenetre », pas « cout », et
    #   c'est deliberé.
    AGE_MAX = 20.0   # au-dela, on relit avant de conclure

    def instantane(self) -> dict[str, float]:
        """Les pourcentages actuels, par libelle de fenetre."""
        return {f["cle"]: f["pct"] for f in self.fenetres}

    async def mesurer(self, depart: dict[str, float]) -> list[dict]:
        """De combien les fenetres ont bouge depuis `depart`.

        Relit si la derniere lecture est trop vieille — mais en passant par rafraichir(), qui
        garde son propre garde-fou anti-429 : mieux vaut un ecart non mesure qu'un endpoint
        qui nous ferme la porte et une en-tete vide pour le reste de la session.
        """
        if not depart:
            return []
        vieux = self._lu_a is None or (time.monotonic() - self._lu_a) > self.AGE_MAX
        if vieux:
            await self.rafraichir(force=True)
        arrivee = self.instantane()
        ecarts = []
        for cle, avant in depart.items():
            apres = arrivee.get(cle)
            if apres is None:
                continue
            ecarts.append({"cle": cle, "delta": round(apres - avant, 1), "pct": apres})
        return ecarts

    @staticmethod
    def dire_ecarts(ecarts: list[dict]) -> str:
        """« 5h +1 pt (31 %) ». Une fenetre qui n'a pas bouge le dit aussi : savoir qu'un tour
        n'a rien coute est une information, pas un vide."""
        bouts = []
        for e in ecarts:
            d = e["delta"]
            signe = f"+{d:g}" if d > 0 else ("±0" if d == 0 else f"{d:g}")
            bouts.append(f"{e['cle']} {signe} pt ({e['pct']:.0f} %)")
        return " · ".join(bouts)

    def resume(self) -> str:
        if not self.fenetres:
            return "indisponible"
        return " · ".join(f"{f['cle']} {f['pct']:.0f} %" for f in self.fenetres)

    def resume_parle(self) -> str:
        """"5h 1 %" reads badly out loud; spell it."""
        if not self.fenetres:
            return "je n'arrive pas à lire le quota"
        dits = {"5h": "la fenêtre de cinq heures", "semaine": "la semaine",
                "semaine Opus": "la semaine Opus"}
        bouts = [f"{dits.get(f['cle'], f['cle'])} est à {f['pct']:.0f} pour cent"
                 for f in self.fenetres]
        return " et ".join(bouts) + "."

    async def boucle(self):
        while True:
            await asyncio.sleep(self.intervalle)
            await self.rafraichir()

    async def fermer(self):
        if self._session and not self._session.closed:
            await self._session.close()
