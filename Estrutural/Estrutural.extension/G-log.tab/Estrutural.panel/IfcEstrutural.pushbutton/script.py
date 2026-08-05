# -*- coding: utf-8 -*-

import os
import re
import traceback
import unicodedata
from collections import defaultdict

from pyrevit import DB, forms, revit
from Autodesk.Revit.DB.Structure import StructuralType


doc = revit.doc


def normalize_text(value):
    """Remove acentos e caracteres especiais."""
    if value is None:
        return ""
    text = str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9\s/x\-]", "", text.lower())


def count_ifc_storeys(ifc_text):
    """Conta níveis/pavimentos no arquivo IFC."""
    return len(re.findall(r"\bIFCBUILDINGSTOREY\s*\(", ifc_text, flags=re.IGNORECASE))


def read_text_file(file_path):
    """Lê arquivo com múltiplas tentativas de encoding."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(file_path, "r", encoding=encoding) as handle:
                return handle.read()
        except Exception:
            pass
    raise IOError("Nao foi possivel ler o arquivo IFC.")


def extract_ifc_entities(ifc_text):
    """Extrai entidades (Column, Beam) do texto IFC."""
    column_pattern = r"#\d+\s*=\s*IFCCOLUMN\s*\((.*?)\)\s*;"
    beam_pattern = r"#\d+\s*=\s*IFCBEAM\s*\((.*?)\)\s*;"
    
    columns = []
    beams = []
    
    for match in re.finditer(column_pattern, ifc_text, re.IGNORECASE | re.DOTALL):
        columns.append(("COLUMN", match.group(1)))
    
    for match in re.finditer(beam_pattern, ifc_text, re.IGNORECASE | re.DOTALL):
        beams.append(("BEAM", match.group(1)))
    
    return columns, beams


def get_revit_levels():
    """Retorna dicionário de níveis do Revit."""
    levels = {}
    collector = DB.FilteredElementCollector(doc).OfClass(DB.Level)
    for level in collector:
        levels[level.Name] = level
    return levels


def main():
    """Função principal."""
    try:
        # Selecionar arquivo IFC
        ifc_path = forms.pick_file(file_ext="ifc")
        if not ifc_path:
            return
        
        if not os.path.exists(ifc_path):
            forms.alert("Arquivo IFC nao encontrado.", title="Erro")
            return
        
        # Ler arquivo
        ifc_text = read_text_file(ifc_path)
        
        # Contar pavimentos
        ifc_storeys = count_ifc_storeys(ifc_text)
        revit_levels = get_revit_levels()
        
        if ifc_storeys > len(revit_levels):
            msg = (
                "O arquivo IFC tem {} pavimentos, "
                "mas o modelo Revit tem apenas {}.\n\n"
                "Crie os pavimentos necessarios antes de continuar.".format(
                    ifc_storeys, len(revit_levels)
                )
            )
            forms.alert(msg, title="Aviso de Pavimentos", warn_icon=True)
            return
        
        # Extrair entidades
        columns, beams = extract_ifc_entities(ifc_text)
        
        if not columns and not beams:
            forms.alert(
                "Nenhuma coluna ou viga encontrada no arquivo IFC.",
                title="Aviso",
                warn_icon=True
            )
            return
        
        # Resumo
        summary = "Analise do arquivo IFC:\n"
        summary += "- Colunas encontradas: {}\n".format(len(columns))
        summary += "- Vigas encontradas: {}\n".format(len(beams))
        summary += "- Pavimentos no IFC: {}\n".format(ifc_storeys)
        summary += "- Pavimentos no Revit: {}\n".format(len(revit_levels))
        
        forms.alert(summary, title="Resultado da Analise")
        
    except Exception as e:
        error_msg = traceback.format_exc()
        forms.alert(
            "Erro ao processar IFC:\n{}\n\nDetalhe:\n{}".format(str(e), error_msg),
            title="Erro",
            warn_icon=True
        )


try:
    main()
except Exception as fatal_exc:
    forms.alert(
        "Erro inesperado:\n{}".format(traceback.format_exc()),
        title="Erro Fatal",
        warn_icon=True
    )
