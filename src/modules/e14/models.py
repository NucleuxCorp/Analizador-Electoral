from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FormOption:
    value: str
    label: str
    porcentaje: int = 0  # % of published mesas extracted from dropdown text


@dataclass
class MesaRecord:
    departamento: str
    cod_dpto: str
    municipio: str
    cod_mpio: str
    zona: str
    puesto: str
    cod_puesto: str
    mesa: str
    pdf_url: str
    scraped_at: str

    def to_dict(self) -> dict:
        return {
            "departamento": self.departamento,
            "cod_dpto": self.cod_dpto,
            "municipio": self.municipio,
            "cod_mpio": self.cod_mpio,
            "zona": self.zona,
            "puesto": self.puesto,
            "cod_puesto": self.cod_puesto,
            "mesa": self.mesa,
            "pdf_url": self.pdf_url,
            "scraped_at": self.scraped_at,
        }
