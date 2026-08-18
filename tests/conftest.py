"""Fixtures partagées — découvertes automatiquement par pytest (aucun import requis)."""
import pytest

from app.api import state


@pytest.fixture
def gapped_ohlcv_df():
    """TEST-05 : série 1h avec trous (pas une grille synthétique régulière)."""
    from tests.test_perf_real_series import gapped_ohlcv
    return gapped_ohlcv(800)


@pytest.fixture(autouse=True)
def _disable_rate_limiting():
    """SEC-04 : plusieurs tests appellent les fonctions de route FastAPI
    directement (sans passer par TestClient/ASGI), donc sans objet Request
    réel. Le décorateur ``@state.limiter.limit(...)`` exige une instance
    authentique de ``starlette.requests.Request`` dès que le limiter est actif
    — désactivé pour toute la suite (``Limiter.enabled`` existe précisément
    pour ce cas d'usage côté slowapi ; la limite globale reste testable
    manuellement via TestClient en la réactivant localement si besoin)."""
    original = state.limiter.enabled
    state.limiter.enabled = False
    yield
    state.limiter.enabled = original
