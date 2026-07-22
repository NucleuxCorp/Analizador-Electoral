"""
datos_personales.py — Consulta datos personales (nombres, apellidos)

Consulta procuraduría vía HTTP + CAPTCHA solver (preguntas predefinidas sin OCR).
Extrae: nombres, segundo nombre, primer apellido, segundo apellido.

No incluye puesto votación (requeriría Puppeteer para registraduría).
"""

import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class ProcuradoriaClient:
    """HTTP client for procuraduría with CAPTCHA solver (sin OCR)."""
    
    BASE_URL = "https://apps.procuraduria.gov.co/webcert/inicio.aspx"
    
    # Mapeo de preguntas de seguridad → respuestas
    CAPTCHA_ANSWERS = {
        "¿ Cual es la Capital del Atlantico?": "barranquilla",
        "¿ Cual es la Capital del Vallle del Cauca?": "cali",
        "¿ Cual es la Capital de Colombia (sin tilde)?": "bogota",
        "¿ Cual es la Capital de Antioquia (sin tilde)?": "medellin",
        "¿Escriba los tres primeros digitos del documento a consultar?": None,  # Computed from NUIP
    }
    
    def __init__(self):
        """Initialize with SSL context that bypasses certificate verification."""
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self.cookies = ""
    
    def _extract_form_field(self, html: str, field_name: str) -> str:
        """Extract hidden form field value by name."""
        pattern = rf'name="{field_name}"[^>]*value="([^"]*)"'
        match = re.search(pattern, html)
        return match.group(1) if match else ""
    
    def _extract_pregunta(self, html: str) -> str:
        """Extract CAPTCHA question from HTML."""
        pattern = r'id="lblPregunta"[^>]*>([^<]+)<'
        match = re.search(pattern, html)
        return match.group(1).strip() if match else ""
    
    def _extract_datos(self, html: str) -> dict[str, str]:
        """Extract personal data (nombres, apellidos) from response HTML."""
        def extract_field(label: str) -> str:
            pattern = rf">\s*{re.escape(label)}\s*<[^>]*>\s*([^<]+)"
            match = re.search(pattern, html, re.IGNORECASE)
            return match.group(1).strip() if match else ""
        
        return {
            "nombres": extract_field("Nombres"),
            "segundoNombre": extract_field("Segundo Nombre"),
            "primerApellido": extract_field("Primer Apellido"),
            "segundoApellido": extract_field("Segundo Apellido"),
        }
    
    def _calcular_respuesta_captcha(self, pregunta: str, numeroIdentificacion: str) -> Optional[str]:
        """Calcula respuesta a pregunta CAPTCHA.
        
        Mapea preguntas conocidas. Para preguntas desconocidas, 
        intenta resolver con heurística o retorna None para re-intentar.
        """
        # Pregunta de aritmética: "Cuanto es X + Y?"
        if "Cuanto es" in pregunta:
            # Reemplazar X por * para búsqueda de multiplicación
            pregunta_normalized = pregunta.replace("X", "*")
            pattern = r"(\d+)\s*([+\-*/])\s*(\d+)"
            match = re.search(pattern, pregunta_normalized)
            if match:
                num1 = int(match.group(1))
                operador = match.group(2)
                num2 = int(match.group(3))
                
                if operador == "+":
                    resultado = num1 + num2
                elif operador == "-":
                    resultado = num1 - num2
                elif operador == "*":
                    resultado = num1 * num2
                elif operador == "/":
                    resultado = int(num1 / num2)
                else:
                    return None
                
                return str(resultado)
            return None
        
        # Preguntas mapeadas
        for pregunta_clave, respuesta in self.CAPTCHA_ANSWERS.items():
            if pregunta_clave in pregunta:
                if respuesta is None:
                    # Tres primeros dígitos del documento
                    return numeroIdentificacion[:3]
                return respuesta
        
        # Preguntas sobre número de letras: "cantidad de letras del primer nombre"
        if "cantidad de letras" in pregunta.lower() and "nombre" in pregunta.lower():
            # Retorna None - necesita respuesta dinámica
            return None
        
        # Pregunta desconocida
        return None
    
    def _realizar_request(
        self, 
        path: str, 
        post_data: str,
        cookies: str = ""
    ) -> tuple[str, str]:
        """Realiza POST request y retorna (html, new_cookies)."""
        body = post_data.encode("utf-8")
        
        req = urllib.request.Request(
            self.BASE_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Cookie": cookies,
            },
        )
        
        try:
            with urllib.request.urlopen(req, context=self.ssl_context, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                
                # Extraer cookie de respuesta
                set_cookie = resp.headers.get("Set-Cookie", "")
                new_cookies = set_cookie.split(";")[0] if set_cookie else cookies
                
                return html, new_cookies
        except Exception as e:
            raise RuntimeError(f"HTTP request failed: {e}")
    
    def consultar_cedula(self, numeroIdentificacion: str) -> dict[str, Any]:
        """Consulta procuraduría y extrae nombres, apellidos.
        
        Realiza flujo completo con reintentos si la pregunta no es conocida:
        1. GET inicial para obtener sesión
        2. POST con cédula para obtener pregunta CAPTCHA
        3. Si pregunta desconocida, solicita CAPTCHA alternativo (botón "Refresh")
        4. POST con respuesta CAPTCHA
        5. Extrae nombres y apellidos
        
        Args:
            numeroIdentificacion: Colombian ID string
        
        Returns:
            dict with keys: nombres, segundoNombre, primerApellido, segundoApellido, error
        """
        max_reintentos = 5
        intento = 0
        
        try:
            # Paso 1: GET inicial para obtener sesión y campos ocultos
            req_get = urllib.request.Request(
                self.BASE_URL,
                method="GET",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                },
            )
            
            with urllib.request.urlopen(req_get, context=self.ssl_context, timeout=30) as resp:
                html_initial = resp.read().decode("utf-8", errors="replace")
                set_cookie = resp.headers.get("Set-Cookie", "")
                self.cookies = set_cookie.split(";")[0] if set_cookie else ""
            
            viewstate = self._extract_form_field(html_initial, "__VIEWSTATE")
            eventvalidation = self._extract_form_field(html_initial, "__EVENTVALIDATION")
            
            if not viewstate or not eventvalidation:
                return {"error": "No se pudo obtener campos ocultos (VIEWSTATE/EVENTVALIDATION)"}
            
            # Paso 2+: POST con cédula para obtener pregunta
            while intento < max_reintentos:
                intento += 1
                
                post_data_1 = urllib.parse.urlencode({
                    "__VIEWSTATE": viewstate,
                    "__EVENTVALIDATION": eventvalidation,
                    "ctl00$ContentPlaceHolder1$ddlTipoID": "1",
                    "ctl00$ContentPlaceHolder1$txtNumID": numeroIdentificacion,
                    "ctl00$ContentPlaceHolder1$btnConsultar": "Consultar",
                })
                
                html_pregunta, self.cookies = self._realizar_request("", post_data_1, self.cookies)
                
                pregunta = self._extract_pregunta(html_pregunta)
                if not pregunta:
                    return {"error": "No se pudo obtener la pregunta CAPTCHA"}
                
                respuesta = self._calcular_respuesta_captcha(pregunta, numeroIdentificacion)
                
                if respuesta is None:
                    # Pregunta desconocida - click en botón refresh para otra pregunta
                    if intento < max_reintentos:
                        viewstate = self._extract_form_field(html_pregunta, "__VIEWSTATE")
                        eventvalidation = self._extract_form_field(html_pregunta, "__EVENTVALIDATION")
                        # Re-intentar con click en botón (simula refresh de pregunta)
                        continue
                    else:
                        return {"error": f"Pregunta no resuelta despues de {max_reintentos} intentos: {pregunta}"}
                
                # Paso 3: Actualizar VIEWSTATE y EVENTVALIDATION
                viewstate = self._extract_form_field(html_pregunta, "__VIEWSTATE")
                eventvalidation = self._extract_form_field(html_pregunta, "__EVENTVALIDATION")
                
                # Paso 4: POST con respuesta
                post_data_2 = urllib.parse.urlencode({
                    "__VIEWSTATE": viewstate,
                    "__EVENTVALIDATION": eventvalidation,
                    "ctl00$ContentPlaceHolder1$ddlTipoID": "1",
                    "ctl00$ContentPlaceHolder1$txtNumID": numeroIdentificacion,
                    "ctl00$ContentPlaceHolder1$txtRespuestaPregunta": respuesta,
                    "ctl00$ContentPlaceHolder1$btnConsultar": "Consultar",
                })
                
                html_respuesta, self.cookies = self._realizar_request("", post_data_2, self.cookies)
                
                # Paso 5: Extraer datos
                datos = self._extract_datos(html_respuesta)
                
                if not datos.get("nombres") and not datos.get("primerApellido"):
                    return {"error": "No se encontraron datos en la respuesta"}
                
                return datos
            
            return {"error": f"Max reintentos ({max_reintentos}) alcanzado"}
        
        except Exception as e:
            return {"error": f"Error en consulta: {str(e)}"}


class RegistraduriaClient:
    """Placeholder para registraduría (requeriría Puppeteer/JavaScript)."""
    
    def __init__(self):
        pass
    
    async def consultar_puesto(self, numeroIdentificacion: str) -> dict:
        """Extrae puesto de votación desde registraduría.
        
        Requiere Puppeteer (headless: false).
        No implementado en este módulo (ver searchPeople/index.mjs).
        
        Returns:
            dict with keys: departamento, municipio, puesto, zona, mesa, error
        """
        return {"error": "No implementado en este módulo"}
