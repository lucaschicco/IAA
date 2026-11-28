"""Liquidaciones de expensas: del PDF a una tabla.

Decisión (26/10/2026): lo que es tabla regular se parsea en código, lo que es
prosa va al modelo.
- El modelo (una llamada, skill `liquidacion`) extrae el encabezado (período,
  vencimientos, administración) y los gastos agrupados por rubro.
- La tabla "ESTADO DE CUENTAS Y PRORRATEO" (una fila por unidad funcional, con
  porcentual e importe a pagar) se parsea con una expresión regular: tiene un
  formato fijo y son ~150 filas por liquidación, que al modelo le costarían
  tokens y errores de dígitos. Las filas que no matchean se cuentan
  (`filas_no_parseadas`) para medir la cobertura del parser.
- Los importes de la tabla nunca pasan por el modelo: `consultar_expensas`
  responde con SQL.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from ..llm import Contador, invocar_estructurado
from .esquemas import Seccion, _nulo_si_vacio
from .estructurar import texto_con_marcas
from .skill import cargar_skill

# ------------------------------------------------------------------ esquemas (modelo)


class RubroLiquidacion(BaseModel):
    numero: int | None = Field(None, description="Número del rubro tal como figura (1, 2, 3...)")
    nombre: str = Field(description="Nombre del rubro: SUELDOS, ABONOS Y SERVICIOS, etc.")
    total: float | None = Field(None, description="Total del rubro en pesos, sin símbolo ni separador de miles")
    porcentaje: float | None = Field(None, description="Incidencia del rubro sobre el total, en %")


class GastoLiquidacion(BaseModel):
    rubro: str = Field(description="Nombre del rubro al que pertenece el gasto")
    proveedor: str | None = Field(None, description="Proveedor o beneficiario del pago")
    concepto: str = Field(description="Qué se pagó, en una línea")
    importe: float = Field(description="Importe en pesos, número sin símbolo ni separador de miles")
    fecha: str | None = Field(None, description="Fecha de la factura o del pago en ISO 8601, si figura")
    comprobante: str | None = Field(None, description="Número de factura o comprobante, si figura")
    pagina: int | None = None

    _limpiar = field_validator("proveedor", "fecha", "comprobante", mode="before")(_nulo_si_vacio)


class LiquidacionExpensas(BaseModel):
    periodo: str = Field(description="Período liquidado en formato AAAA-MM (p. ej. 'Julio 2026' → '2026-07')")
    primer_vencimiento: str | None = Field(None, description="Fecha del 1° vencimiento en ISO 8601")
    segundo_vencimiento: str | None = Field(None, description="Fecha del 2° vencimiento en ISO 8601")
    consorcio: str | None = Field(None, description="Nombre o dirección del consorcio")
    administracion: str | None = Field(None, description="Nombre de la administración")
    total_gastos: float | None = Field(None, description="Total de gastos del período, si figura")
    rubros: list[RubroLiquidacion] = Field(default_factory=list)
    gastos: list[GastoLiquidacion] = Field(default_factory=list)

    _limpiar = field_validator(
        "primer_vencimiento", "segundo_vencimiento", "consorcio", "administracion", mode="before"
    )(_nulo_si_vacio)


# ------------------------------------------------------------------ detección y modelo

_MARCAS_LIQUIDACION = ("EXPENSAS", "VENC")


def es_liquidacion(paginas: list[dict]) -> bool:
    """Heurística en código que respalda la clasificación del modelo: la primera
    página de una liquidación menciona expensas y vencimientos."""
    primera = (paginas[0]["texto"] if paginas else "").upper()
    return all(m in primera for m in _MARCAS_LIQUIDACION) and "PRORRATEO" in "".join(
        p["texto"].upper() for p in paginas
    )


def _paginas_para_el_modelo(paginas: list[dict]) -> list[dict]:
    """Saca las páginas de prorrateo (van al parser) y los recibos de sueldo
    (detalle que no se liquida como gasto): menos tokens, menos ruido."""
    utiles = []
    for p in paginas:
        t = p["texto"].upper()
        if "ESTADO DE CUENTAS Y PRORRATEO" in t or "DETALLE DE SUELDOS" in t:
            continue
        utiles.append(p)
    return utiles or paginas


def extraer_liquidacion(paginas: list[dict], contador: Contador) -> LiquidacionExpensas:
    texto = texto_con_marcas(_paginas_para_el_modelo(paginas))
    return invocar_estructurado(contador, "liquidacion", cargar_skill("liquidacion"), texto, LiquidacionExpensas)


def secciones_de_rubros(liq: LiquidacionExpensas) -> list[Seccion]:
    """Cada rubro queda como sección citable, así `preguntar` también encuentra
    las liquidaciones ("¿cuánto se gastó en limpieza en julio?")."""
    secciones = []
    for r in liq.rubros:
        gastos = [g for g in liq.gastos if _clave(g.rubro) == _clave(r.nombre)]
        detalle = "; ".join(f"{g.proveedor or g.concepto}: ${g.importe:,.2f}" for g in gastos[:8])
        total = f" Total ${r.total:,.2f}" if r.total is not None else ""
        porc = f" ({r.porcentaje:.2f} % del período)" if r.porcentaje is not None else ""
        secciones.append(
            Seccion(
                titulo=f"Expensas {liq.periodo} · rubro {r.numero or ''} {r.nombre}".replace("  ", " "),
                tema="expensas",
                resumen=f"Liquidación de expensas {liq.periodo}, rubro {r.nombre}.{total}{porc}. {detalle}".strip(),
                pagina_inicio=min((g.pagina for g in gastos if g.pagina), default=None),
                pagina_fin=max((g.pagina for g in gastos if g.pagina), default=None),
            )
        )
    return secciones


def _clave(texto: str) -> str:
    return " ".join((texto or "").lower().split())


# ------------------------------------------------------------------ prorrateo (código)

_IMP = r"-?[\d,]+\.\d\d"
# Una fila del prorrateo: UF, piso-depto, propietario, importes previos (saldo anterior, pagos,
# crédito/débito, deuda, interés: cuántos hay depende de la fila), 2 a 5 pares "porcentaje% $ importe"
# (expensas ordinarias, extraordinaria, previsión, otros conceptos: cuántos hay depende del mes),
# el total de expensa, 0 a 2 importes sueltos (gasto particular, redondeo), lo que hay que pagar y la UF
# repetida al final. El formato cambió tres veces entre enero y agosto de 2026, por eso es general.
_FILA = re.compile(
    rf"^(?P<uf>\d+)\s+(?P<piso_depto>\S+)\s+(?P<propietario>.+?)\s+"
    rf"(?P<previos>(?:\$ {_IMP}\s+)*)"
    rf"(?P<pares>(?:\d+\.\d\d%\s+\$ {_IMP}\s+){{2,5}})"
    rf"\$ (?P<total>{_IMP})"
    rf"(?P<sueltos>(?:\s+\$ {_IMP}){{0,2}})"
    rf"\s+\$ (?P<a_pagar>{_IMP})\s+(?P=uf)$"
)
_PAR = re.compile(rf"(\d+\.\d\d)%\s+\$ ({_IMP})")
_CANDIDATA = re.compile(r"^\d+\s+\S+\s+.*\$ -?[\d,]+\.\d\d.*\d+$")


def _num(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


def _fila_a_unidad(m: re.Match, pagina: int) -> dict:
    previos = [_num(x) for x in re.findall(_IMP, m.group("previos"))]
    pares = [(float(pc), _num(imp)) for pc, imp in _PAR.findall(m.group("pares"))]
    porcentual = pares[0][0]
    # Los conceptos con el porcentual de la unidad son, en orden: ordinarias, extraordinaria, previsión.
    propios = [imp for pc, imp in pares if pc == porcentual]
    otros = [{"porcentaje": pc, "importe": imp} for pc, imp in pares if pc != porcentual]
    sueltos = [_num(x) for x in re.findall(_IMP, m.group("sueltos"))]
    if len(sueltos) == 2:
        gasto_particular, redondeo = sueltos
    elif len(sueltos) == 1:
        gasto_particular, redondeo = (None, sueltos[0]) if abs(sueltos[0]) < 1 else (sueltos[0], None)
    else:
        gasto_particular, redondeo = None, None
    return {
        "uf": int(m.group("uf")),
        "piso_depto": m.group("piso_depto"),
        "propietario": m.group("propietario").strip(),
        "porcentual": porcentual,
        "expensas_ordinarias": propios[0] if len(propios) > 0 else None,
        "expensa_extraordinaria": propios[1] if len(propios) > 1 else None,
        "prevision_gastos": propios[2] if len(propios) > 2 else None,
        "otros_conceptos": otros + [{"porcentaje": porcentual, "importe": x} for x in propios[3:]],
        "total_expensa": _num(m.group("total")),
        "gasto_particular": gasto_particular,
        "redondeo": redondeo,
        "a_pagar": _num(m.group("a_pagar")),
        "saldo_anterior": previos[0] if previos else None,
        "importes_previos": previos,
        "pagina": pagina,
    }


def parsear_prorrateo(paginas: list[dict]) -> dict:
    """Devuelve {"unidades": [...], "filas_prorrateo": n, "filas_no_parseadas": m}."""
    unidades: list[dict] = []
    no_parseadas = 0
    for p in paginas:
        if "PRORRATEO" not in p["texto"].upper():
            continue
        for linea in p["texto"].splitlines():
            linea = linea.strip()
            m = _FILA.match(linea)
            if m:
                unidades.append(_fila_a_unidad(m, p["numero"]))
            elif _CANDIDATA.match(linea) and not linea.upper().startswith("TOTAL"):
                no_parseadas += 1
    return {"unidades": unidades, "filas_prorrateo": len(unidades), "filas_no_parseadas": no_parseadas}
