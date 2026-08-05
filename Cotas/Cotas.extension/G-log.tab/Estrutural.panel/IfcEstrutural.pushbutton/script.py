# -*- coding: utf-8 -*-

import os
import re
import traceback
import unicodedata
from collections import defaultdict

from pyrevit import DB, forms, revit
from Autodesk.Revit.DB.Structure import StructuralType


doc = revit.doc


def get_linked_ifc_files():
    """Detecta arquivos IFC vinculados no modelo Revit."""
    ifc_files = []
    
    try:
        # Obter todos os elementos vinculados
        collector = DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance)
        
        for link_instance in collector:
            try:
                link_doc = link_instance.GetLinkDocument()
                if link_doc:
                    doc_path = link_doc.PathName
                    if doc_path and doc_path.lower().endswith('.ifc'):
                        file_name = os.path.basename(doc_path)
                        ifc_files.append({
                            'path': doc_path,
                            'name': file_name,
                            'instance': link_instance
                        })
            except Exception:
                pass
    except Exception as e:
        pass
    
    return ifc_files


def select_ifc_from_list(ifc_files):
    """Permite selecionar arquivo IFC da lista de links."""
    if len(ifc_files) == 1:
        return ifc_files[0]
    
    if len(ifc_files) > 1:
        # Mostrar diálogo para escolher qual IFC usar
        file_names = [f['name'] for f in ifc_files]
        selected_index = forms.SelectFromList.show(
            file_names,
            title="Selecionar arquivo IFC",
            button_name="Usar"
        )
        
        if selected_index is None or selected_index < 0:
            return None
        
        return ifc_files[selected_index]
    
    return None


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
    import sys
    
    # Tentar vários encodings
    encodings_to_try = [
        "utf-8-sig", 
        "utf-8", 
        "latin-1",
        "cp1252",
        sys.getdefaultencoding()
    ]
    
    errors_list = []
    
    for encoding in encodings_to_try:
        try:
            with open(file_path, "r", encoding=encoding, errors="ignore") as handle:
                content = handle.read()
                if content and len(content) > 10:  # Validar que leu algo
                    return content
        except Exception as e:
            errors_list.append("{}: {}".format(encoding, str(e)))
    
    # Se chegou aqui, não conseguiu ler
    error_details = "\n".join(errors_list)
    raise IOError("Nao foi possivel ler o arquivo IFC com nenhum encoding.\nTentativas:\n{}".format(error_details))


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
        # Validar se tem modelo Revit aberto
        if not doc:
            forms.alert("Nenhum documento Revit aberto.", title="Erro")
            return
        
        # Detectar arquivos IFC vinculados
        ifc_files = get_linked_ifc_files()
        
        if not ifc_files:
            forms.alert(
                "Nenhum arquivo IFC vinculado encontrado no modelo.\n\n"
                "Vincule um arquivo .ifc antes de usar este plugin.",
                title="Aviso",
                warn_icon=True
            )
            return
        
        # Selecionar qual IFC usar (se houver mais de um)
        selected_ifc = select_ifc_from_list(ifc_files)
        if not selected_ifc:
            return
        
        ifc_path = selected_ifc['path']
        ifc_name = selected_ifc['name']
        
        # Ler arquivo IFC
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
        summary = "Analise do arquivo IFC vinculado:\n\n"
        summary += "Arquivo: {}\n\n".format(ifc_name)
        summary += "- Colunas encontradas: {}\n".format(len(columns))
        summary += "- Vigas encontradas: {}\n".format(len(beams))
        summary += "- Pavimentos no IFC: {}\n".format(ifc_storeys)
        summary += "- Pavimentos no Revit: {}".format(len(revit_levels))
        
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
