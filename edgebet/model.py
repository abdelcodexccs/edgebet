"""Dixon-Coles (1997) goals model with exponential time decay.

Each team gets an attack and a defence rating. Expected goals:
    home xG = exp(home_adv + attack[home] + defence[away])
    away xG = exp(attack[away] + defence[home])
Goals are Poisson, with the Dixon-Coles `rho` correction for the
low-scoring results (0-0, 1-0, 0-1, 1-1) that plain Poisson gets wrong.
Recent matches count more: weight = exp(-xi * days_ago).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson

MAX_GOALS = 10


def _tau(x, y, lam, mu, rho):
    t = np.ones_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    t[m00] = 1 - lam[m00] * mu[m00] * rho
    t[m01] = 1 + lam[m01] * rho
    t[m10] = 1 + mu[m10] * rho
    t[m11] = 1 - rho
    return np.clip(t, 1e-10, None)


@dataclass
class DixonColes:
    xi: float = 0.0018  # time decay per day (half-life ~ 385 days)
    l2: float = 0.0  # optional ridge penalty on ratings
    teams: list[str] = field(default_factory=list)
    attack: np.ndarray | None = None
    defence: np.ndarray | None = None
    home_adv: float = 0.25
    rho: float = -0.05
    n_matches: dict = field(default_factory=dict)
    _x0: np.ndarray | None = None

    def fit(self, df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> "DixonColes":
        as_of = as_of or df["Date"].max() + pd.Timedelta(days=1)
        teams = sorted(set(df["HomeTeam"]) | set(df["AwayTeam"]))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        h = df["HomeTeam"].map(idx).to_numpy()
        a = df["AwayTeam"].map(idx).to_numpy()
        x = df["FTHG"].to_numpy()
        y = df["FTAG"].to_numpy()
        days = (as_of - df["Date"]).dt.days.to_numpy()
        w = np.exp(-self.xi * days)
        if "Weight" in df:  # e.g. friendlies count less than competitive games
            w = w * df["Weight"].to_numpy(float)
        hm = 1.0 - df["Neutral"].to_numpy(float) if "Neutral" in df else np.ones(len(df))  # no home edge on neutral ground
        const = gammaln(x + 1) + gammaln(y + 1)

        m00 = (x == 0) & (y == 0)
        m01 = (x == 0) & (y == 1)
        m10 = (x == 1) & (y == 0)
        m11 = (x == 1) & (y == 1)

        W = w.sum()  # normalise so the scale of the problem doesn't depend on its size

        def nll(p):
            """Weighted negative log-likelihood (per match) and its exact gradient."""
            att, dfn, home, rho = p[:n], p[n:2 * n], p[2 * n], p[2 * n + 1]
            lam = np.exp(np.clip(home * hm + att[h] + dfn[a], -6, 4))
            mu = np.exp(np.clip(att[a] + dfn[h], -6, 4))
            tau = _tau(x, y, lam, mu, rho)
            ll = x * np.log(lam) - lam + y * np.log(mu) - mu - const + np.log(tau)
            pen = 10.0 * att.sum() ** 2 + self.l2 * (att @ att + dfn @ dfn)

            # derivatives of tau w.r.t. lam, mu, rho (only low scores are affected)
            dl, dm, dr = np.zeros_like(lam), np.zeros_like(lam), np.zeros_like(lam)
            dl[m00], dm[m00], dr[m00] = -mu[m00] * rho, -lam[m00] * rho, -lam[m00] * mu[m00]
            dl[m01], dr[m01] = rho, lam[m01]
            dm[m10], dr[m10] = rho, mu[m10]
            dr[m11] = -1.0
            gl = w * (x - lam + lam * dl / tau)   # d ll / d log(lam)
            gm = w * (y - mu + mu * dm / tau)     # d ll / d log(mu)
            g_att = np.bincount(h, gl, n) + np.bincount(a, gm, n)
            g_def = np.bincount(a, gl, n) + np.bincount(h, gm, n)
            grad = -np.concatenate([g_att, g_def, [(gl * hm).sum()], [(w * dr / tau).sum()]]) / W
            grad[:n] += 20.0 * att.sum() + 2 * self.l2 * att
            grad[n:2 * n] += 2 * self.l2 * dfn
            return -(w * ll).sum() / W + pen, grad

        # warm start from the previous fit when the team list is unchanged
        if self._x0 is not None and len(self._x0) == 2 * n + 2 and teams == self.teams:
            x0 = self._x0
        else:
            x0 = np.concatenate([np.zeros(2 * n), [0.25, -0.05]])
        bounds = [(None, None)] * (2 * n) + [(None, None), (-0.3, 0.3)]
        res = minimize(nll, x0, jac=True, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 5000, "ftol": 1e-12, "gtol": 1e-8})
        p = res.x
        self.teams, self._x0 = teams, p
        self.attack, self.defence = p[:n], p[n:2 * n]
        self.home_adv, self.rho = float(p[2 * n]), float(p[2 * n + 1])
        counts = pd.concat([df["HomeTeam"], df["AwayTeam"]]).value_counts()
        self.n_matches = counts.to_dict()
        return self

    def expected_goals(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        i, j = self.teams.index(home), self.teams.index(away)
        lam = np.exp((0.0 if neutral else self.home_adv) + self.attack[i] + self.defence[j])
        mu = np.exp(self.attack[j] + self.defence[i])
        return float(lam), float(mu)

    def score_matrix(self, home: str, away: str, neutral: bool = False) -> np.ndarray:
        lam, mu = self.expected_goals(home, away, neutral)
        g = np.arange(MAX_GOALS + 1)
        m = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
        m[0, 0] *= 1 - lam * mu * self.rho
        m[0, 1] *= 1 + lam * self.rho
        m[1, 0] *= 1 + mu * self.rho
        m[1, 1] *= 1 - self.rho
        return m / m.sum()

    def predict(self, home: str, away: str, neutral: bool = False) -> dict:
        m = self.score_matrix(home, away, neutral)
        lam, mu = self.expected_goals(home, away, neutral)
        total = np.add.outer(np.arange(MAX_GOALS + 1), np.arange(MAX_GOALS + 1))
        return {
            "home": float(np.tril(m, -1).sum()),
            "draw": float(np.trace(m)),
            "away": float(np.triu(m, 1).sum()),
            "over25": float(m[total > 2.5].sum()),
            "xg_home": lam,
            "xg_away": mu,
        }

    def knows(self, team: str, min_matches: int = 0) -> bool:
        return team in self.teams and self.n_matches.get(team, 0) >= min_matches

    def ratings(self) -> pd.DataFrame:
        return (pd.DataFrame({"team": self.teams, "attack": self.attack,
                              "defence": self.defence})
                .assign(strength=lambda d: d.attack - d.defence)
                .sort_values("strength", ascending=False)
                .reset_index(drop=True))
