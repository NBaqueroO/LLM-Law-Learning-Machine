"""Estado del grafo. Es el contrato entre nodos"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class Pasaje(TypedDict, total=False):
    """Un fragmento del índice ."""
    chunk_id: str
    doc_id: str
    inicio: int
    fin: int
    texto: str                   
    score: float
    encabezado: str               
    articulo: Optional[str]   
    cuerpos: list               
    score_rerank: float          

class Candidato(TypedDict):
    """Un hit de bm25_search o vector_search, todavía sin hidratar (sin texto)."""
    chunk_id: str
    indice: str                  
    fuente: str                 
    rango: int                   
    score: float                 


class Estado(TypedDict, total=False):
    id: int
    formato: Optional[str]        
    pregunta: str
    opciones: dict[str, str]      
    area: Optional[str]

   
    consulta: str                 
    cuerpos_esperados: list[tuple]  
    retry: int                   

    lookup_hits: list[Pasaje]     
    filtro_cuerpos: list[tuple]  
    bm25_hits: list[Candidato]    
    dense_hits: list[Candidato]
    pasajes: list[Pasaje]      
    score_max: float             

    salida: dict[str, Any]       
    usados: list[int]            
    abstencion: bool             
    submission: dict[str, Any]   

    traza: dict[str, Any]


CAMPOS_OBLIGATORIOS: dict[str, tuple[str, ...]] = {
    "multiple_choice": ("respuesta_correcta", "justificacion", "descarte_opciones"),
    "semi_open": ("respuesta", "palabras_clave", "referencia_legal"),
    "open_ended": ("marco_normativo", "analisis", "jurisprudencia", "conclusion"),
}