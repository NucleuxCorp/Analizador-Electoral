"""Módulos para consulta-cc: vigencia (defunciones) y datos personales."""

try:
    from .datos_personales import ProcuradoriaClient, RegistraduriaClient
except ImportError:
    pass

__all__ = [
    "ProcuradoriaClient",
    "RegistraduriaClient",
]
