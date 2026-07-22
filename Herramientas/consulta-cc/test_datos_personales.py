#!/usr/bin/env python3
"""
test_datos_personales.py — Quick test of ProcuradoriaClient

Teste consulta de un NUIP conocido para validar:
1. Conexión SSL (sin validación de certificado)
2. CAPTCHA solver (preguntas predefinidas)
3. Extracción de datos personales
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from modulos.datos_personales import ProcuradoriaClient


def test_consulta_cedula():
    """Test consulta de cédula."""
    print("=" * 60)
    print("TEST: ProcuradoriaClient.consultar_cedula()")
    print("=" * 60)
    
    # Ejemplo con un NUIP (ajusta si es necesario)
    numeroIdentificacion = input("\nIngresa NUIP para probar (ej: 73135439): ").strip()
    if not numeroIdentificacion:
        print("SKIP: Sin NUIP")
        return
    
    print(f"\nConsultando procuraduría para NUIP: {numeroIdentificacion}")
    print("(Esto puede tomar 10-30 segundos por timeouts/redirects...)\n")
    
    client = ProcuradoriaClient()
    
    try:
        resultado = client.consultar_cedula(numeroIdentificacion)
        
        if "error" in resultado and resultado["error"]:
            print(f"ERROR: {resultado['error']}")
        else:
            print("EXITO: Datos extraidos")
            print(f"  nombres:          {resultado.get('nombres', 'N/A')}")
            print(f"  segundoNombre:    {resultado.get('segundoNombre', 'N/A')}")
            print(f"  primerApellido:   {resultado.get('primerApellido', 'N/A')}")
            print(f"  segundoApellido:  {resultado.get('segundoApellido', 'N/A')}")
    
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


def test_captcha_answers():
    """Test CAPTCHA solver lógica."""
    print("\n" + "=" * 60)
    print("TEST: CAPTCHA Solver Logic")
    print("=" * 60)
    
    client = ProcuradoriaClient()
    test_cases = [
        ("¿ Cual es la Capital del Atlantico?", "123456", "barranquilla"),
        ("¿Escriba los tres primeros digitos del documento a consultar?", "123456", "123"),
        ("Cuanto es 5 + 3?", "123456", "8"),
        ("Cuanto es 10 - 2?", "123456", "8"),
    ]
    
    print()
    all_ok = True
    for pregunta, nuip, expected in test_cases:
        respuesta = client._calcular_respuesta_captcha(pregunta, nuip)
        status = "OK" if respuesta == expected else "FAIL"
        if respuesta != expected:
            all_ok = False
        print(f"  [{status}] '{pregunta}' -> '{respuesta}' (expected: '{expected}')")
    
    if all_ok:
        print("\nTodos los tests pasaron!")
    else:
        print("\nAlgunos tests fallaron!")
        return False
    
    return True


if __name__ == "__main__":
    print("\nTEST SUITE: datos_personales.py\n")
    
    # Test 1: CAPTCHA logic
    if not test_captcha_answers():
        sys.exit(1)
    
    # Test 2: Consulta real (opcional)
    test_consulta_cedula()
    
    print("\n" + "=" * 60)
    print("FIN TESTS")
    print("=" * 60)
