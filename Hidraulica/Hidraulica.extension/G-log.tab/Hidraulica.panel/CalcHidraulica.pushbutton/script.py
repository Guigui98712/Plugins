# -*- coding: utf-8 -*-

import math
import traceback
import unicodedata

from pyrevit import DB, forms, revit, script
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List


FT_TO_M = 0.3048
CFS_TO_LPS = 28.3168466
# Altura da lamina d'agua acima do ponto de origem selecionado (m).
# 0.0 assume que a origem escolhida ja esta no nivel de referencia (ex.: saida da caixa).
WATER_COLUMN_ABOVE_SOURCE_M = 0.0
# Vazao usada apenas quando o ponto de saida nao tem parametro de vazao configurado.
FALLBACK_OUTLET_FLOW_LPS = 0.2
GRAVITY_KPA_PER_M = 9.81
# Palavras-chave sem acento que identificam a linha de conexoes AmancoWavin de agua fria.
BLUE_POINT_FAMILY_KEYWORDS = ["conexoesdetubos", "aguafriasoldavel"]
# Assinatura das duas pontas azuis: adaptador roscavel com bucha de latao (joelho ou reta).
BLUE_POINT_ADAPTER_KEYWORDS = ["buchadelatao", "joelho", "reta"]


def normalize_text(value):
    """Remove acentos e caixa para tornar a comparacao de nomes robusta a variacoes Unicode."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_accents.lower()


def get_element_name(element):
    """Element.Name pode ser uma interface explicita no Revit 2025 (.NET 8) e lancar AttributeError."""
    if element is None:
        return ""
    try:
        return element.Name or ""
    except AttributeError:
        try:
            return DB.Element.Name.__get__(element) or ""
        except Exception:
            return ""


def get_connectors(element):
    if isinstance(element, DB.MEPCurve):
        manager = element.ConnectorManager
    elif isinstance(element, DB.FamilyInstance) and element.MEPModel:
        manager = element.MEPModel.ConnectorManager
    else:
        return []
    return list(manager.Connectors) if manager else []


def connector_key(connector):
    return "{}:{}".format(connector.Owner.Id.IntegerValue, connector.Id)


class DisjointSet(object):
    def __init__(self):
        self.parents = {}

    def add(self, item):
        if item not in self.parents:
            self.parents[item] = item

    def find(self, item):
        parent = self.parents[item]
        while parent != self.parents[parent]:
            parent = self.parents[parent]
        self.parents[item] = parent
        return parent

    def union(self, first, second):
        root_first = self.find(first)
        root_second = self.find(second)
        if root_first != root_second:
            self.parents[root_second] = root_first


def to_table_rows(records, columns):
    """output.print_table espera linhas como listas na ordem das colunas, nao dicts."""
    return [[record.get(column, "") for column in columns] for record in records]


def get_piping_system_label(source_element):
    """Nome do sistema do trecho de origem, usado so como titulo do relatorio."""
    try:
        system = source_element.MEPSystem
        if system is not None:
            label = get_element_name(system)
            if label:
                return label
    except Exception:
        pass
    return "Rede a partir do elemento {}".format(source_element.Id.IntegerValue)


def choose_source(document):
    forms.alert(
        "Selecione no modelo o primeiro cano que sai da caixa d'agua.\n\n"
        "A partir dele o plugin percorre sozinho toda a rede conectada.",
        title="Calculadora Hidraulica - Origem",
        warn_icon=False,
    )
    try:
        reference = revit.uidoc.Selection.PickObject(
            ObjectType.Element, "Selecione o primeiro cano apos a caixa d'agua"
        )
    except Exception:
        return None, None

    selected = document.GetElement(reference.ElementId)
    if selected is None:
        return None, None

    connectors = get_connectors(selected)
    if not connectors:
        forms.alert("O elemento selecionado nao possui conectores MEP.",
                    title="Calculadora Hidraulica")
        return None, None

    picked_point = getattr(reference, "GlobalPoint", None)
    if picked_point:
        connectors.sort(key=lambda item: item.Origin.DistanceTo(picked_point))
    return selected, connectors[0]


def collect_reachable_elements(source_element):
    """Segue os conectores a partir da origem para achar toda a rede fisica conectada."""
    visited_ids = set([source_element.Id.IntegerValue])
    elements = [source_element]
    queue = [source_element]
    while queue:
        current = queue.pop(0)
        for connector in get_connectors(current):
            try:
                refs = connector.AllRefs
            except Exception:
                continue
            for ref in refs:
                try:
                    if ref.Domain != DB.Domain.DomainPiping:
                        continue
                except Exception:
                    pass
                owner = ref.Owner
                if owner is None:
                    continue
                owner_id = owner.Id.IntegerValue
                if owner_id in visited_ids:
                    continue
                visited_ids.add(owner_id)
                elements.append(owner)
                queue.append(owner)
    return elements


def is_outlet(element):
    if not isinstance(element, DB.FamilyInstance):
        return False
    symbol = element.Symbol
    if symbol is None:
        return False

    combined_name = normalize_text("{} {}".format(symbol.FamilyName or "", get_element_name(symbol)))
    if not all(keyword in combined_name for keyword in BLUE_POINT_FAMILY_KEYWORDS):
        return False
    if not any(keyword in combined_name for keyword in BLUE_POINT_ADAPTER_KEYWORDS):
        return False

    for connector in get_connectors(element):
        try:
            if connector.Domain == DB.Domain.DomainPiping:
                return True
        except Exception:
            continue
    return False


def find_near_miss_families(network_elements):
    """Lista familias da linha AmancoWavin que nao bateram no filtro, para ajudar a depurar."""
    seen = set()
    near_misses = []
    for element in network_elements:
        if not isinstance(element, DB.FamilyInstance) or is_outlet(element):
            continue
        symbol = element.Symbol
        if symbol is None:
            continue
        combined_name = normalize_text("{} {}".format(symbol.FamilyName or "", get_element_name(symbol)))
        if not all(keyword in combined_name for keyword in BLUE_POINT_FAMILY_KEYWORDS):
            continue
        label = "{} / {}".format(symbol.FamilyName, get_element_name(symbol))
        if label not in seen:
            seen.add(label)
            near_misses.append(label)
    return near_misses


def get_min_pressure(element):
    name = "{} {}".format(get_element_name(element), element.Symbol.FamilyName or "").lower()
    if "chuveiro" in name or "ducha" in name:
        return 100.0
    if "torneira" in name or "lavatorio" in name or "lavat" in name:
        return 50.0
    if "vaso" in name or "bacia" in name:
        return 70.0
    return 100.0


def get_material(pipe):
    try:
        pipe_type = pipe.PipeType
        parameter = pipe_type.get_Parameter(DB.BuiltInParameter.RBS_PIPE_MATERIAL_PARAM)
        if parameter and parameter.HasValue:
            material = pipe.Document.GetElement(parameter.AsElementId())
            if material:
                return get_element_name(material)
        return get_element_name(pipe_type)
    except Exception:
        return "Nao informado"


def get_hazen_williams_c(material):
    normalized = (material or "").lower()
    if any(item in normalized for item in ["pvc", "ppr", "pe", "polietileno"]):
        return 150.0
    if "cobre" in normalized:
        return 140.0
    if "ferro fundido" in normalized:
        return 100.0
    if "aco carbono" in normalized or "aço carbono" in normalized:
        return 110.0
    return 120.0


def get_outlet_flow_lps(element, fallback_flow_lps):
    parameter_names = ["Vazao de Projeto", "Vazao", "Flow", "Flow Rate"]
    for parameter_name in parameter_names:
        try:
            parameter = element.LookupParameter(parameter_name)
            if parameter and parameter.HasValue:
                value = parameter.AsDouble() * CFS_TO_LPS
                if value > 0:
                    return value, False
        except Exception:
            continue

    try:
        parameter = element.get_Parameter(DB.BuiltInParameter.RBS_PIPE_FLOW_PARAM)
        if parameter and parameter.HasValue:
            value = parameter.AsDouble() * CFS_TO_LPS
            if value > 0:
                return value, False
    except Exception:
        pass
    return fallback_flow_lps, True


def equivalent_length_m(fitting_name, diameter_mm):
    name = (fitting_name or "").lower()
    small = diameter_mm <= 25
    medium = diameter_mm <= 50
    if "90" in name or "cotovelo" in name or "curva" in name:
        return 0.6 if small else 1.2 if medium else 2.1
    if "tee" in name or "te " in name or "tê" in name:
        return 0.9 if small else 1.8 if medium else 3.1
    if "registro" in name or "gaveta" in name or "valvula" in name or "válvula" in name:
        return 0.2 if small else 0.4 if medium else 0.7
    return 0.5


def get_fitting_equivalent_length(pipe, diameter_mm):
    total = 0.0
    for connector in get_connectors(pipe):
        for reference in connector.AllRefs:
            owner = reference.Owner
            if owner and owner.Id != pipe.Id and not isinstance(owner, Pipe):
                total += equivalent_length_m(get_element_name(owner), diameter_mm)
    return total


def calculate_loss_m(flow_lps, diameter_mm, length_m, c_factor):
    if flow_lps <= 0 or diameter_mm <= 0 or length_m <= 0:
        return 0.0
    flow_m3s = flow_lps / 1000.0
    diameter_m = diameter_mm / 1000.0
    return 10.67 * math.pow(flow_m3s, 1.852) * length_m / (
        math.pow(c_factor, 1.852) * math.pow(diameter_m, 4.87)
    )


def build_network(document, system_elements, source_connector, fallback_flow_lps):
    connectors = []
    connector_by_key = {}
    sets = DisjointSet()
    for element in system_elements:
        for connector in get_connectors(element):
            key = connector_key(connector)
            connectors.append(connector)
            connector_by_key[key] = connector
            sets.add(key)

    for connector in connectors:
        current_key = connector_key(connector)
        for reference in connector.AllRefs:
            reference_key = connector_key(reference)
            if reference_key in connector_by_key:
                sets.union(current_key, reference_key)

    # A fitting connects its own ports; it is a junction rather than a pipe segment.
    for element in system_elements:
        if isinstance(element, Pipe):
            continue
        element_connectors = get_connectors(element)
        if len(element_connectors) > 1:
            first_key = connector_key(element_connectors[0])
            for connector in element_connectors[1:]:
                sets.union(first_key, connector_key(connector))

    node_by_root = {}
    connector_node_ids = {}
    for key in connector_by_key:
        root = sets.find(key)
        if root not in node_by_root:
            node_by_root[root] = "NODE_{:03d}".format(len(node_by_root) + 1)
        connector_node_ids[key] = node_by_root[root]

    segments = []
    adjacency = {}
    for pipe in [item for item in system_elements if isinstance(item, Pipe)]:
        pipe_connectors = get_connectors(pipe)
        if len(pipe_connectors) != 2:
            continue
        start_node = connector_node_ids[connector_key(pipe_connectors[0])]
        end_node = connector_node_ids[connector_key(pipe_connectors[1])]
        if start_node == end_node:
            continue

        diameter_mm = pipe.Diameter * 304.8
        length_m = pipe.Location.Curve.Length * FT_TO_M
        material = get_material(pipe)
        segment = {
            "id": pipe.Id.IntegerValue,
            "name": get_element_name(pipe) or "Tubo {}".format(pipe.Id.IntegerValue),
            "start": start_node,
            "end": end_node,
            "start_elevation": pipe_connectors[0].Origin.Z * FT_TO_M,
            "end_elevation": pipe_connectors[1].Origin.Z * FT_TO_M,
            "diameter_mm": diameter_mm,
            "length_m": length_m,
            "flow_lps": 0.0,
            "flow_is_estimated": False,
            "material": material,
            "c_factor": get_hazen_williams_c(material),
            "equivalent_length_m": get_fitting_equivalent_length(pipe, diameter_mm),
        }
        segments.append(segment)
        adjacency.setdefault(start_node, []).append(segment)
        adjacency.setdefault(end_node, []).append(segment)

    source_node = connector_node_ids.get(connector_key(source_connector))
    outlets = []
    for element in system_elements:
        if not is_outlet(element):
            continue
        fixture_connectors = get_connectors(element)
        if not fixture_connectors:
            continue
        outlet_node = connector_node_ids.get(connector_key(fixture_connectors[0]))
        if outlet_node:
            flow_lps, used_fallback = get_outlet_flow_lps(element, fallback_flow_lps)
            outlets.append({
                "element": element,
                "node": outlet_node,
                "name": get_element_name(element) or "Ponto {}".format(element.Id.IntegerValue),
                "minimum_kpa": get_min_pressure(element),
                "elevation_m": fixture_connectors[0].Origin.Z * FT_TO_M,
                "flow_lps": flow_lps,
                "flow_is_estimated": used_fallback,
            })
    return segments, adjacency, source_node, outlets


def assign_flows_from_outlets(segments, adjacency, source_node, outlets):
    parents = {source_node: (None, None)}
    traversal = [source_node]
    queue = [source_node]
    while queue:
        current_node = queue.pop(0)
        for segment in adjacency.get(current_node, []):
            next_node = segment["end"] if segment["start"] == current_node else segment["start"]
            if next_node in parents:
                continue
            parents[next_node] = (current_node, segment)
            traversal.append(next_node)
            queue.append(next_node)

    node_flow = dict((node, 0.0) for node in traversal)
    node_has_estimated_flow = dict((node, False) for node in traversal)
    unreachable_outlets = []
    for outlet in outlets:
        if outlet["node"] not in node_flow:
            unreachable_outlets.append(outlet)
            continue
        node_flow[outlet["node"]] += outlet["flow_lps"]
        node_has_estimated_flow[outlet["node"]] |= outlet["flow_is_estimated"]

    for node in reversed(traversal):
        parent_node, segment = parents[node]
        if segment is None:
            continue
        segment["flow_lps"] = node_flow[node]
        segment["flow_is_estimated"] = node_has_estimated_flow[node]
        node_flow[parent_node] += node_flow[node]
        node_has_estimated_flow[parent_node] |= node_has_estimated_flow[node]

    return parents, unreachable_outlets


def analyze_network(segments, adjacency, source_node, source_pressure_kpa):
    pressures = {source_node: source_pressure_kpa}
    parents = {source_node: None}
    results = []
    queue = [source_node]
    while queue:
        current_node = queue.pop(0)
        for segment in adjacency.get(current_node, []):
            next_node = segment["end"] if segment["start"] == current_node else segment["start"]
            if next_node in parents:
                continue

            entering_at_start = segment["start"] == current_node
            initial_elevation = segment["start_elevation"] if entering_at_start else segment["end_elevation"]
            final_elevation = segment["end_elevation"] if entering_at_start else segment["start_elevation"]
            distributed_m = calculate_loss_m(
                segment["flow_lps"], segment["diameter_mm"], segment["length_m"], segment["c_factor"]
            )
            localized_m = calculate_loss_m(
                segment["flow_lps"], segment["diameter_mm"],
                segment["equivalent_length_m"], segment["c_factor"]
            )
            elevation_m = final_elevation - initial_elevation
            total_loss_kpa = (distributed_m + localized_m + elevation_m) * GRAVITY_KPA_PER_M
            final_pressure = pressures[current_node] - total_loss_kpa
            pressures[next_node] = final_pressure
            parents[next_node] = current_node
            velocity = (segment["flow_lps"] / 1000.0) / (
                math.pi * math.pow(segment["diameter_mm"] / 1000.0, 2) / 4.0
            )
            results.append({
                "Trecho": segment["name"],
                "Diametro (mm)": round(segment["diameter_mm"], 1),
                "Comprimento (m)": round(segment["length_m"], 2),
                "Vazao (L/s)": round(segment["flow_lps"], 3),
                "Velocidade (m/s)": round(velocity, 2),
                "Perda dist. (kPa)": round(distributed_m * GRAVITY_KPA_PER_M, 2),
                "Perda loc. (kPa)": round(localized_m * GRAVITY_KPA_PER_M, 2),
                "Perda elev. (kPa)": round(elevation_m * GRAVITY_KPA_PER_M, 2),
                "Pressao final (kPa)": round(final_pressure, 2),
                "Vazao estimada": "Sim" if segment["flow_is_estimated"] else "Nao",
            })
            queue.append(next_node)
    return pressures, parents, results


def name_outlets(outlets):
    named = 0
    skipped = 0
    with revit.Transaction("Calculadora Hidraulica - Nomear Pontos"):
        for index, outlet in enumerate(outlets, 1):
            mark = outlet["element"].get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            if mark and not mark.IsReadOnly and not (mark.AsString() or "").strip():
                mark.Set("HP-{:03d}".format(index))
                outlet["name"] = "HP-{:03d}".format(index)
                named += 1
            else:
                skipped += 1
    return named, skipped


def main():
    document = revit.doc
    output = script.get_output()

    source_element, source_connector = choose_source(document)
    if source_connector is None:
        return

    network_elements = collect_reachable_elements(source_element)
    source_pressure = WATER_COLUMN_ABOVE_SOURCE_M * GRAVITY_KPA_PER_M

    segments, adjacency, source_node, outlets = build_network(
        document, network_elements, source_connector, FALLBACK_OUTLET_FLOW_LPS
    )
    if not segments or source_node is None:
        forms.alert("Nao foi possivel montar trechos conectados a partir do ponto de origem selecionado.",
                    title="Calculadora Hidraulica")
        return

    _, unreachable_outlets = assign_flows_from_outlets(
        segments, adjacency, source_node, outlets
    )
    pressures, _, segment_results = analyze_network(
        segments, adjacency, source_node, source_pressure
    )
    named, skipped = name_outlets(outlets)

    point_results = []
    critical_ids = []
    for outlet in outlets:
        pressure = pressures.get(outlet["node"])
        status = "NAO ALCANCADO"
        if pressure is not None:
            status = "CRITICO" if pressure < outlet["minimum_kpa"] else "OK"
        if status == "CRITICO":
            critical_ids.append(outlet["element"].Id)
        point_results.append({
            "Ponto": outlet["name"],
            "Elevacao (m)": round(outlet["elevation_m"], 2),
            "Vazao (L/s)": round(outlet["flow_lps"], 3),
            "Pressao (kPa)": "-" if pressure is None else round(pressure, 2),
            "Pressao (mca)": "-" if pressure is None else round(pressure / GRAVITY_KPA_PER_M, 2),
            "Minimo (kPa)": round(outlet["minimum_kpa"], 2),
            "Status": status,
        })

    output.set_title("Calculadora Hidraulica")
    output.print_md("# {}".format(get_piping_system_label(source_element)))
    output.print_md("Referencia na origem selecionada: **{:.2f} kPa** ({:.2f} mca) | Trechos: **{}** | Pontos de saida: **{}** | Criticos: **{}**".format(
        source_pressure, source_pressure / GRAVITY_KPA_PER_M, len(segment_results), len(point_results), len(critical_ids)
    ))
    output.print_md(
        "Pressao calculada apenas pela diferenca de cota, diametros e perdas por atrito/acessorios a partir da origem selecionada. "
        "Pontos sem vazao configurada na familia usam o valor padrao de {:.2f} L/s.".format(FALLBACK_OUTLET_FLOW_LPS)
    )
    output.print_md("## Perdas por trecho")
    segment_columns = [
        "Trecho", "Diametro (mm)", "Comprimento (m)", "Vazao (L/s)", "Velocidade (m/s)",
        "Perda dist. (kPa)", "Perda loc. (kPa)", "Perda elev. (kPa)", "Pressao final (kPa)", "Vazao estimada",
    ]
    output.print_table(to_table_rows(segment_results, segment_columns), columns=segment_columns)
    output.print_md("## Pontos de saida")
    if point_results:
        point_columns = [
            "Ponto", "Elevacao (m)", "Vazao (L/s)", "Pressao (kPa)", "Pressao (mca)", "Minimo (kPa)", "Status",
        ]
        output.print_table(to_table_rows(point_results, point_columns), columns=point_columns)
    else:
        near_misses = find_near_miss_families(network_elements)
        output.print_md("**Nenhum ponto azul foi reconhecido na rede conectada a partir da origem selecionada.**")
        if near_misses:
            output.print_md("Familias da linha AmancoWavin encontradas no percurso, mas nao reconhecidas como ponto de saida:")
            for label in near_misses:
                output.print_md("- {}".format(label))
        else:
            output.print_md("Nenhuma familia da linha AmancoWavin foi encontrada no percurso conectado a partir da origem.")
    if unreachable_outlets:
        output.print_md("**Atencao:** {} ponto(s) de saida nao possuem percurso conectado ate a origem selecionada.".format(
            len(unreachable_outlets)
        ))
    output.print_md("Pontos nomeados em `Marca`: **{}** | Pontos preservados por ja terem Marca: **{}**".format(named, skipped))

    if critical_ids:
        revit.uidoc.Selection.SetElementIds(List[DB.ElementId](critical_ids))
        forms.alert("{} ponto(s) critico(s) foram selecionados na vista ativa. Veja a tabela no painel PyRevit.".format(
            len(critical_ids)
        ), title="Calculadora Hidraulica", warn_icon=True)
    else:
        forms.alert("Analise concluida. Nenhum ponto de saida esta abaixo da pressao minima configurada.",
                    title="Calculadora Hidraulica", warn_icon=False)


try:
    main()
except Exception as error:
    forms.alert("Erro na analise hidraulica:\n{}\n\n{}".format(error, traceback.format_exc()),
                title="Calculadora Hidraulica")