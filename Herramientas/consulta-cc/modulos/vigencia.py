"""
vigencia.py — Extract Vigencia (defunciones) functionality from consulta_defunciones.py

Consulta defunciones API and classifies NUIP status (vivo, fallecido, etc.).
This module is a thin wrapper around the parent's logic for modular import.
"""

def consult_vigencia(nuip: str, config: dict) -> dict:
    """Consult vigencia of a NUIP against Registraduría API.
    
    Args:
        nuip: Colombian ID number (string)
        config: dict with API settings, delay, workers, etc.
    
    Returns:
        dict with keys: clasificacion, vigencia_raw, codigo, fecha_consulta, error
    """
    # Delegation: call parent consulta_defunciones.py functions
    # (imported dynamically or via direct call)
    pass
