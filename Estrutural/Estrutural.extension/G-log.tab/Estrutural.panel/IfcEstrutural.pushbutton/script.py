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
    """Detecta arquivos IFC vinculados - múltiplas estratégias."""
    ifc_files = []
    
    # Estratégia 1: RevitLinkInstance
    try:
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
                            'source': 'RevitLinkInstance'
                        })
            except Exception:
                pass
    except Exception:
        pass
    
    # Estratégia 2: ExternalFileReference (mais robusto)
    try:
        external_files = doc.ExternalFileReferences
        for ext_file in external_files:
            try:
                file_path = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(ext_file.GetPath())
                if file_path and file_path.lower().endswith('.ifc'):
                    file_name = os.path.basename(file_path)
                    # Evitar duplicatas
                    if not any(f['path'] == file_path for f in ifc_files):
                        ifc_files.append({
                            'path': file_path,
                            'name': file_name,
                            'source': 'ExternalFileReference'
                        })
            except Exception:
                pass
    except Exception:
        pass
    
    return ifc_files


def select_ifc_file(ifc_files):
    """Permite selecionar arquivo IFC."""
    if len(ifc_files) == 1:
        return ifc_files[0]
    
    if len(ifc_files) > 1:
        file_names = ["{} ({})".format(f['name'], f['source']) for f in ifc_files]
        selected_index = forms.SelectFromList.show(
            file_names,
            title="Selecionar arquivo IFC"
        )
        
        if selected_index is not None and selected_index >= 0:
            return ifc_files[selected_index]
    
    # Se não encontrou automaticamente, permitir seleção manual
    result = forms.ask_for_one_option(
        ["Selecionar arquivo .ifc manualmente"],
        title="Nenhum IFC detectado"
    )
    
    if result:
        ifc_path = forms.pick_file(file_ext="ifc")
        if ifc_path and os.path.exists(ifc_path):
            return {
                'path': ifc_path,
                'name': os.path.basename(ifc_path),
                'source': 'Manual'
            }
    
    return None


def read_text_file(file_path):
    """Lê arquivo com múltiplas tentativas de encoding."""
    import sys
    
    encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252", sys.getdefaultencoding()]
    
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding, errors="ignore") as f:
                content = f.read()
                if content and len(content) > 100:
                    return content
        except Exception:
            pass
    
    raise IOError("Falha ao ler arquivo: {}".format(file_path))


def count_ifc_storeys(ifc_text):
    """Conta pavimentos no IFC."""
    return len(re.findall(r"\bIFCBUILDINGSTOREY\s*\(", ifc_text, re.IGNORECASE))


def extract_ifc_elements(ifc_text):
    """Extrai elementos estruturais do IFC com dados completos."""
    elements = {
        'columns': [],
        'beams': [],
        'slabs': []
    }
    
    # Padrões melhorados para capturar mais dados
    column_pattern = r"#(\d+)\s*=\s*IFCCOLUMN\s*\(([^)]+)\)"
    beam_pattern = r"#(\d+)\s*=\s*IFCBEAM\s*\(([^)]+)\)"
    slab_pattern = r"#(\d+)\s*=\s*IFCSLAB\s*\(([^)]+)\)"
    
    for match in re.finditer(column_pattern, ifc_text, re.IGNORECASE | re.DOTALL):
        elements['columns'].append({
            'id': match.group(1),
            'data': match.group(2),
            'type': 'COLUMN'
        })
    
    for match in re.finditer(beam_pattern, ifc_text, re.IGNORECASE | re.DOTALL):
        elements['beams'].append({
            'id': match.group(1),
            'data': match.group(2),
            'type': 'BEAM'
        })
    
    for match in re.finditer(slab_pattern, ifc_text, re.IGNORECASE | re.DOTALL):
        elements['slabs'].append({
            'id': match.group(1),
            'data': match.group(2),
            'type': 'SLAB'
        })
    
    return elements


def get_revit_levels():
    """Retorna níveis do Revit."""
    levels = {}
    collector = DB.FilteredElementCollector(doc).OfClass(DB.Level)
    for level in collector:
        levels[level.Name] = level
    return levels


def main():
    """Função principal - análise inicial e validação."""
    try:
        if not doc:
            forms.alert("Nenhum documento Revit aberto.", title="Erro")
            return
        
        # Detectar ou selecionar arquivo IFC
        ifc_files = get_linked_ifc_files()
        selected_ifc = select_ifc_file(ifc_files)
        
        if not selected_ifc:
            return
        
        ifc_path = selected_ifc['path']
        ifc_name = selected_ifc['name']
        
        if not os.path.exists(ifc_path):
            forms.alert("Arquivo não encontrado: {}".format(ifc_path), title="Erro")
            return
        
        # Ler e analisar
        ifc_text = read_text_file(ifc_path)
        ifc_storeys = count_ifc_storeys(ifc_text)
        revit_levels = get_revit_levels()
        elements = extract_ifc_elements(ifc_text)
        
        # Validações
        if ifc_storeys > len(revit_levels):
            forms.alert(
                "IFC tem {} pavimentos, Revit tem {}.\n\n"
                "Crie os pavimentos necessários.".format(ifc_storeys, len(revit_levels)),
                title="Aviso"
            )
            return
        
        if not (elements['columns'] or elements['beams'] or elements['slabs']):
            forms.alert(
                "Nenhum elemento estrutural encontrado no IFC.",
                title="Aviso"
            )
            return
        
        # Mostrar resultado
        summary = "ANÁLISE DO ARQUIVO IFC\n\n"
        summary += "Arquivo: {}\n".format(ifc_name)
        summary += "Fonte: {}\n\n".format(selected_ifc['source'])
        summary += "ELEMENTOS ENCONTRADOS:\n"
        summary += "- Colunas: {}\n".format(len(elements['columns']))
        summary += "- Vigas: {}\n".format(len(elements['beams']))
        summary += "- Lajes: {}\n\n".format(len(elements['slabs']))
        summary += "MODELO REVIT:\n"
        summary += "- Pavimentos IFC: {}\n".format(ifc_storeys)
        summary += "- Pavimentos Revit: {}".format(len(revit_levels))
        
        forms.alert(summary, title="Análise - OK")
        
    except Exception as e:
        forms.alert(
            "Erro:\n{}\n\n{}".format(str(e), traceback.format_exc()),
            title="Erro"
        )


try:
    main()
except Exception as fatal_exc:
    forms.alert(
        "Erro fatal:\n{}".format(traceback.format_exc()),
        title="Erro Fatal"
    )
