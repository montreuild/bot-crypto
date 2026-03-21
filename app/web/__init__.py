"""Package web — interface utilisateur HTML/Jinja2.

État actuel
-----------
Les routes web (rendu HTML via Jinja2Templates) sont définies dans
``app/api/main.py`` et utilisent les templates du dossier
``app/web/templates/``.

Routes web existantes (définie dans app/api/main.py) :
  GET /            → dashboard.html
  GET /backtest    → backtest.html
  GET /optimizer   → optimizer.html
  GET /config      → config.html
  GET /scanner     → scanner.html
  GET /audit       → audit.html

Templates disponibles dans app/web/templates/ :
  base.html, dashboard.html, backtest.html, optimizer.html,
  config.html, scanner.html, audit.html

Note d'architecture
-------------------
Si les routes web sont extraites de ``app/api/main.py`` dans le futur,
elles devront être placées dans ``app/web/views.py`` (ou ``app/web/routes.py``)
et réimportées dans ``app/api/main.py`` via un APIRouter FastAPI.
"""
