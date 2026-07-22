"""
consulta_cc.py — CLI integrado para consultas de cédulas colombianas

Integra dos módulos:
1. Vigencia de cédula (defunciones) — consulta_defunciones.py original
2. Datos personales (nombres, apellidos) — modulos/datos_personales.py

Menú interactivo para seleccionar qué consultar.
"""

import sys
import subprocess
from pathlib import Path

# Agregar módulos al path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from modulos.datos_personales import ProcuradoriaClient


def mostrar_menu() -> str:
    """Muestra menú principal y retorna opción seleccionada."""
    print("\n" + "="*60)
    print("CONSULTA CC - Cedulas Colombianas")
    print("="*60)
    print("\n[1] Consultar Vigencia de Cedula (Fallecido?)")
    print("[2] Consultar Datos Personales (Nombres, Apellidos)")
    print("[3] Consulta Combinada (Nombres + Fallecido)")
    print("[0] Salir\n")
    
    opcion = input("Selecciona una opcion: ").strip()
    return opcion


def consultar_datos_personales():
    """Consulta datos personales de una cédula."""
    print("\n--- Consulta Datos Personales ---\n")
    
    numeroIdentificacion = input("Ingresa numero de cedula: ").strip()
    if not numeroIdentificacion:
        print("[X] Cedula vacia")
        return
    
    print(f"Consultando procuraduria para: {numeroIdentificacion}...")
    
    client = ProcuradoriaClient()
    resultado = client.consultar_cedula(numeroIdentificacion)
    
    if "error" in resultado and resultado["error"]:
        print(f"[X] Error: {resultado['error']}")
    else:
        print("\n[OK] Datos encontrados:")
        print(f"  Nombres:          {resultado.get('nombres', 'N/A')}")
        print(f"  Segundo Nombre:   {resultado.get('segundoNombre', 'N/A')}")
        print(f"  Primer Apellido:  {resultado.get('primerApellido', 'N/A')}")
        print(f"  Segundo Apellido: {resultado.get('segundoApellido', 'N/A')}")
    
    return resultado


def consultar_vigencia():
    """Delegación a consulta_defunciones.py (modo interactivo)."""
    print("\n--- Consulta Vigencia de Cedula ---\n")
    print("[INFO] Ejecutando consulta_defunciones.py...\n")
    
    # Ejecutar script original interactivo
    result = subprocess.run(
        [sys.executable, "consulta_defunciones.py"],
        cwd=str(SCRIPT_DIR)
    )
    
    return result.returncode == 0


def consulta_combinada():
    """Consulta vigencia + datos personales."""
    print("\n--- Consulta Combinada (Nombres + Fallecido) ---\n")
    
    numeroIdentificacion = input("Ingresa numero de cedula: ").strip()
    if not numeroIdentificacion:
        print("[X] Cedula vacia")
        return
    
    print(f"\nConsultando datos personales para: {numeroIdentificacion}...")
    
    # 1. Datos personales
    client = ProcuradoriaClient()
    datos_personales = client.consultar_cedula(numeroIdentificacion)
    
    if "error" in datos_personales and datos_personales["error"]:
        print(f"[X] Error en datos personales: {datos_personales['error']}")
        return
    
    print("\n[OK] Datos personales:")
    print(f"  Nombres:          {datos_personales.get('nombres', 'N/A')}")
    print(f"  Primer Apellido:  {datos_personales.get('primerApellido', 'N/A')}")
    
    # 2. Vigencia
    print(f"\nConsultando vigencia para: {numeroIdentificacion}...")
    
    try:
        # Llamar consulta_defunciones en modo batch
        import csv
        import json
        from datetime import datetime
        
        # Usar la lógica de consulta_defunciones.py
        sys.path.insert(0, str(SCRIPT_DIR / ".."))
        
        # Crear lista temporal y llamar funciones del script original
        result = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '{SCRIPT_DIR}')
from consulta_defunciones import consult_nuip, classify_vigencia

nuip = "{numeroIdentificacion}"
parsed, status, error = consult_nuip(nuip, "")
clasificacion, _ = classify_vigencia(status, parsed, error)
print(f"VIGENCIA:{{clasificacion}}")
"""],
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True
        )
        
        # Extraer resultado
        for line in result.stdout.split("\n"):
            if line.startswith("VIGENCIA:"):
                vigencia = line.split(":")[1]
                print(f"\n[OK] Vigencia: {vigencia}")
                break
        else:
            print(f"[X] Error en vigencia: {result.stderr}")
    
    except Exception as e:
        print(f"[X] Error consultando vigencia: {e}")
    
    # 3. Resumen
    print("\n" + "="*60)
    print("RESUMEN COMBINADO")
    print("="*60)
    print(f"Cedula:           {numeroIdentificacion}")
    print(f"Nombres:          {datos_personales.get('nombres', 'N/A')}")
    print(f"Primer Apellido:  {datos_personales.get('primerApellido', 'N/A')}")
    # Vigencia se muestra arriba


def main():
    """Menu principal."""
    while True:
        opcion = mostrar_menu()
        
        if opcion == "1":
            consultar_vigencia()
        elif opcion == "2":
            consultar_datos_personales()
        elif opcion == "3":
            consulta_combinada()
        elif opcion == "0":
            print("[OK] Saliendo...")
            sys.exit(0)
        else:
            print("[X] Opcion no valida")


if __name__ == "__main__":
    main()
