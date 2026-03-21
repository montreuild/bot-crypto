"""Utilitaires partagés entre les stratégies.

Ce module centralise les fonctions utilitaires qui étaient dupliquées dans
plusieurs fichiers de stratégies.

Contenu actuel :
  fft_direction() — analyse spectrale FFT extraite de fft_spectral.py et
                    composite_score.py (code identique dans les deux fichiers).
"""
import numpy as np


def fft_direction(
    prices: np.ndarray,
    min_period: int = 5,
    max_period: int = 150,
    top_n: int = 10,
) -> dict:
    """
    Analyse spectrale FFT sur les prix de clôture.

    Étapes :
      1. Détrending logarithmique (log-prix moins tendance linéaire)
      2. Fenêtre de Hanning pour réduire le spectral leakage
      3. FFT réelle (numpy.fft.rfft) — algorithme de Cooley-Tukey
      4. Filtrage des cycles entre min_period et max_period barres
      5. Extraction des top_n cycles par amplitude (énergie)
      6. Phase à la dernière barre → direction (+1/-1) et jours avant retournement

    Retourne un dict avec :
      direction     : +1 haussier, −1 baissier, 0 neutre
      confidence    : |avg_signal| ∈ [0.0, 1.0]
      cycles        : liste des cycles dominants (period, weight_pct, direction,
                      days_to_reversal)
      avg_signal    : signal pondéré brut ∈ [−1.0, +1.0]
      next_reversal : barres estimées avant le prochain retournement
    """
    n = len(prices)
    if n < max(min_period * 3, 30):
        return {
            "direction": 0, "confidence": 0.0,
            "cycles": [], "avg_signal": 0.0, "next_reversal": 0.0,
        }

    # Étape 1 : log + détrending linéaire
    log_p     = np.log(np.maximum(prices, 1e-10))
    t         = np.arange(n)
    coeffs    = np.polyfit(t, log_p, 1)
    detrended = log_p - np.polyval(coeffs, t)

    # Étape 2 : fenêtre de Hanning
    windowed = detrended * np.hanning(n)

    # Étape 3 : FFT réelle
    fft_vals = np.fft.rfft(windowed)
    freqs    = np.fft.rfftfreq(n)
    amps     = np.abs(fft_vals)
    phases   = np.angle(fft_vals)

    # Étape 4 : filtrage sur [min_period, max_period] barres
    with np.errstate(divide="ignore", invalid="ignore"):
        periods_arr = np.where(freqs > 0, 1.0 / freqs, np.inf)
    valid     = (freqs > 0) & (periods_arr >= min_period) & (periods_arr <= max_period)
    valid_idx = np.where(valid)[0]

    if len(valid_idx) == 0:
        return {
            "direction": 0, "confidence": 0.0,
            "cycles": [], "avg_signal": 0.0, "next_reversal": 0.0,
        }

    total_energy = float(np.sum(amps[valid_idx] ** 2))
    if total_energy < 1e-15:
        return {
            "direction": 0, "confidence": 0.0,
            "cycles": [], "avg_signal": 0.0, "next_reversal": 0.0,
        }

    # Étape 5 : top N cycles par amplitude décroissante
    sorted_idx   = valid_idx[np.argsort(amps[valid_idx])[::-1]][:top_n]
    cycles       = []
    weighted_dir = 0.0
    total_weight = 0.0

    for idx in sorted_idx:
        freq_k   = float(freqs[idx])
        period_k = 1.0 / freq_k
        amp_k    = float(amps[idx])
        phase_k  = float(phases[idx])
        weight_k = (amp_k ** 2) / total_energy

        # Étape 6 : phase courante à la dernière barre (position n−1)
        # Signal : A·cos(2π·freq·t + φ)
        # Dérivée : −ω·sin(2π·freq·t + φ)  →  haussier si sin(phase) < 0
        current_phase = 2.0 * np.pi * freq_k * (n - 1) + phase_k
        sin_val       = float(np.sin(current_phase))
        direction_k   = 1.0 if sin_val < 0 else -1.0

        # Estimation du prochain retournement
        phi_norm = current_phase % (2.0 * np.pi)
        if direction_k > 0:   # montée → prochain retournement = pic
            days_rev = ((np.pi - phi_norm) % (2.0 * np.pi)) / (2.0 * np.pi) * period_k
        else:                  # descente → prochain retournement = creux
            days_rev = ((2.0 * np.pi - phi_norm) % (2.0 * np.pi)) / (2.0 * np.pi) * period_k

        cycles.append({
            "period":           round(period_k, 1),
            "weight_pct":       round(weight_k * 100, 1),
            "direction":        int(direction_k),
            "days_to_reversal": round(float(days_rev), 1),
        })

        weighted_dir += direction_k * weight_k
        total_weight += weight_k

    avg_signal    = weighted_dir / total_weight if total_weight > 0 else 0.0
    direction     = 1 if avg_signal > 0.15 else (-1 if avg_signal < -0.15 else 0)
    confidence    = min(abs(avg_signal), 1.0)
    next_reversal = min((c["days_to_reversal"] for c in cycles), default=0.0)

    return {
        "direction":     direction,
        "confidence":    round(confidence, 4),
        "cycles":        cycles,
        "avg_signal":    round(avg_signal, 4),
        "next_reversal": round(float(next_reversal), 1),
    }
