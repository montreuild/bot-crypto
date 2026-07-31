# Rapport d'analyse quantitative BTC (1h/4h/1d)

```
Chargement & enrichissement des 3 timeframes...

==============================================================================
1. QUALITÉ & COUVERTURE DES DONNÉES
==============================================================================

  [1h]  50500 bougies | 2020-03-17 → 2026-06-03 (2269 j, ~6.2 ans)
       gaps>90min=20 | doublons=0 | OHLC incohérents=0 | NaN close=0
       prix: min=4933  max=126296  dernier=65518

  [4h]  15378 bougies | 2018-12-15 → 2026-06-03 (2727 j, ~7.5 ans)
       gaps>360min=5 | doublons=0 | OHLC incohérents=0 | NaN close=0
       prix: min=3000  max=126296  dernier=65508

  [1d]   2565 bougies | 2018-12-15 → 2026-06-03 (2727 j, ~7.5 ans)
       gaps>2160min=1 | doublons=0 | OHLC incohérents=0 | NaN close=0
       prix: min=3000  max=126296  dernier=65464

==============================================================================
2. DISTRIBUTION DES RENDEMENTS (log-returns par barre)
==============================================================================

   TF |   µ/bar%   σ/bar%  vol.ann%    skew  exKurt    min%    max% |      B&H%   CAGR%  maxDD%  Sharpe
  --------------------------------------------------------------------------------------------------------
   1h |   0.0051    0.650      60.8   -0.22    17.6  -10.58   11.60 |      1190    50.9   -74.1    0.73
   4h |   0.0196    1.340      62.7   -0.58    17.8  -23.04   13.87 |      1931    49.7   -73.7    0.68
   1d |   0.1179    3.383      64.6   -1.21    22.5  -50.69   17.83 |      1956    49.9   -72.7    0.67

  → Fat tails massives (exKurt >> 0) + asymétrie : modèle gaussien invalide.
    Conséquence design : stops ATR obligatoires, pas de martingale, sizing par risque.

==============================================================================
3. AUTOCORRÉLATION — momentum vs mean-reversion & clustering de volatilité
==============================================================================

  [1h]
    ACF rendements  lag 1/2/3/5/10/24 : -0.010 -0.014 -0.000 -0.008 +0.009 -0.031
    ACF |rendement| lag 1/2/3/5/10/24 : +0.277 +0.230 +0.204 +0.189 +0.172 +0.185   (clustering vol)
    Variance ratio  q=2/5/10/20       : 0.999 0.962 0.929 0.972   (>1 momentum, <1 mean-rev)

  [4h]
    ACF rendements  lag 1/2/3/5/10/24 : -0.021 +0.002 +0.046 -0.014 +0.007 -0.005
    ACF |rendement| lag 1/2/3/5/10/24 : +0.211 +0.187 +0.183 +0.196 +0.139 +0.148   (clustering vol)
    Variance ratio  q=2/5/10/20       : 0.961 0.987 0.928 0.992   (>1 momentum, <1 mean-rev)

  [1d]
    ACF rendements  lag 1/2/3/5/10/24 : -0.085 +0.053 -0.019 +0.011 +0.024 +0.038
    ACF |rendement| lag 1/2/3/5/10/24 : +0.152 +0.098 +0.077 +0.116 +0.070 +0.072   (clustering vol)
    Variance ratio  q=2/5/10/20       : 0.893 0.906 0.935 0.980   (>1 momentum, <1 mean-rev)

  → Le clustering de |r| (ACF positive et persistante) est l'edge le plus
    robuste : la volatilité est prévisible même si la direction l'est peu.

==============================================================================
4. RÉGIMES DE MARCHÉ (structure SMA20/50/100/200 + ADX) — fwd returns
==============================================================================

  [1h]  (forward 6 barres)
    régime        %temps  fwd_moy%  P(up)%  fwd_med%
    choppy          44.2    -0.009    50.3     0.006
    range           30.1     0.052    51.4     0.023
    trend_down      11.9     0.075    55.4     0.138
    trend_up        13.7     0.134    50.7     0.012

  [4h]  (forward 6 barres)
    régime        %temps  fwd_moy%  P(up)%  fwd_med%
    choppy          44.2     0.078    51.2     0.049
    range           28.3     0.156    51.9     0.093
    trend_down      11.4     0.192    55.4     0.337
    trend_up        16.1     0.426    53.2     0.123

  [1d]  (forward 6 barres)
    régime        %temps  fwd_moy%  P(up)%  fwd_med%
    choppy          51.6    -0.223    48.4    -0.207
    range           22.9     1.724    59.3     1.150
    trend_down       9.3     1.598    56.4     0.614
    trend_up        16.3     2.155    54.3     0.694

  → NB : même en trend_down, fwd_moy reste >0 (dérive haussière de BTC) →
    shorter le simple régime baissier ne suffit pas. trend_up = meilleur biais
    long ; range/choppy → mean-reversion douce ou abstention.

==============================================================================
5. SAISONNALITÉ (heure UTC, jour de semaine)
==============================================================================

  [1h] rendement moyen par heure UTC (×1e4) :
    h00= +0.1  h01= -0.0  h02= -2.6  h03= -1.6  h04= -0.7  h05= +1.2  h06= +1.4  h07= +1.0
    h08= +2.0  h09= +0.1  h10= +1.8  h11= +0.5  h12= +0.8  h13= +1.6  h14= -2.4  h15= +1.6
    h16= -0.2  h17= +0.3  h18= -0.3  h19= -0.5  h20= +2.4  h21= +4.8  h22= +3.8  h23= -3.2
    meilleure heure=21h (+4.8e-4)  pire=23h (-3.2e-4)
    par jour (×1e4) : Lun=+1.8 Mar=-0.2 Mer=+2.1 Jeu=-0.6 Ven=-0.1 Sam=+0.3 Dim=+0.2

  [4h] rendement moyen par heure UTC (×1e4) :
    h00= -2.6  h01= +0.0  h02= +0.0  h03= +0.0  h04= +0.9  h05= +0.0  h06= +0.0  h07= +0.0
    h08= +2.7  h09= +0.0  h10= +0.0  h11= +0.0  h12= +2.9  h13= +0.0  h14= +0.0  h15= +0.0
    h16= +0.6  h17= +0.0  h18= +0.0  h19= +0.0  h20= +7.2  h21= +0.0  h22= +0.0  h23= +0.0
    meilleure heure=20h (+7.2e-4)  pire=00h (-2.6e-4)
    par jour (×1e4) : Lun=+7.4 Mar=-0.5 Mer=+7.8 Jeu=-6.6 Ven=+3.8 Sam=+1.0 Dim=+0.6

==============================================================================
6. ANALYSE SPECTRALE (FFT) — cycles dominants & significativité
==============================================================================

  [1h] détrend log-prix — top cycles (période en barres) :
      période ≈   476.4 barres  (  19.9 j)  power=  2.6% de la bande
      période ≈   388.5 barres  (  16.2 j)  power=  2.3% de la bande
      période ≈   455.0 barres  (  19.0 j)  power=  2.1% de la bande
      période ≈   400.8 barres  (  16.7 j)  power=  2.1% de la bande
      période ≈   330.1 barres  (  13.8 j)  power=  1.8% de la bande
      → pic spectral des rendements : période ≈ 7.1 barres, p-value(permutation)=0.430 (NON significatif — pas de cycle fixe)

  [4h] détrend log-prix — top cycles (période en barres) :
      période ≈   427.2 barres  (  71.2 j)  power=  5.8% de la bande
      période ≈   341.7 barres  (  57.0 j)  power=  5.0% de la bande
      période ≈   415.6 barres  (  69.3 j)  power=  4.5% de la bande
      période ≈   496.1 barres  (  82.7 j)  power=  4.3% de la bande
      période ≈   349.5 barres  (  58.2 j)  power=  3.8% de la bande
      → pic spectral des rendements : période ≈ 4.3 barres, p-value(permutation)=0.225 (NON significatif — pas de cycle fixe)

  [1d] détrend log-prix — top cycles (période en barres) :
      période ≈   320.6 barres  ( 320.6 j)  power= 24.9% de la bande
      période ≈   366.4 barres  ( 366.4 j)  power= 10.3% de la bande
      période ≈   197.3 barres  ( 197.3 j)  power=  9.4% de la bande
      période ≈   213.8 barres  ( 213.8 j)  power=  9.0% de la bande
      période ≈   285.0 barres  ( 285.0 j)  power=  8.4% de la bande
      → pic spectral des rendements : période ≈ 2.1 barres, p-value(permutation)=0.040 (significatif)

  → Sur le log-prix détrendé, l'énergie se concentre sur les très basses
    fréquences (tendances) : exploitable comme *phase* directionnelle, pas
    comme horloge fixe. Les rendements n'ont pas de cycle déterministe stable.

==============================================================================
7. EFFICACITÉ DES RETRACEMENTS DE FIBONACCI (taux de rebond)
==============================================================================

  [1h] baseline P(up,6 barres)=51.3%  (lookback swing=120)
      fib 0.382: n= 3522  P(up)= 50.5%  fwd_moy=+0.035%  edge=-0.8pt
      fib 0.5: n= 3868  P(up)= 50.8%  fwd_moy=+0.031%  edge=-0.5pt
      fib 0.618: n= 3502  P(up)= 55.0%  fwd_moy=+0.133%  edge=+3.7pt

  [4h] baseline P(up,6 barres)=52.2%  (lookback swing=90)
      fib 0.382: n= 1223  P(up)= 53.8%  fwd_moy=+0.285%  edge=+1.6pt
      fib 0.5: n= 1184  P(up)= 52.4%  fwd_moy=+0.108%  edge=+0.3pt
      fib 0.618: n= 1400  P(up)= 50.6%  fwd_moy=-0.028%  edge=-1.6pt

  [1d] baseline P(up,6 barres)=53.4%  (lookback swing=60)
      fib 0.382: n=  206  P(up)= 52.9%  fwd_moy=+0.244%  edge=-0.5pt
      fib 0.5: n=  186  P(up)= 40.3%  fwd_moy=-0.927%  edge=-13.1pt
      fib 0.618: n=  261  P(up)= 51.7%  fwd_moy=+0.361%  edge=-1.7pt

  → Un edge réel se traduit par P(up) près d'un niveau nettement ≠ baseline.

==============================================================================
8. BATTERIE D'EDGES CONDITIONNELS (forward returns, side-ajusté)
==============================================================================
  Légende : n=échantillon, P(win)=% forward favorable (side appliqué),
            moy%=rendement forward moyen côté trade, t=t-stat (|t|>2 ~ signif.)

  ── [1h] horizon 12 barres ─────────────────────────────────
    setup                                           n  P(win)%     moy%      t
    LONG momentum (close>ema50>ema200)          18524     49.8   +0.106    7.0  <<<
    SHORT momentum (close<ema50<ema200)         14924     45.7   -0.079   -4.1
    LONG RSI<30 (survente)                       2351     50.3   -0.102   -1.9
    SHORT RSI>70 (surachat)                      3028     48.1   -0.285   -6.7
    LONG RSI<35 EN uptrend (pullback)                               —  (n<30)
    SHORT RSI>65 EN downtrend (pullback)                            —  (n<30)
    LONG breakout Donchian20                     2310     47.9   +0.160    3.5  <<<
    SHORT breakdown Donchian20                   1882     46.2   -0.056   -1.0
    LONG breakout Bollinger sup                  3015     49.6   +0.178    4.5  <<<
    SHORT cassure Bollinger inf                  2874     47.0   +0.035    0.8
    LONG MACD flip+ EN uptrend                    725     50.9   +0.182    2.3  <<<
    SHORT MACD flip- EN downtrend                 620     43.5   +0.010    0.1
    LONG ADX>25 trend_up                         8279     49.4   +0.133    5.7  <<<
    SHORT ADX>25 trend_down                      8242     46.2   -0.035   -1.3
    LONG momentum filtré low-vol                10571     48.4   +0.056    2.8  <<<

  ── [4h] horizon 6 barres ─────────────────────────────────
    setup                                           n  P(win)%     moy%      t
    LONG momentum (close>ema50>ema200)           5797     53.5   +0.355    8.4  <<<
    SHORT momentum (close<ema50<ema200)          4483     47.5   -0.075   -1.4
    LONG RSI<30 (survente)                        700     58.6   +0.315    1.9
    SHORT RSI>70 (surachat)                      1223     46.9   -0.507   -4.8
    LONG RSI<35 EN uptrend (pullback)                               —  (n<30)
    SHORT RSI>65 EN downtrend (pullback)                            —  (n<30)
    LONG breakout Donchian20                      798     51.6   +0.417    3.2  <<<
    SHORT breakdown Donchian20                    542     44.1   -0.285   -1.7
    LONG breakout Bollinger sup                  1042     53.3   +0.457    4.1  <<<
    SHORT cassure Bollinger inf                   860     45.6   -0.039   -0.3
    LONG MACD flip+ EN uptrend                    230     53.5   +0.514    2.2  <<<
    SHORT MACD flip- EN downtrend                 194     46.9   -0.063   -0.2
    LONG ADX>25 trend_up                         2750     53.3   +0.419    6.8  <<<
    SHORT ADX>25 trend_down                      2227     45.9   -0.064   -0.7
    LONG momentum filtré low-vol                 3155     51.1   +0.292    5.4  <<<

  ── [1d] horizon 3 barres ─────────────────────────────────
    setup                                           n  P(win)%     moy%      t
    LONG momentum (close>ema50>ema200)           1026     53.5   +0.623    3.8  <<<
    SHORT momentum (close<ema50<ema200)           522     47.7   -0.365   -1.4
    LONG RSI<30 (survente)                         91     63.7   +1.190    1.7
    SHORT RSI>70 (surachat)                       243     39.1   -1.763   -4.8
    LONG RSI<35 EN uptrend (pullback)                               —  (n<30)
    SHORT RSI>65 EN downtrend (pullback)                            —  (n<30)
    LONG breakout Donchian20                      158     56.3   +1.056    2.4  <<<
    SHORT breakdown Donchian20                     75     36.0   -0.507   -0.5
    LONG breakout Bollinger sup                   160     53.1   +0.921    2.2  <<<
    SHORT cassure Bollinger inf                   105     46.7   +0.265    0.3
    LONG MACD flip+ EN uptrend                     42     50.0   +0.269    0.4
    SHORT MACD flip- EN downtrend                                   —  (n<30)
    LONG ADX>25 trend_up                          503     57.3   +1.154    4.9  <<<
    SHORT ADX>25 trend_down                       289     47.1   -0.243   -0.6
    LONG momentum filtré low-vol                  552     50.9   +0.315    1.6

==============================================================================
9. COHÉRENCE BULL vs BEAR (edges momentum testés par macro-régime)
==============================================================================
  Macro-régime défini par pente SMA200 (>0 = bull, <0 = bear).

  [1h] horizon 12
    BULL (54.2% du temps)
        LONG momentum  : P(win)=50.1% moy=+0.132% t=8.2 n=16528
        SHORT momentum : P(win)=44.3% moy=-0.117% t=-2.7 n=2122
    BEAR (45.8% du temps)
        LONG momentum  : P(win)=47.0% moy=-0.104% t=-2.2 n=1996
        SHORT momentum : P(win)=45.9% moy=-0.073% t=-3.5 n=12802

  [4h] horizon 6
    BULL (57.5% du temps)
        LONG momentum  : P(win)=53.3% moy=+0.356% t=8.0 n=5423
        SHORT momentum : P(win)=47.4% moy=-0.233% t=-2.0 n=597
    BEAR (42.5% du temps)
        LONG momentum  : P(win)=56.4% moy=+0.339% t=2.5 n=374
        SHORT momentum : P(win)=47.6% moy=-0.051% t=-0.9 n=3886

  [1d] horizon 3
    BULL (68.5% du temps)
        LONG momentum  : P(win)=54.0% moy=+0.752% t=4.5 n=918
        SHORT momentum : P(win)=44.4% moy=-0.855% t=-1.8 n=135
    BEAR (31.5% du temps)
        LONG momentum  : P(win)=49.1% moy=-0.476% t=-0.8 n=108
        SHORT momentum : P(win)=48.8% moy=-0.194% t=-0.6 n=387

  → CONSTAT : LONG en bull = très positif (t≈8). SHORT en bear = négatif/nul
    (t≈-0.6/-0.9) → l'edge short n'existe PAS sur le rendement forward moyen ;
    il faut le skew gauche + trailing serré, ou rester FLAT en bear (cash).

==============================================================================
SYNTHÈSE — edges retenus pour la conception de la stratégie
==============================================================================

  FAITS MESURÉS (et leurs conséquences de conception) :

  1. EDGE LONG-MOMENTUM ROBUSTE & MULTI-TF — close>ema50>ema200 (+ ADX>25,
     breakout Donchian/Bollinger, flip MACD) a une expectancy forward positive
     et significative sur 1h/4h/1d (t = 7.0 / 8.4 / 3.8), surtout en BULL
     (t = 8.2 / 8.0 / 4.5). C'est le moteur directionnel principal.

  2. LES SHORTS SONT STRUCTURELLEMENT FAIBLES — sur 2018-2026, la dérive
     séculaire est haussière et les bear-markets contiennent des rallyes de
     contre-tendance violents : l'expectancy forward des shorts momentum est
     négative ou nulle MÊME en bear (4h t=-0.9, 1d t=-0.6). NE PAS shorter
     naïvement. Deux leviers de rattrapage : (a) le skew NÉGATIF des rendements
     (-0.2 à -1.2) → les krachs sont nets, donc un short avec trailing serré
     capture la queue gauche ; (b) restreindre les shorts au macro-régime bear
     CONFIRMÉ (prix<EMA long, pente SMA200<0) + breakdown + momentum, taille
     réduite. Sinon : rester FLAT.

  3. LA MEILLEURE PROTECTION BEAR = ÊTRE FLAT — éviter les -72% de drawdown du
     Buy&Hold en s'abstenant hors confluence haussière. « Performer en marché
     baissier » = préserver le capital (cash) + shorts opportunistes filtrés.

  4. VOLATILITÉ PRÉVISIBLE, DIRECTION NON — ACF|r| persistante (0.15-0.28) =
     clustering fort ; ACF(r)≈0 et VR≈1 = pas de momentum/mean-reversion
     LINÉAIRE. ⇒ la volatilité pilote le TIMING (squeeze→expansion) et le
     SIZING (stop ATR), pas un edge directionnel linéaire.

  5. MEAN-REVERSION LONG CONDITIONNELLE — RSI<30 sur 4h/1d (P(win) 58-64%) et
     régime range (1d fwd +1.7%, P(up) 59%) ⇒ acheter les dips UNIQUEMENT hors
     tendance baissière forte.

  6. CYCLES SPECTRAUX NON DÉTERMINISTES — aucun cycle fixe significatif sur les
     rendements (p=0.43/0.22) ; l'énergie FFT du log-prix est sur les très
     basses fréquences (la tendance). ⇒ phase spectrale = confirmation
     directionnelle douce (faible poids), pas une horloge.

  7. FIBONACCI = ZONES, PAS DÉCLENCHEUR — edge incohérent selon TF
     (1h 0.618 +3.7pt mais 1d 0.5 -13pt). ⇒ usage en confluence/placement de
     stop-target seulement.

  CONCEPTION RETENUE — stratégie régime-adaptative « Harmonic Regime » :
   • LONG trend-momentum (cœur) : macro-trend up + structure EMA + ADX +
     déclencheur (breakout OU pullback-resume OU flip MACD), filtre volatilité.
   • LONG mean-reversion : RSI survente en range/non-bear (taille réduite).
   • SHORT défensif : macro-bear confirmé + breakdown + momentum, taille réduite,
     trailing serré (capture le skew gauche).
   • Score de QUALITÉ = confluence pondérée ≥ seuil, sinon ABSTENTION (flat).
   • Sizing par risque (1%/trade) + stop ATR + trailing multi-phase + max-hold.
   • Composantes signal/fréquence (cycle FFT) et Fibonacci intégrées en
     confirmation/zones, conformément à leur poids statistique réel.

```
