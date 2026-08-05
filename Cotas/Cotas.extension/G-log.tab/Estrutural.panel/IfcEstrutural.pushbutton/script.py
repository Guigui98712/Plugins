# -*- coding: utf-8 -*-

import os
import re
import traceback
import unicodedata
from collections import defaultdict

from pyrevit import DB, forms, revit
from Autodesk.Revit.DB.Structure import StructuralType


doc = revit.doc


def pick_ifc_path():
    """Permite ao usuário selecionar arquivo IFC."""
    import tkinter
    from tkinter import filedialog
    
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    
    file_path = filedialog.askopenfilename(
        title="Selecionar arquivo IFC",
        filetypes=[("IFC Files", "*.ifc"), ("All Files", "*.*")]
    )
    
    root.destroy()
    return file_path if file_path else None


def read_text_file(file_path):
    """Lê arquivo com múltiplas tentativas de encoding."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(file_path, "r", encoding=encoding) as handle:
                return handle.read()
        except Exception:
            pass
    raise IOError("Nao foi possivel ler o arquivo IFC.")


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


def show_warning(title, message):
    """Mostra mensagem de aviso."""
    forms.alert(message, title=title, warn_icon=True)


def show_error(title, message, details=""):
    """Mostra mensagem de erro."""
    msg = message
    if details:
        msg += "\n\nDetalhe tecnico:\n" + details
    forms.alert(msg, title=title, warn_icon=True)


def extract_ifc_entities(ifc_text):
    """Extrai entidades (Column, Beam) do texto IFC."""
    # Padrão para IfcColumn e IfcBeam
    column_pattern = r"#\d+\s*=\s*IFCCOLUMN\s*\((.*?)\)\s*;"
    beam_pattern = r"#\d+\s*=\s*IFCBEAM\s*\((.*?)\)\s*;"
    
    columns = []
    beams = []
    
    # Procurar por colunas
    for match in re.finditer(column_pattern, ifc_text, re.IGNORECASE | re.DOTALL):
        columns.append(("COLUMN", match.group(1)))
    
    # Procurar por vigas
    for match in re.finditer(beam_pattern, ifc_text, re.IGNORECASE | re.DOTALL):
        beams.append(("BEAM", match.group(1)))
    
    return columns, beams


def extract_property_value(property_text, property_name):
    """Extrai valor de propriedade do texto."""
    # Procura por 'Pset_ColumnCommon.Height' ou similar
    pattern = rf"{property_name}\s*=>\s*([^,;]+)"
    match = re.search(pattern, property_text, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def parse_section_dimensions(text):
    """Extrai dimensões de seção do texto (ex: 40x50, W460x89)."""
    if not text:
        return None
    
    text = normalize_text(text)
    
    # Padrão retangular: 40x50, 40/50, etc
    rect_match = re.search(r"(\d+\.?\d*)\s*[x\/]\s*(\d+\.?\d*)", text)
    if rect_match:
        width = float(rect_match.group(1))
        height = float(rect_match.group(2))
        # Converter de cm para mm se necessário
        if width < 100:
            width *= 10
        if height < 100:
            height *= 10
        return ("RECTANGULAR", width, height)
    
    # Padrão circular: Ø400
    circ_match = re.search(r"[ø∅d]\s*(\d+\.?\d*)", text)
    if circ_match:
        diameter = float(circ_match.group(1))
        if diameter < 100:
            diameter *= 10
        return ("CIRCULAR", diameter)
    
    # Padrão de aço: W460x89, IPE200, HEA200
    steel_match = re.search(r"([whiu]\s*)?(\d+\.?\d*)\s*[x]?\s*(\d+\.?\d*)?", text)
    if steel_match:
        return ("STEEL", steel_match.group(2), steel_match.group(3))
    
    return None


def find_matching_family(doc, dimensions, element_type="Column"):
    """Procura por família compatível com as dimensões."""
    if not dimensions:
        return None
    
    try:
        # Obter todas as famílias de coluna ou viga
        if element_type.lower() == "column":
            family_param = DB.BuiltInCategory.OST_StructuralColumns
        else:
            family_param = DB.BuiltInCategory.OST_StructuralFraming
        
        # Procurar em view padrão
        view = doc.ActiveView
        collector = DB.FilteredElementCollector(doc).OfCategory(family_param)
        
        matching = []
        for elem in collector:
            if isinstance(elem, DB.FamilySymbol):
                # Tentar extrair dimensões da família
                name = elem.Name.lower()
                if dimensions[0] == "RECTANGULAR":
                    # Procurar por dimensões retangulares no nome
                    w, h = dimensions[1], dimensions[2]
                    if f"{int(w)}" in name or f"{int(h)}" in name:
                        matching.append((elem, 2))
                    elif name.count("x") > 0:
                        matching.append((elem, 1))
        
        if matching:
            matching.sort(key=lambda x: x[1], reverse=True)
            return matching[0][0]
    
    except Exception as e:
        pass
    
    return None


def get_revit_levels():
    """Retorna dicionário de níveis do Revit."""
    levels = {}
    collector = DB.FilteredElementCollector(doc).OfClass(DB.Level)
    for level in collector:
        levels[level.Name] = level
    return levels


def create_column_in_revit(family_symbol, level, position_x, position_y, height=None):
    """Cria uma coluna no Revit."""
    try:
        if not family_symbol.IsActive:
            family_symbol.Activate()
        
        # Criar coluna na posição especificada
        pt = DB.XYZ(position_x, position_y, level.Elevation)
        column = DB.FamilyInstance.CreateColumn(
            doc, family_symbol, level, StructuralType.Column, pt
        )
        
        if height and column:
            # Tentar definir altura se suportado
            try:
                param = column.LookupParameter("Height")
                if param and not param.IsReadOnly:
                    param.Set(DB.UnitUtils.ConvertToInternalUnits(
                        height, DB.UnitTypeId.Millimeters
                    ))
            except:
                pass
        
        return column
    except Exception as e:
        return None


def create_beam_in_revit(family_symbol, level, start_point, end_point):
    """Cria uma viga no Revit."""
    try:
        if not family_symbol.IsActive:
            family_symbol.Activate()
        
        # Converter para coordenadas Revit (pés)
        start_xyz = DB.XYZ(start_point[0], start_point[1], level.Elevation)
        end_xyz = DB.XYZ(end_point[0], end_point[1], level.Elevation)
        
        # Criar viga
        beam = DB.FamilyInstance.CreateBeam(
            doc, family_symbol, level, start_xyz, end_xyz, StructuralType.Beam
        )
        return beam
    except Exception as e:
        return None


def main():
    """Função principal."""
    try:
        # Validar se tem modelo aberto
        if not doc:
            show_error("Erro", "Nenhum documento Revit aberto.")
            return
        
        # Selecionar arquivo IFC
        ifc_path = pick_ifc_path()
        if not ifc_path:
            return
        
        if not os.path.exists(ifc_path):
            show_error("Erro", "Arquivo IFC nao encontrado.")
            return
        
        # Ler arquivo
        ifc_text = read_text_file(ifc_path)
        
        # Contar pavimentos
        ifc_storeys = count_ifc_storeys(ifc_text)
        revit_levels = get_revit_levels()
        
        if ifc_storeys > len(revit_levels):
            msg = (
                f"O arquivo IFC tem {ifc_storeys} pavimentos, "
                f"mas o modelo Revit tem apenas {len(revit_levels)}.\n\n"
                f"Crie os pavimentos necessarios antes de continuar."
            )
            show_warning("Aviso de Pavimentos", msg)
            return
        
        # Extrair entidades
        columns, beams = extract_ifc_entities(ifc_text)
        
        if not columns and not beams:
            show_warning(
                "Aviso",
                "Nenhuma coluna ou viga encontrada no arquivo IFC."
            )
            return
        
        # Procurar por famílias faltantes
        missing_families = defaultdict(list)
        found_families = {}
        
        # Processar colunas
        for col_type, col_data in columns:
            dims = parse_section_dimensions(col_data)
            family = find_matching_family(doc, dims, "Column")
            if family:
                found_families[col_data] = family
            else:
                missing_families["Column"].append(dims if dims else "Desconhecido")
        
        # Processar vigas
        for beam_type, beam_data in beams:
            dims = parse_section_dimensions(beam_data)
            family = find_matching_family(doc, dims, "Beam")
            if family:
                found_families[beam_data] = family
            else:
                missing_families["Beam"].append(dims if dims else "Desconhecido")
        
        # Mostrar famílias faltantes se houver
        if missing_families:
            msg = "Familias faltantes para criar elementos:\n\n"
            for elem_type, dims_list in missing_families.items():
                unique_dims = set(str(d) for d in dims_list)
                msg += f"\n{elem_type}:\n"
                for dim in unique_dims:
                    msg += f"  - {dim}\n"
            
            show_warning("Familias Faltantes", msg)
        
        # Resumo
        summary = f"Analise do arquivo IFC:\n"
        summary += f"- Colunas encontradas: {len(columns)}\n"
        summary += f"- Vigas encontradas: {len(beams)}\n"
        summary += f"- Pavimentos no IFC: {ifc_storeys}\n"
        summary += f"- Pavimentos no Revit: {len(revit_levels)}\n"
        
        forms.alert(summary, title="Resultado da Analise")
        
    except Exception as e:
        error_msg = traceback.format_exc()
        show_error(
            "Erro ao processar IFC",
            str(e),
            error_msg
        )


if __name__ == "__main__":
    main()
