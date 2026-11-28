"""Tapar datos personales antes de que lleguen a MLflow (docs/2026-11-27-spec-privacidad-trazas.md,
opción B). Los patrones no necesitan base de datos ni MLflow."""
import pytest

from consorcio.datos_personales import tapar, tapar_valor

# Caso 1: mails, CUIT, DNI y teléfonos, con el texto de alrededor intacto
TAPA = [
    ("Escribime a ana.perez@ejemplo.com mañana", "Escribime a [MAIL] mañana"),
    ("CUIT 20-12345678-3 del proveedor", "CUIT [CUIT] del proveedor"),
    ("cuit 20123456783", "cuit [CUIT]"),
    ("DNI 12.345.678, piso 2", "DNI [DNI], piso 2"),
    ("documento 12345678", "documento [DNI]"),
    ("llamame al 11 4567-8901", "llamame al [TELEFONO]"),
    ("llamame al +54 9 11 4567-8901 hoy", "llamame al [TELEFONO] hoy"),
    ("celular 15-4567-8901", "celular [TELEFONO]"),
    ("fijo 4567-8901", "fijo [TELEFONO]"),
    ("whatsapp 1145678901", "whatsapp [TELEFONO]"),
]

# Caso 2: importes, períodos, fechas y unidades no son datos personales y tienen que seguir legibles
NO_TAPA = [
    "Te corresponde pagar $ 110.000,00 en agosto",
    "El total fue 1.100.000,00",
    "Se juntaron $ 1.100.000 para la bomba",
    "Período 2026-08",
    "La cuota vence 2026-08-10",
    "La UF 3 es el 2-A",
    "Silencio de 22 a 8",
    "El porcentual es 4,5 %",
    "Hola, ¿cómo pago las expensas?",
]

# Caso 3: direcciones y nombres por patrón
PATRONES = [
    ("Vivo en Av. Rivadavia 1234, 3° B", "Vivo en [DIRECCION], 3° B"),
    ("la calle Mercedes 274 está cortada", "la [DIRECCION] está cortada"),
    ("constituye domicilio en Mercedes 274, CABA", "constituye domicilio en [DIRECCION], CABA"),
    ("Lo firmó la Sra. Ana Pérez ayer", "Lo firmó la Sra. [NOMBRE] ayer"),
    ("la propietaria PEREZ JUANA pidió la palabra", "la propietaria [NOMBRE] pidió la palabra"),
    ("el vecino del 3B hace ruido", "el vecino del 3B hace ruido"),
]


@pytest.mark.parametrize(("texto", "esperado"), TAPA)
def test_tapa_mails_cuit_dni_y_telefonos(texto, esperado):
    assert tapar(texto) == esperado


@pytest.mark.parametrize("texto", NO_TAPA)
def test_no_tapa_importes_periodos_ni_unidades(texto):
    assert tapar(texto) == texto


@pytest.mark.parametrize(("texto", "esperado"), PATRONES)
def test_tapa_direcciones_y_nombres_por_patron(texto, esperado):
    assert tapar(texto) == esperado


def test_tapar_valor_recorre_estructuras_y_deja_claves_y_numeros():
    """Caso 5: la entrada de un span es un diccionario con listas adentro."""
    valor = {
        "entrada": "mi mail es ana.perez@ejemplo.com",
        "historial": [{"texto": "DNI 12345678", "uf": 3}, ("llamame al 4567-8901",)],
        "uf": 3,
        "ok": True,
        "nada": None,
    }
    assert tapar_valor(valor) == {
        "entrada": "mi mail es [MAIL]",
        "historial": [{"texto": "DNI [DNI]", "uf": 3}, ("llamame al [TELEFONO]",)],
        "uf": 3,
        "ok": True,
        "nada": None,
    }
