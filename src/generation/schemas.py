"""Modelos Pydantic de la salida del LLM."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DescarteOpcion(BaseModel):
    letra: str = Field(description="Letra de una opción incorrecta")
    motivo: str = Field(description="Por qué es incorrecta, en una oración")


class SalidaMC(BaseModel):
    razonamiento: str = Field(description="Análisis breve de las opciones antes de decidir")
    pasajes_usados: list[int] = Field(description="Números [n] de los pasajes usados")
    respuesta_correcta: str = Field(description="Una sola letra mayúscula")
    justificacion: str
    descarte_opciones: list[DescarteOpcion]


class SalidaSemi(BaseModel):
    pasajes_usados: list[int]
    respuesta: str = Field(description="3 a 5 oraciones, máximo 150 palabras")
    palabras_clave: list[str]
    referencia_legal: str


class SalidaOpen(BaseModel):
    pasajes_usados: list[int]
    marco_normativo: str
    analisis: str = Field(description="5 a 8 oraciones")
    jurisprudencia: str
    conclusion: str
