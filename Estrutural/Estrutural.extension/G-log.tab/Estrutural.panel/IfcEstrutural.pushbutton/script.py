# -*- coding: utf-8 -*-

import os
import re
import traceback
import unicodedata

from pyrevit import DB, forms, revit
from Autodesk.Revit.DB.Structure import StructuralType

try:
    import ifcopenshell
    import ifcopenshell.util.element as ifc_element
    import ifcopenshell.util.placement as ifc_placement
    import ifcopenshell.util.unit as ifc_unit
    IFC_IMPORT_ERROR = None
except Exception as import_exc:
    ifcopenshell = None
    ifc_element = None
    ifc_placement = None
    ifc_unit = None
    IFC_IMPORT_ERROR = import_exc


doc = revit.doc


def pick_ifc_path():
    try:
        return forms.pick_file(file_ext="ifc")
    except Exception:
        return forms.pick_file()


def read_text_file(file_path):
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(file_path, "r", encoding=encoding) as handle:
                return handle.read()
        except Exception:
            pass
    raise IOError("Nao foi possivel ler o arquivo IFC.")


def count_ifc_building_storeys(ifc_text):
    return len(re.findall(r"\bIFCBUILDINGSTOREY\s*\(", ifc_text, flags=re.IGNORECASE))


def normalize_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def normalize_key(value):
    text = normalize_text(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def format_mm(value):
    return int(round(value))


def extract_numeric_values(text):
    if not text:
        return []
    normalized = str(text).replace(",", ".")
    return [float(match) for match in re.findall(r"\d+(?:\.\d+)?", normalized)]


def parse_pair_text(text):
    if not text:
        return None
    normalized = str(text).replace(",", ".").lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*([x/])\s*(\d+(?:\.\d+)?)", normalized)
    if match is None:
        return None
    first = float(match.group(1))
    second = float(match.group(3))
    if first <= 100.0 and second <= 100.0:
        first *= 10.0
        second *= 10.0
    return ("rect", tuple(sorted((format_mm(first), format_mm(second)))))


def parse_circle_text(text):
    if not text:
        return None
    normalized = str(text).replace(",", ".").lower()
    match = re.search(r"(?:ø|dia\.?|d)\s*(\d+(?:\.\d+)?)", normalized)
    if match is None:
        return None
    diameter = float(match.group(1))
    if diameter <= 100.0:
        diameter *= 10.0
    return ("circle", (format_mm(diameter),))


def parse_steel_text(text):
    if not text:
        return None
    normalized = normalize_key(text)
    match = re.search(r"(w\d+x\d+|ipe\d+|hea\d+|heb\d+|hem\d+|ub\d+|uc\d+|hss\d+x\d+)", normalized)
    if match is None:
        return None
    return ("steel", (match.group(1),))


def get_text_match_keys(text):
    keys = set()
    if not text:
        return keys
    normalized = normalize_key(text)
    if normalized:
        keys.add(("name", normalized))

    pair_signature = parse_pair_text(text)
    if pair_signature is not None:
        keys.add(pair_signature)

    circle_signature = parse_circle_text(text)
    if circle_signature is not None:
        keys.add(circle_signature)

    steel_signature = parse_steel_text(text)
    if steel_signature is not None:
        keys.add(steel_signature)

    return keys


def get_text_match_candidates(text):
    candidates = []
    if not text:
        return candidates

    normalized = normalize_key(text)
    if normalized:
        candidates.append(("name", normalized))

    pair_signature = parse_pair_text(text)
    if pair_signature is not None:
        candidates.append(pair_signature)

    circle_signature = parse_circle_text(text)
    if circle_signature is not None:
        candidates.append(circle_signature)

    steel_signature = parse_steel_text(text)
    if steel_signature is not None:
        candidates.append(steel_signature)

    return candidates


def get_revit_levels(document):
    levels = DB.FilteredElementCollector(document).OfClass(DB.Level).WhereElementIsNotElementType()
    return [level for level in levels if level is not None]


def level_label(level):
    try:
        elevation_mm = DB.UnitUtils.ConvertFromInternalUnits(level.Elevation, DB.UnitTypeId.Millimeters)
        return "{} ({:.0f} mm)".format(level.Name, elevation_mm)
    except Exception:
        return level.Name


def get_ifc_unit_scale(model):
    if ifc_unit is None:
        return 1.0
    try:
        return ifc_unit.calculate_unit_scale(model)
    except Exception:
        return 1.0


def ifc_to_internal_length(value, unit_scale):
    meters = float(value) * unit_scale
    return DB.UnitUtils.ConvertToInternalUnits(meters, DB.UnitTypeId.Meters)


def internal_to_mm(value):
    return DB.UnitUtils.ConvertFromInternalUnits(value, DB.UnitTypeId.Millimeters)


def ifc_point_to_xyz(coords, unit_scale):
    x = ifc_to_internal_length(coords[0], unit_scale)
    y = ifc_to_internal_length(coords[1], unit_scale)
    z = ifc_to_internal_length(coords[2] if len(coords) > 2 else 0.0, unit_scale)
    return DB.XYZ(x, y, z)


def ifc_vector_to_xyz(coords):
    vector = DB.XYZ(float(coords[0]), float(coords[1]), float(coords[2] if len(coords) > 2 else 0.0))
    if vector.GetLength() <= 1e-9:
        return None
    return vector.Normalize()


def get_ifc_storeys(model):
    return [storey for storey in model.by_type("IfcBuildingStorey") if storey is not None]


def get_ifc_products(model):
    columns = model.by_type("IfcColumn")
    beams = model.by_type("IfcBeam")
    return columns, beams


def get_product_storey(product):
    try:
        for rel in getattr(product, "ContainedInStructure", []) or []:
            structure = getattr(rel, "RelatingStructure", None)
            if structure is not None and structure.is_a("IfcBuildingStorey"):
                return structure
    except Exception:
        pass
    return None


def get_product_matrix(product):
    if ifc_placement is None:
        return None
    try:
        placement = getattr(product, "ObjectPlacement", None)
        if placement is None:
            return None
        return ifc_placement.get_local_placement(placement)
    except Exception:
        return None


def matrix_point(matrix, coords, unit_scale):
    x = (matrix[0][0] * coords[0]) + (matrix[0][1] * coords[1]) + (matrix[0][2] * coords[2]) + matrix[0][3]
    y = (matrix[1][0] * coords[0]) + (matrix[1][1] * coords[1]) + (matrix[1][2] * coords[2]) + matrix[1][3]
    z = (matrix[2][0] * coords[0]) + (matrix[2][1] * coords[1]) + (matrix[2][2] * coords[2]) + matrix[2][3]
    return ifc_point_to_xyz((x, y, z), unit_scale)


def matrix_vector(matrix, coords):
    return ifc_vector_to_xyz(
        (
            (matrix[0][0] * coords[0]) + (matrix[0][1] * coords[1]) + (matrix[0][2] * coords[2]),
            (matrix[1][0] * coords[0]) + (matrix[1][1] * coords[1]) + (matrix[1][2] * coords[2]),
            (matrix[2][0] * coords[0]) + (matrix[2][1] * coords[1]) + (matrix[2][2] * coords[2]),
        )
    )


def get_curve_points(curve_item):
    if curve_item is None:
        return None

    try:
        if curve_item.is_a("IfcPolyline"):
            return [tuple(point.Coordinates) for point in curve_item.Points]
    except Exception:
        pass

    try:
        if curve_item.is_a("IfcTrimmedCurve"):
            return get_curve_points(curve_item.BasisCurve)
    except Exception:
        pass

    try:
        if curve_item.is_a("IfcCompositeCurve"):
            points = []
            for segment in curve_item.Segments:
                seg_points = get_curve_points(segment.ParentCurve)
                if seg_points:
                    if not points:
                        points.extend(seg_points)
                    else:
                        points.extend(seg_points[1:])
            if points:
                return points
    except Exception:
        pass

    try:
        if curve_item.is_a("IfcIndexedPolyCurve"):
            coord_list = getattr(getattr(curve_item, "Points", None), "CoordList", None)
            if coord_list:
                return [tuple(coords) for coords in coord_list]
    except Exception:
        pass

    return None


def get_product_axis_points(product):
    representation = getattr(product, "Representation", None)
    if representation is None:
        return None

    for shape_rep in getattr(representation, "Representations", []) or []:
        identifier = normalize_text(getattr(shape_rep, "RepresentationIdentifier", ""))
        if identifier not in ("axis", "reference"):
            continue
        for item in getattr(shape_rep, "Items", []) or []:
            points = get_curve_points(item)
            if points and len(points) >= 2:
                return points
    return None


def get_numeric_from_product(product, keys):
    try:
        info = product.get_info()
        for key in keys:
            value = info.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
    except Exception:
        pass

    try:
        psets = ifc_element.get_psets(product)
        for pset in psets.values():
            if not isinstance(pset, dict):
                continue
            for key in keys:
                value = pset.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return float(value)
    except Exception:
        pass

    return None


def inspect_profile(profile, unit_scale):
    if profile is None:
        return None

    profile_type = normalize_text(getattr(profile, "is_a", lambda: "")())

    try:
        if profile_type == "ifcrectangleprofiledef":
            x_dim = format_mm(internal_to_mm(ifc_to_internal_length(profile.XDim, unit_scale)))
            y_dim = format_mm(internal_to_mm(ifc_to_internal_length(profile.YDim, unit_scale)))
            return ("rect", tuple(sorted((x_dim, y_dim))))

        if profile_type == "ifccircleprofiledef":
            diameter = format_mm(internal_to_mm(ifc_to_internal_length(profile.Radius, unit_scale) * 2.0))
            return ("circle", (diameter,))

        if profile_type in ("ifcishapeprofiledef", "ifctshapeprofiledef", "ifcushapeprofiledef", "ifclshapeprofiledef", "ifczshapeprofiledef", "ifcrightangleprofiledef"):
            width = getattr(profile, "OverallWidth", None)
            depth = getattr(profile, "OverallDepth", None)
            if width is not None and depth is not None:
                w_mm = format_mm(internal_to_mm(ifc_to_internal_length(width, unit_scale)))
                d_mm = format_mm(internal_to_mm(ifc_to_internal_length(depth, unit_scale)))
                return ("rect", tuple(sorted((w_mm, d_mm))))

        if profile_type == "ifcrectangularhollowprofiledef":
            x_dim = format_mm(internal_to_mm(ifc_to_internal_length(profile.XDim, unit_scale)))
            y_dim = format_mm(internal_to_mm(ifc_to_internal_length(profile.YDim, unit_scale)))
            return ("rect", tuple(sorted((x_dim, y_dim))))
    except Exception:
        return None

    return None


def inspect_representation_item(item, unit_scale):
    if item is None:
        return None

    try:
        if item.is_a("IfcMappedItem"):
            mapped_source = getattr(item.MappingSource, "MappedRepresentation", None)
            if mapped_source is not None:
                for mapped_item in getattr(mapped_source, "Items", []) or []:
                    signature = inspect_representation_item(mapped_item, unit_scale)
                    if signature is not None:
                        return signature
    except Exception:
        pass

    try:
        if item.is_a("IfcExtrudedAreaSolid"):
            return inspect_profile(item.SweptArea, unit_scale)
    except Exception:
        pass

    try:
        if item.is_a("IfcSweptDiskSolid"):
            radius = getattr(item, "Radius", None)
            if radius is not None:
                diameter = format_mm(ifc_to_internal_length(radius, unit_scale) * 2.0 / DB.UnitUtils.ConvertToInternalUnits(1.0, DB.UnitTypeId.Meters))
                return ("circle", (diameter,))
    except Exception:
        pass

    try:
        if item.is_a("IfcBooleanResult"):
            left = inspect_representation_item(item.FirstOperand, unit_scale)
            if left is not None:
                return left
            return inspect_representation_item(item.SecondOperand, unit_scale)
    except Exception:
        pass

    return None


def get_product_section_signature(product, unit_scale):
    representation = getattr(product, "Representation", None)
    if representation is None:
        return None

    for shape_rep in getattr(representation, "Representations", []) or []:
        identifier = normalize_text(getattr(shape_rep, "RepresentationIdentifier", ""))
        if identifier and identifier not in ("body", "model", "reference"):
            continue
        for item in getattr(shape_rep, "Items", []) or []:
            signature = inspect_representation_item(item, unit_scale)
            if signature is not None:
                return signature
    return None


def get_product_text_candidates(product):
    keys = []
    try:
        info = product.get_info()
        for key in ("Name", "ObjectType", "Tag", "PredefinedType"):
            value = info.get(key)
            keys.extend(get_text_match_candidates(value))
    except Exception:
        pass

    try:
        psets = ifc_element.get_psets(product)
        for pset in psets.values():
            if not isinstance(pset, dict):
                continue
            for key in ("ProfileName", "Reference", "Name", "Designation", "SectionName"):
                value = pset.get(key)
                keys.extend(get_text_match_candidates(value))
    except Exception:
        pass

    return keys


def get_product_match_keys(product, unit_scale):
    keys = []
    section_signature = get_product_section_signature(product, unit_scale)
    if section_signature is not None:
        keys.append(section_signature)
    keys.extend(get_product_text_candidates(product))
    return keys


def get_product_beam_line(product, unit_scale):
    matrix = get_product_matrix(product)
    if matrix is None:
        return None

    axis_points = get_product_axis_points(product)
    if axis_points:
        start = matrix_point(matrix, axis_points[0], unit_scale)
        end = matrix_point(matrix, axis_points[-1], unit_scale)
        if start.DistanceTo(end) > 1e-6:
            return DB.Line.CreateBound(start, end)

    length = get_numeric_from_product(product, ["Length", "OverallLength", "NetLength"])
    if length is None:
        return None

    origin = matrix_point(matrix, (0.0, 0.0, 0.0), unit_scale)
    axis = matrix_vector(matrix, (1.0, 0.0, 0.0))
    if axis is None:
        return None

    half_length = ifc_to_internal_length(length * 0.5, unit_scale)
    start = origin - (axis * half_length)
    end = origin + (axis * half_length)
    return DB.Line.CreateBound(start, end)


def get_product_column_point(product, unit_scale):
    matrix = get_product_matrix(product)
    if matrix is None:
        return None
    return matrix_point(matrix, (0.0, 0.0, 0.0), unit_scale)


def get_level_for_product(product, storey_level_map, fallback_levels, unit_scale):
    storey = get_product_storey(product)
    if storey is not None:
        mapped_level = storey_level_map.get(storey.id())
        if mapped_level is not None:
            return mapped_level

    matrix = get_product_matrix(product)
    if matrix is None or not fallback_levels:
        return None

    z_value = ifc_to_internal_length(matrix[2][3], unit_scale)
    nearest_level = None
    nearest_distance = None
    for level in fallback_levels:
        distance = abs(level.Elevation - z_value)
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest_level = level
    return nearest_level


def build_storey_level_map(storeys, levels):
    sorted_storeys = sorted(storeys, key=lambda storey: getattr(storey, "Elevation", 0.0) or 0.0)
    sorted_levels = sorted(levels, key=lambda level: level.Elevation)

    if len(sorted_storeys) > len(sorted_levels):
        return None, "O IFC possui mais storeys do que levels no Revit."

    level_by_name = {}
    for level in sorted_levels:
        level_by_name[normalize_text(level.Name)] = level

    used_levels = set()
    storey_level_map = {}

    for storey in sorted_storeys:
        storey_name = normalize_text(getattr(storey, "Name", ""))
        mapped_level = level_by_name.get(storey_name)
        if mapped_level is not None and mapped_level not in used_levels:
            storey_level_map[storey.id()] = mapped_level
            used_levels.add(mapped_level)
            continue

        for level in sorted_levels:
            if level not in used_levels:
                storey_level_map[storey.id()] = level
                used_levels.add(level)
                break

    return storey_level_map, None


def get_first_symbol(document, bic):
    collector = DB.FilteredElementCollector(document).OfClass(DB.FamilySymbol).OfCategory(bic)
    for symbol in collector:
        return symbol
    return None


def build_symbol_lookup(document, bic):
    lookup = {}
    collector = DB.FilteredElementCollector(document).OfClass(DB.FamilySymbol).OfCategory(bic)
    for symbol in collector:
        for key in get_text_match_keys("{} {}".format(getattr(symbol.Family, "Name", ""), getattr(symbol, "Name", ""))):
            if key not in lookup:
                lookup[key] = symbol
    return lookup


def activate_symbol(document, symbol):
    if symbol is None:
        return
    try:
        if not symbol.IsActive:
            symbol.Activate()
            document.Regenerate()
    except Exception:
        pass


def symbol_label(symbol):
    try:
        return "{}:{}".format(symbol.Family.Name, symbol.Name)
    except Exception:
        return symbol.Name


def create_column_instance(document, symbol, level, point):
    return document.Create.NewFamilyInstance(point, symbol, level, StructuralType.Column)


def create_beam_instance(document, symbol, level, line):
    return document.Create.NewFamilyInstance(line, symbol, level, StructuralType.Beam)


def get_ifc_product_label(product):
    name = getattr(product, "Name", None)
    if name:
        return str(name)
    try:
        return product.GlobalId
    except Exception:
        return "Sem nome"


def get_ifc_product_display_text(product, unit_scale):
    texts = []
    try:
        info = product.get_info()
        for key in ("Name", "ObjectType", "Tag", "PredefinedType"):
            value = info.get(key)
            if value:
                texts.append(str(value))
    except Exception:
        pass

    try:
        psets = ifc_element.get_psets(product)
        for pset in psets.values():
            if not isinstance(pset, dict):
                continue
            for key in ("ProfileName", "Reference", "Designation", "SectionName"):
                value = pset.get(key)
                if value:
                    texts.append(str(value))
    except Exception:
        pass

    section_signature = get_product_section_signature(product, unit_scale)
    if section_signature is not None:
        texts.append(signature_to_text(section_signature))
    return " | ".join([text for text in texts if text])


def signature_to_text(signature):
    if signature is None:
        return "desconhecido"
    kind, dims = signature
    if kind == "circle":
        return "Ø{} mm".format(dims[0])
    return "{}x{} mm".format(dims[0], dims[1])


def collect_missing_signatures(products, unit_scale, symbol_lookup, kind_label):
    missing = []
    unknown = []
    matched = {}

    for product in products:
        match_keys = get_product_match_keys(product, unit_scale)
        symbol = None
        for key in match_keys:
            symbol = symbol_lookup.get(key)
            if symbol is not None:
                break
        if symbol is None:
            display_text = get_ifc_product_display_text(product, unit_scale)
            if not display_text:
                display_text = get_ifc_product_label(product)
            if not match_keys:
                unknown.append((kind_label, display_text, "seção nao reconhecida"))
            else:
                unknown.append((kind_label, display_text, "nenhum tipo correspondente encontrado"))
            missing.append(display_text)
            continue
        matched[id(product)] = symbol

    unique_missing = []
    seen = set()
    for item in missing:
        if item in seen:
            continue
        seen.add(item)
        unique_missing.append(item)

    return matched, unique_missing, unknown


def main():
    if IFC_IMPORT_ERROR is not None:
        forms.alert(
            "Nao foi possivel carregar o suporte IFC no Python atual.\n\nDetalhe tecnico:\n{}".format(
                str(IFC_IMPORT_ERROR)
            ),
            title="IFC Estrutural",
        )
        return

    ifc_path = pick_ifc_path()
    if not ifc_path:
        forms.alert("Selecione um arquivo IFC para iniciar a importacao.", title="IFC Estrutural")
        return

    if not os.path.isfile(ifc_path):
        forms.alert("O arquivo IFC selecionado nao existe.", title="IFC Estrutural")
        return

    if not ifc_path.lower().endswith(".ifc"):
        forms.alert("O plugin por enquanto aceita apenas arquivos .ifc.", title="IFC Estrutural")
        return

    model = ifcopenshell.open(ifc_path)
    unit_scale = get_ifc_unit_scale(model)

    ifc_text = read_text_file(ifc_path)
    ifc_storey_count = count_ifc_building_storeys(ifc_text)
    ifc_storeys = get_ifc_storeys(model)
    revit_levels = get_revit_levels(doc)
    revit_level_count = len(revit_levels)

    if ifc_storey_count > revit_level_count:
        forms.alert(
            "O IFC possui mais niveis ({}) do que o Revit ({}). Crie os levels antes de converter.".format(
                ifc_storey_count, revit_level_count
            ),
            title="IFC Estrutural",
        )
        return

    if len(ifc_storeys) > revit_level_count:
        forms.alert(
            "O IFC possui mais storeys ({}) do que levels no Revit ({}).".format(
                len(ifc_storeys), revit_level_count
            ),
            title="IFC Estrutural",
        )
        return

    storey_level_map, map_error = build_storey_level_map(ifc_storeys, revit_levels)
    if map_error is not None:
        forms.alert(map_error, title="IFC Estrutural")
        return

    columns, beams = get_ifc_products(model)
    total_products = len(columns) + len(beams)
    if total_products == 0:
        forms.alert("Nao encontrei IfcColumn nem IfcBeam no arquivo IFC.", title="IFC Estrutural")
        return

    column_symbol_lookup = build_symbol_lookup(doc, DB.BuiltInCategory.OST_StructuralColumns)
    beam_symbol_lookup = build_symbol_lookup(doc, DB.BuiltInCategory.OST_StructuralFraming)

    matched_columns, missing_columns, unknown_columns = collect_missing_signatures(
        columns, unit_scale, column_symbol_lookup, "IfcColumn"
    )
    matched_beams, missing_beams, unknown_beams = collect_missing_signatures(
        beams, unit_scale, beam_symbol_lookup, "IfcBeam"
    )

    if missing_columns or missing_beams:
        summary_lines = ["Nao encontrei familias/tipos carregados para estas secoes:"]
        for description in missing_columns:
            summary_lines.append("- Pilar {}".format(description))
        for description in missing_beams:
            summary_lines.append("- Viga {}".format(description))
        summary_lines.append("")
        summary_lines.append("Carregue familias/tipos com essas secoes e execute novamente.")
        forms.alert("\n".join(summary_lines), title="IFC Estrutural")
        return

    if not matched_columns and not matched_beams:
        forms.alert("Nao foi possivel mapear pilares nem vigas do IFC.", title="IFC Estrutural")
        return

    created_columns = 0
    created_beams = 0
    skipped_items = unknown_columns + unknown_beams

    tx = DB.Transaction(doc, "Importar IFC Estrutural")
    tx.Start()
    try:
        used_symbols = set(matched_columns.values()) | set(matched_beams.values())
        for symbol in used_symbols:
            activate_symbol(doc, symbol)

        for column in columns:
            symbol = matched_columns.get(id(column))
            level = get_level_for_product(column, storey_level_map, revit_levels, unit_scale)
            point = get_product_column_point(column, unit_scale)
            if symbol is None or level is None or point is None:
                skipped_items.append(("IfcColumn", get_ifc_product_label(column), "sem symbol, level ou ponto valido"))
                continue

            try:
                create_column_instance(doc, symbol, level, point)
                created_columns += 1
            except Exception as create_exc:
                skipped_items.append(("IfcColumn", get_ifc_product_label(column), str(create_exc)))

        for beam in beams:
            symbol = matched_beams.get(id(beam))
            level = get_level_for_product(beam, storey_level_map, revit_levels, unit_scale)
            line = get_product_beam_line(beam, unit_scale)
            if symbol is None or level is None or line is None:
                skipped_items.append(("IfcBeam", get_ifc_product_label(beam), "sem symbol, level ou linha valida"))
                continue

            try:
                create_beam_instance(doc, symbol, level, line)
                created_beams += 1
            except Exception as create_exc:
                skipped_items.append(("IfcBeam", get_ifc_product_label(beam), str(create_exc)))

        tx.Commit()
    except Exception:
        try:
            tx.RollBack()
        except Exception:
            pass
        raise

    summary_lines = [
        "Arquivo IFC: {}".format(os.path.basename(ifc_path)),
        "Storeys IFC: {}".format(len(ifc_storeys)),
        "Levels Revit: {}".format(revit_level_count),
        "Pilares criados: {}".format(created_columns),
        "Vigas criadas: {}".format(created_beams),
        "Itens ignorados: {}".format(len(skipped_items)),
        "",
        "Tipos carregados para pilares: {}".format(len(column_symbol_lookup)),
        "Tipos carregados para vigas: {}".format(len(beam_symbol_lookup)),
    ]

    if skipped_items:
        summary_lines.append("")
        summary_lines.append("Primeiros itens ignorados:")
        for kind, label, reason in skipped_items[:12]:
            summary_lines.append("- {} | {} | {}".format(kind, label, reason))

    forms.alert("\n".join(summary_lines), title="IFC Estrutural", warn_icon=False)


try:
    main()
except Exception as fatal_exc:
    forms.alert(
        "Erro inesperado na importacao IFC:\n{}\n\nDetalhe tecnico:\n{}".format(
            str(fatal_exc), traceback.format_exc()
        ),
        title="IFC Estrutural",
    )
