# Fork jmarpas: Fase 1 — diagnóstico de los 403 masivos

Síntoma en producción: miles de HTTP 403 diarios en los tres datasets a la vez
(`obtenerDatosConsumoDH`, `obtenerDatosGeneracionPeriodo`, `obtenerMedicionOnline`).

## Causa raíz (verificada en el código)

`ideenergy` **2.0.1.dev3** (la única versión en PyPI), `ideenergy/client.py`:

```python
SESSION_TIMEOUT = 900
...
self._session_timeout = timedelta(session_timeout.total_seconds() * 0.9)
```

`timedelta(x)` interpreta `x` como **días**: `timedelta(810.0)` = 810 días.
`Client.is_logged` devuelve `True` durante 810 días tras el primer login, así que el
decorador `@auth_required` nunca vuelve a llamar a `login()` cuando i-DE caduca la
sesión en su lado (~15 min).

Agravantes:

- `Client.renew_session()` no lleva `@auth_required`: con la sesión caducada en
  servidor devuelve 403 y, como el coordinador lo llamaba fuera del `try`, cada ciclo
  (180 s) fallaba entero.
- Los tres datasets comparten la misma sesión, por eso fallan a la vez.

En `ldotlopez/ideenergy` `main` (commit `f7b3118`, 2026-11-06, sin publicar en PyPI)
está corregido como `timedelta(seconds=session_timeout.total_seconds() * 0.9)`.

## Mitigaciones del fork (Fase 1)

1. Forzar `client._session_timeout = 13 min` (válido con dev3 y con la librería arreglada).
2. Ante 401/403: invalidar sesión (`_login_ts = None`), re-login y reintentar una vez.
3. Backoff exponencial por dataset (5 min → 30 min → 2 h → 6 h) persistido en el Store.
4. `renew_session()` solo si algún dataset va a pedir datos en el ciclo.
