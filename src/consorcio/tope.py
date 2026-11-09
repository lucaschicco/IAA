"""Tope de gasto mensual en modelos, en la aplicación.

Suma el libro `gastos_modelo` del mes calendario UTC. `atender` e `ingestar_pdf` lo consultan antes de
empezar y, si se alcanzó, no llaman al modelo. `preguntar` por Telegram, CLI o evaluación y `evaluar-ocr`
cuentan pero no se bloquean (Telegram está congelado).

Límites conocidos:
- El chequeo es previo a cada corrida: se puede pasar por las llamadas en curso y por una ingesta
  entera, porque una ingesta nunca se corta a mitad de camino (dejaría un documento parcial que
  después da `duplicado`).
- Los modelos sin precio no suman: aparecen en `modelos_sin_precio` y el gasto queda subestimado.
- No sustituye al tope del proveedor, que es el único que frena de verdad.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .almacen.postgres import GastoModelo, crear_tablas, engine
from .config import config


def _limites_del_mes(ahora: datetime | None = None) -> tuple[datetime, datetime]:
    inicio = (ahora or datetime.now(UTC)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    siguiente = inicio.replace(year=inicio.year + 1, month=1) if inicio.month == 12 else inicio.replace(month=inicio.month + 1)
    return inicio, siguiente


def _del_mes(q, limites: tuple[datetime, datetime]):
    return q.where(GastoModelo.creado_en >= limites[0], GastoModelo.creado_en < limites[1])


def gasto_del_mes() -> float:
    crear_tablas()
    with Session(engine()) as s:
        q = select(func.coalesce(func.sum(GastoModelo.costo_usd), 0.0))
        return float(s.scalar(_del_mes(q, _limites_del_mes())))


def tope_excedido() -> bool:
    """False si TOPE_USD_MENSUAL es 0, que significa sin tope."""
    tope = config.tope_usd_mensual
    return tope != 0 and gasto_del_mes() >= tope


def estado_tope() -> dict:
    """Lo que muestran `consorcio gasto` y el CRM. `disponible_usd` es None cuando no hay tope."""
    limites = _limites_del_mes()
    tope = config.tope_usd_mensual
    gastado = gasto_del_mes()
    with Session(engine()) as s:
        q = select(
            GastoModelo.modelo, func.count(), func.sum(GastoModelo.tokens_entrada + GastoModelo.tokens_salida)
        ).where(GastoModelo.costo_usd.is_(None)).group_by(GastoModelo.modelo).order_by(GastoModelo.modelo)
        sin_precio = [
            {"modelo": modelo, "llamadas": llamadas, "tokens": int(tokens or 0)}
            for modelo, llamadas, tokens in s.execute(_del_mes(q, limites)).all()
        ]
    return {
        "tope_usd": tope, "gastado_usd": gastado, "disponible_usd": max(tope - gastado, 0.0) if tope else None,
        "excedido": tope != 0 and gastado >= tope, "mes": limites[0].strftime("%Y-%m"),
        "modelos_sin_precio": sin_precio,
    }
