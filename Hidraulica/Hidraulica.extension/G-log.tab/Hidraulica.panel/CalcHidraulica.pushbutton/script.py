# -*- coding: utf-8 -*-

import heapq
import math
import traceback
import unicodedata

from pyrevit import DB, forms, revit, script
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List


FT_TO_M = 0.3048
SOURCE_PIPE_SNAP_TOLERANCE_M = 0.15
# Altura da lamina d'agua acima do ponto de origem selecionado (m).
# 0.0 assume que a origem escolhida ja esta no nivel de referencia (ex.: saida da caixa).
WATER_COLUMN_ABOVE_SOURCE_M = 1.0
# Vazao usada apenas quando o ponto de saida nao tem parametro de vazao configurado.
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


def is_pipe_element(element):
    """Recognizes rigid and flexible piping curves from Revit."""
    if isinstance(element, Pipe):
        return True
    try:
        category_id = element.Category.Id.IntegerValue
        pipe_categories = [
            int(DB.BuiltInCategory.OST_PipeCurves),
            int(DB.BuiltInCategory.OST_FlexPipeCurves),
        ]
        if category_id in pipe_categories:
            return True
    except Exception:
        pass
    try:
        type_name = element.GetType().Name.lower()
        return "flexpipe" in type_name
    except Exception:
        return False


def collect_pipe_elements(document):
    """Collect rigid/flexible pipe curves even when their runtime wrapper differs."""
    result = []
    seen = set()
    try:
        candidates = DB.FilteredElementCollector(document).OfClass(Pipe).WhereElementIsNotElementType().ToElements()
        for element in candidates:
            if element.Id.IntegerValue not in seen:
                seen.add(element.Id.IntegerValue)
                result.append(element)
    except Exception:
        pass
    for category_name in ("OST_PipeCurves", "OST_FlexPipeCurves"):
        try:
            category = getattr(DB.BuiltInCategory, category_name)
            candidates = DB.FilteredElementCollector(document).OfCategory(category).WhereElementIsNotElementType().ToElements()
            for element in candidates:
                if is_pipe_element(element) and element.Id.IntegerValue not in seen:
                    seen.add(element.Id.IntegerValue)
                    result.append(element)
        except Exception:
            continue
    return result


def get_pipe_diameter_mm(pipe):
    try:
        return pipe.Diameter * 304.8
    except Exception:
        try:
            parameter = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
            return parameter.AsDouble() * 304.8 if parameter and parameter.HasValue else 0.0
        except Exception:
            return 0.0


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
    """output.print_table espera linhas como listas de strings na ordem das colunas."""
    return [[str(record.get(column, "")) for column in columns] for record in records]


def html_escape(value):
    text = str(value if value is not None else "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def render_html_table(title, records, columns):
    table_class = "route-table" if normalize_text(title) == "pecas no percurso" else "point-table"
    parts = [
        '<section class="card"><h2 class="table-title">{}</h2><div class="table-wrap"><table class="{}"><thead><tr>'.format(html_escape(title), table_class)
    ]
    for column in columns:
        parts.append("<th>{}</th>".format(html_escape(column)))
    parts.append("</tr></thead><tbody>")
    for record in records:
        parts.append("<tr>")
        for column in columns:
            value = record.get(column, "")
            if column.startswith("Status"):
                normalized = normalize_text(value)
                css_class = "ok" if normalized == "ok" else "review" if any(
                    token in normalized for token in ["revisar", "divergente", "conferir", "fallback", "solver", "negativa"]
                ) else "empty" if "sem caminho" in normalized else "ok"
                parts.append('<td><span class="status {}">{}</span></td>'.format(css_class, html_escape(value)))
            else:
                parts.append("<td>{}</td>".format(html_escape(value)))
        parts.append("</tr>")
    if not records:
        parts.append('<tr><td class="empty-row" colspan="{}">&mdash;</td></tr>'.format(len(columns)))
    parts.append("</tbody></table></div></section>")
    return "".join(parts)


def render_results_html(system_title, point_results, route_results,
                        point_columns, route_columns):
    style = """
    <style>
      body { margin:0; background:#eef3f8; color:#182b3d; font-family:'Segoe UI',Arial,sans-serif; }
      .report { max-width:1500px; margin:0 auto; padding:24px 22px 36px; }
      .report-head { background:linear-gradient(115deg,#102b43,#1d5570); border-radius:12px; padding:22px 26px; margin:0 0 22px; box-shadow:0 5px 16px rgba(20,48,70,.16); }
      h1 { color:#fff; font-size:25px; line-height:1.3; font-weight:650; letter-spacing:.2px; margin:0; overflow-wrap:anywhere; }
      .card { background:#fff; border:1px solid #d3dee8; border-radius:10px; margin:0 0 22px; overflow:hidden; box-shadow:0 3px 12px rgba(28,54,77,.07); }
      .table-title { display:block; color:#173a54; background:#eaf1f6; font-size:17px; font-weight:700; padding:14px 18px; margin:0; border-bottom:2px solid #c8d7e2; letter-spacing:.1px; }
      .table-wrap { width:100%; overflow-x:auto; }
      table { width:100%; border-collapse:separate; border-spacing:0; font-size:13px; table-layout:fixed; }
      .point-table { min-width:1160px; }
      .point-table th:nth-child(1), .point-table td:nth-child(1) { width:7%; }
      .point-table th:nth-child(2), .point-table td:nth-child(2) { width:8%; }
      .point-table th:nth-child(3), .point-table td:nth-child(3) { width:16%; }
      .point-table th:nth-child(4), .point-table td:nth-child(4) { width:9%; }
      .point-table th:nth-child(5), .point-table td:nth-child(5) { width:8%; }
      .point-table th:nth-child(6), .point-table td:nth-child(6) { width:8%; }
      .point-table th:nth-child(7), .point-table td:nth-child(7) { width:9%; }
      .point-table th:nth-child(8), .point-table td:nth-child(8) { width:10%; }
      .point-table th:nth-child(9), .point-table td:nth-child(9) { width:12%; }
      .point-table th:nth-child(10), .point-table td:nth-child(10) { width:13%; }
      .route-table { min-width:760px; }
      .route-table th:first-child, .route-table td:first-child { width:18%; }
      .route-table th:nth-child(2), .route-table td:nth-child(2) { width:34%; }
      .route-table th:nth-child(n+3), .route-table td:nth-child(n+3) { width:16%; text-align:center; }
      th { background:linear-gradient(90deg,#24445e,#2b5874); color:#fff; font-weight:650; text-align:center; padding:12px 9px; white-space:normal; line-height:1.25; vertical-align:middle; }
      td { padding:11px 9px; border-bottom:1px solid #e9eef3; vertical-align:middle; text-align:center; overflow-wrap:anywhere; }
      td:first-child { padding-left:18px; color:#173a54; font-weight:600; }
      tbody tr:nth-child(even) { background:#f7f9fc; }
      tbody tr:hover { background:#edf5fa; }
      tbody tr:last-child td { border-bottom:0; }
      .status { display:inline-block; border-radius:20px; padding:4px 10px; font-size:11px; font-weight:650; white-space:nowrap; }
      .status.ok { background:#e4f5ec; color:#176b43; }
      .status.review { background:#fff1d2; color:#855600; }
      .status.empty { background:#fde9e7; color:#a33a32; }
      .empty-row { color:#8493a3; text-align:center; padding:20px; }
    </style>
    """
    content = '<div class="report"><header class="report-head"><h1>{}</h1></header>'.format(html_escape(system_title))
    content += render_html_table(u"Pontos de sa\u00edda", point_results, point_columns)
    content += render_html_table(u"Pe\u00e7as no percurso", route_results, route_columns)
    return style + content + "</div>"


class ReportOutput(object):
    """Suprime mensagens intermediarias e encaminha apenas o relatorio HTML."""
    def __init__(self, output):
        self.output = output

    def print_md(self, _text):
        pass

    def print_table(self, _data, columns=None):
        pass

    def print_html(self, content):
        self.output.print_html(content)


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
    forms.alert("Selecione o adaptador conectado a caixa d\x27agua. Ele sera o ponto de partida do percurso.",
                title="Calculadora Hidraulica - Origem", warn_icon=False)
    try:
        reference = revit.uidoc.Selection.PickObject(
            ObjectType.Element, "Selecione o adaptador para caixa d'agua"
        )
    except Exception:
        return None, None, None

    selected = document.GetElement(reference.ElementId)
    if selected is None:
        return None, None, None

    if not isinstance(selected, DB.FamilyInstance):
        forms.alert("Selecione o adaptador da caixa d'agua, nao um tubo.",
                    title="Calculadora Hidraulica")
        return None, None, None

    piping_connectors = get_piping_connectors(selected)
    if not piping_connectors:
        forms.alert("O adaptador selecionado nao possui conectores de tubulacao. Confira a familia escolhida.",
                    title="Calculadora Hidraulica")
        return None, None, None

    pipe_connectors = []
    connected_connectors = []
    reference_counts = {}
    for connector in piping_connectors:
        try:
            references = list(connector.AllRefs)
            reference_counts[connector_key(connector)] = len(references)
            if any(is_pipe_element(ref.Owner) for ref in references):
                pipe_connectors.append(connector)
            if connector.IsConnected and references:
                connected_connectors.append(connector)
        except Exception:
            continue

    # Revit pode expor a ligacao atraves de uma conexao intermediaria e nao
    # marcar IsConnected de forma consistente. Nunca bloqueia a selecao por isso.
    source_candidates = pipe_connectors or connected_connectors or piping_connectors
    picked_point = getattr(reference, "GlobalPoint", None)
    if pipe_connectors or connected_connectors:
        if picked_point is not None:
            source_connector = min(source_candidates,
                                   key=lambda item: item.Origin.DistanceTo(picked_point))
        else:
            source_connector = max(source_candidates,
                                   key=lambda item: reference_counts.get(connector_key(item), 0))
    else:
        # Geometria e apenas fallback quando o Revit nao reporta nenhuma ligacao.
        document_pipes = collect_pipe_elements(document)
        geometric_candidates = []
        for connector in piping_connectors:
            nearby = find_nearest_pipe_connector(connector, document_pipes)
            if nearby is not None:
                geometric_candidates.append((connector, connector.Origin.DistanceTo(nearby.Origin)))
        if geometric_candidates:
            source_connector = min(geometric_candidates, key=lambda item: item[1])[0]
        elif picked_point is not None:
            source_connector = min(piping_connectors,
                                   key=lambda item: item.Origin.DistanceTo(picked_point))
        else:
            source_connector = max(piping_connectors,
                                   key=lambda item: reference_counts.get(connector_key(item), 0))
    return selected, source_connector, source_connector.Origin


def collect_reachable_elements(source_element, source_connector=None):
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
                owner = ref.Owner
                if owner is None:
                    continue
                if not is_pipe_element(owner) and not get_piping_connectors(owner):
                    continue
                owner_id = owner.Id.IntegerValue
                if owner_id in visited_ids:
                    continue
                visited_ids.add(owner_id)
                elements.append(owner)
                queue.append(owner)

    if source_connector is not None and not any(is_pipe_element(item) for item in elements):
        try:
            all_pipes = collect_pipe_elements(source_element.Document)
            nearby_pipe_connector = find_nearest_pipe_connector(source_connector, all_pipes)
            if nearby_pipe_connector is not None:
                owner = nearby_pipe_connector.Owner
                owner_id = owner.Id.IntegerValue
                if owner_id not in visited_ids:
                    visited_ids.add(owner_id)
                    elements.append(owner)
                    queue.append(owner)
                while queue:
                    current = queue.pop(0)
                    for connector in get_connectors(current):
                        try:
                            refs = connector.AllRefs
                        except Exception:
                            continue
                        for ref in refs:
                            owner = ref.Owner
                            if owner is None:
                                continue
                            owner_id = owner.Id.IntegerValue
                            if owner_id in visited_ids:
                                continue
                            if not is_pipe_element(owner) and not get_piping_connectors(owner):
                                continue
                            visited_ids.add(owner_id)
                            elements.append(owner)
                            queue.append(owner)
        except Exception:
            pass
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

    connectors = get_piping_connectors(element)
    if len(connectors) != 2:
        return False
    connected = 0
    open_count = 0
    for connector in connectors:
        try:
            if connector.IsConnected:
                connected += 1
            else:
                open_count += 1
        except Exception:
            open_count += 1
    # Pontos de consumo têm uma porta ligada à rede e uma porta terminal aberta.
    return connected == 1 and open_count == 1


def get_piping_connectors(element):
    result = []
    for connector in get_connectors(element):
        try:
            if connector.Domain == DB.Domain.DomainPiping:
                result.append(connector)
        except Exception:
            continue
    return result


def find_nearest_pipe_connector(source_connector, elements):
    """Find the nearest pipe endpoint when Revit omits an adapter reference."""
    nearest = None
    nearest_distance = float("inf")
    for element in elements:
        if not is_pipe_element(element):
            continue
        for pipe_connector in get_piping_connectors(element):
            try:
                distance = source_connector.Origin.DistanceTo(pipe_connector.Origin)
            except Exception:
                continue
            if distance < nearest_distance:
                nearest = pipe_connector
                nearest_distance = distance
    if nearest is not None and nearest_distance <= SOURCE_PIPE_SNAP_TOLERANCE_M / FT_TO_M:
        return nearest
    return None


def get_outlet_connector_pair(element):
    """Escolhe a ponta azul aberta sem depender da ordem dos conectores da familia."""
    connectors = get_piping_connectors(element)
    if not connectors:
        return None, None
    connected = []
    open_connectors = []
    for connector in connectors:
        try:
            if connector.IsConnected:
                connected.append(connector)
            else:
                open_connectors.append(connector)
        except Exception:
            open_connectors.append(connector)
    terminal = open_connectors[0] if open_connectors else connectors[-1]
    network_connector = min(connected or connectors,
                            key=lambda item: item.Origin.DistanceTo(terminal.Origin))
    return terminal, network_connector

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


def equivalent_length_m(fitting_name, diameter_mm):
    name = normalize_text(fitting_name or "")
    small = diameter_mm <= 25
    medium = diameter_mm <= 50
    if "45" in name:
        return 0.4 if small else 0.8 if medium else 1.4
    if "90" in name or "cotovelo" in name or "curva" in name or "elbow" in name:
        return 0.6 if small else 1.2 if medium else 2.1
    if "tee" in name or " te " in name or name.startswith("te ") or "derivacao" in name:
        return 0.9 if small else 1.8 if medium else 3.1
    if "registro" in name or "gaveta" in name or "valvula" in name:
        return 0.2 if small else 0.4 if medium else 0.7
    return 0.5


def get_assigned_fitting_k(fitting):
    coefficients = []
    for connector in get_piping_connectors(fitting):
        try:
            coefficient = float(connector.AssignedKCoefficient)
            if coefficient > 0.0:
                coefficients.append(coefficient)
        except Exception:
            continue
    return max(coefficients) if coefficients else 0.0


def get_pipe_fitting_k(pipe):
    total = 0.0
    seen_fittings = set()
    for connector in get_piping_connectors(pipe):
        try:
            references = connector.AllRefs
        except Exception:
            continue
        for reference in references:
            fitting = getattr(reference, "Owner", None)
            if (fitting is None or fitting.Id == pipe.Id or is_pipe_element(fitting)
                    or is_outlet(fitting)):
                continue
            fitting_id = fitting.Id.IntegerValue
            if fitting_id in seen_fittings:
                continue
            seen_fittings.add(fitting_id)
            total += 0.5 * get_assigned_fitting_k(fitting)
    return total


def get_fitting_equivalent_length(pipe, diameter_mm):
    total = 0.0
    for connector in get_piping_connectors(pipe):
        for reference in connector.AllRefs:
            try:
                owner = reference.Owner
            except Exception:
                continue
            if owner and owner.Id != pipe.Id and not is_pipe_element(owner) and not is_outlet(owner):
                if get_assigned_fitting_k(owner) > 0.0:
                    continue
                total += 0.5 * equivalent_length_m(get_fitting_label(owner), diameter_mm)
    return total


def calculate_loss_m(flow_lps, diameter_mm, length_m, c_factor):
    if flow_lps <= 0 or diameter_mm <= 0 or length_m <= 0:
        return 0.0
    flow_m3s = flow_lps / 1000.0
    diameter_m = diameter_mm / 1000.0
    return 10.67 * math.pow(flow_m3s, 1.852) * length_m / (
        math.pow(c_factor, 1.852) * math.pow(diameter_m, 4.87)
    )


def get_connected_pipe_diameter(connector, fallback_segments):
    """Prioriza o tubo ligado diretamente a porta; usa os tubos do no como reserva."""
    for reference in connector.AllRefs:
        try:
            owner = reference.Owner
            if is_pipe_element(owner):
                return get_pipe_diameter_mm(owner)
        except Exception:
            continue
    if fallback_segments:
        return min(item["diameter_mm"] for item in fallback_segments)
    return 0.0

def build_network(document, system_elements, source_connector):
    connectors = []
    connector_by_key = {}
    sets = DisjointSet()
    for element in system_elements:
        for connector in get_piping_connectors(element):
            key = connector_key(connector)
            connectors.append(connector)
            connector_by_key[key] = connector
            sets.add(key)

    for connector in connectors:
        current_key = connector_key(connector)
        try:
            references = connector.AllRefs
        except Exception:
            continue
        for reference in references:
            try:
                reference_key = connector_key(reference)
            except Exception:
                continue
            if reference_key in connector_by_key:
                sets.union(current_key, reference_key)

    # A fitting connects its own ports; it is a junction rather than a pipe segment.
    for element in system_elements:
        if is_pipe_element(element):
            continue
        element_connectors = get_piping_connectors(element)
        if len(element_connectors) > 1:
            first_key = connector_key(element_connectors[0])
            for connector in element_connectors[1:]:
                sets.union(first_key, connector_key(connector))

    source_key = connector_key(source_connector)
    source_root = sets.find(source_key)
    source_has_pipe = any(
        sets.find(connector_key(connector)) == source_root
        for element in system_elements if is_pipe_element(element)
        for connector in get_piping_connectors(element)
    )
    if not source_has_pipe:
        nearby_pipe_connector = find_nearest_pipe_connector(source_connector, system_elements)
        if nearby_pipe_connector is not None:
            sets.union(source_key, connector_key(nearby_pipe_connector))

    node_by_root = {}
    connector_node_ids = {}
    for key in connector_by_key:
        root = sets.find(key)
        if root not in node_by_root:
            node_by_root[root] = "NODE_{:03d}".format(len(node_by_root) + 1)
        connector_node_ids[key] = node_by_root[root]

    segments = []
    skipped_pipes = []
    adjacency = {}
    for pipe in [item for item in system_elements if is_pipe_element(item)]:
        pipe_connectors = get_piping_connectors(pipe)
        if len(pipe_connectors) != 2:
            skipped_pipes.append((pipe.Id.IntegerValue, len(pipe_connectors)))
            continue
        start_node = connector_node_ids[connector_key(pipe_connectors[0])]
        end_node = connector_node_ids[connector_key(pipe_connectors[1])]

        diameter_mm = get_pipe_diameter_mm(pipe)
        try:
            length_m = pipe.Location.Curve.Length * FT_TO_M
        except Exception:
            length_m = pipe_connectors[0].Origin.DistanceTo(pipe_connectors[1].Origin) * FT_TO_M
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
            "fitting_k": get_pipe_fitting_k(pipe),
        }
        segments.append(segment)
        adjacency.setdefault(start_node, []).append(segment)
        adjacency.setdefault(end_node, []).append(segment)

    source_node = connector_node_ids.get(connector_key(source_connector))
    outlets = []
    for element in system_elements:
        if not is_outlet(element):
            continue
        terminal_connector, network_connector = get_outlet_connector_pair(element)
        if terminal_connector is None or network_connector is None:
            continue
        outlet_node = connector_node_ids.get(connector_key(network_connector))
        if outlet_node:
            connected_pipes = adjacency.get(outlet_node, [])
            if not connected_pipes:
                continue
            outlets.append({
                "element": element,
                "node": outlet_node,
                "name": get_outlet_label(element),
                "connector": terminal_connector,
                "terminal_connector": terminal_connector,
                "network_connector": network_connector,
                "terminal_offset_m": terminal_connector.Origin.DistanceTo(network_connector.Origin) * FT_TO_M,
                "diameter_mm": get_connected_pipe_diameter(network_connector, connected_pipes),
            })
    return segments, adjacency, source_node, outlets, skipped_pipes


def build_shortest_path_tree(adjacency, source_node):
    """Calcula o menor percurso pela soma dos comprimentos reais de tubos."""
    distances = {source_node: 0.0}
    parents = {source_node: (None, None)}
    order = []
    sequence = 0
    pending = [(0.0, sequence, source_node)]
    while pending:
        current_distance, _, current_node = heapq.heappop(pending)
        if current_distance > distances.get(current_node, float("inf")) + 1e-9:
            continue
        order.append(current_node)
        for segment in adjacency.get(current_node, []):
            next_node = segment["end"] if segment["start"] == current_node else segment["start"]
            candidate = current_distance + segment["length_m"]
            if candidate + 1e-9 >= distances.get(next_node, float("inf")):
                continue
            distances[next_node] = candidate
            parents[next_node] = (current_node, segment)
            sequence += 1
            heapq.heappush(pending, (candidate, sequence, next_node))
    return parents, order, distances


def reconstruct_segment_path(source_node, target_node, parents):
    """Returns whole pipe segments along the shortest path, never connector subpaths."""
    if target_node not in parents:
        return None
    path = []
    current = target_node
    visited = set()
    while current != source_node:
        if current in visited:
            return None
        visited.add(current)
        parent = parents.get(current)
        if parent is None:
            return None
        previous_node, segment = parent
        if segment is None:
            return None
        path.append(segment)
        current = previous_node
    path.reverse()
    return path


def build_element_traversal_tree(system_elements, source_element, source_connector):
    """Walk the complete connected MEP element graph once from the source."""
    element_by_id = {item.Id.IntegerValue: item for item in system_elements}
    adjacency = {element_id: set() for element_id in element_by_id}
    for element_id, element in element_by_id.items():
        for connector in get_piping_connectors(element):
            try:
                references = connector.AllRefs
            except Exception:
                continue
            for reference in references:
                try:
                    neighbor_id = reference.Owner.Id.IntegerValue
                except Exception:
                    continue
                if neighbor_id == element_id or neighbor_id not in element_by_id:
                    continue
                adjacency[element_id].add(neighbor_id)
                adjacency[neighbor_id].add(element_id)

    source_id = source_element.Id.IntegerValue
    # Geometry is only a recovery when the selected adapter has no connector links.
    if not adjacency.get(source_id):
        nearby_pipe_connector = find_nearest_pipe_connector(source_connector, system_elements)
        if nearby_pipe_connector is not None:
            pipe_id = nearby_pipe_connector.Owner.Id.IntegerValue
            adjacency[source_id].add(pipe_id)
            adjacency[pipe_id].add(source_id)

    parents = {source_id: None}
    queue = [source_id]
    while queue:
        current_id = queue.pop(0)
        for neighbor_id in sorted(adjacency.get(current_id, [])):
            if neighbor_id in parents:
                continue
            parents[neighbor_id] = current_id
            queue.append(neighbor_id)
    return parents, element_by_id


def reconstruct_element_pipe_path(source_element_id, target_element_id,
                                   parents, element_by_id, segment_by_id):
    """Rebuild a source-to-outlet route, charging each traversed pipe exactly once."""
    if target_element_id not in parents:
        return None
    path = []
    current_id = target_element_id
    visited = set()
    while current_id != source_element_id:
        if current_id in visited:
            return None
        visited.add(current_id)
        element = element_by_id.get(current_id)
        if element is None:
            return None
        if is_pipe_element(element):
            segment = segment_by_id.get(str(current_id))
            if segment is None:
                return None
            path.append(segment)
        current_id = parents.get(current_id)
        if current_id is None:
            return None
    path.reverse()
    return path


def connector_angle_degrees(first, second):
    try:
        first_direction = first.CoordinateSystem.BasisZ.Normalize()
        second_direction = second.CoordinateSystem.BasisZ.Normalize()
        dot = abs(first_direction.DotProduct(second_direction))
        return math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    except Exception:
        return None


def get_fitting_label(element):
    parts = [get_element_name(element)]
    try:
        parts.append(element.Symbol.FamilyName or "")
        parts.append(get_element_name(element.Symbol))
    except Exception:
        pass
    try:
        parts.append(str(element.MEPModel.PartType))
    except Exception:
        pass
    return " ".join(parts)


def build_connector_route_graph(system_elements, source_connector=None):
    """Grafo sem colapsar conectores: tubos carregam comprimento; conexoes/fittings, zero."""
    connectors = {}
    for element in system_elements:
        for connector in get_piping_connectors(element):
            connectors[connector_key(connector)] = connector
    graph = {}

    def add_edge(first_key, second_key, edge):
        if first_key == second_key:
            return
        graph.setdefault(first_key, []).append(dict(edge, to=second_key))
        graph.setdefault(second_key, []).append(dict(edge, to=first_key))

    # Ligacoes externas entre portas (fim de tubo ate fitting, por exemplo).
    seen_connections = set()
    for key, connector in connectors.items():
        try:
            refs = connector.AllRefs
        except Exception:
            continue
        for reference in refs:
            try:
                other_key = connector_key(reference)
            except Exception:
                continue
            if other_key not in connectors:
                continue
            pair = tuple(sorted((key, other_key)))
            if pair in seen_connections:
                continue
            seen_connections.add(pair)
            add_edge(key, other_key, {"kind": "connection", "length_m": 0.0})

    for element in system_elements:
        ports = get_piping_connectors(element)
        if is_pipe_element(element):
            if len(ports) == 2:
                try:
                    length_m = element.Location.Curve.Length * FT_TO_M
                except Exception:
                    length_m = ports[0].Origin.DistanceTo(ports[1].Origin) * FT_TO_M
                add_edge(connector_key(ports[0]), connector_key(ports[1]), {
                    "kind": "pipe", "length_m": length_m,
                    "element_id": element.Id.IntegerValue,
                })
        elif len(ports) > 1 and isinstance(element, DB.FamilyInstance):
            for index, first in enumerate(ports):
                for second in ports[index + 1:]:
                    add_edge(connector_key(first), connector_key(second), {
                        "kind": "internal" if is_outlet(element) else "fitting", "length_m": 0.0,
                        "element_id": element.Id.IntegerValue,
                        "label": get_fitting_label(element),
                        "angle": connector_angle_degrees(first, second),
                    })
    if source_connector is not None:
        source_key = connector_key(source_connector)
        reachable = set([source_key])
        pending = [source_key]
        reaches_pipe = False
        while pending and not reaches_pipe:
            current_key = pending.pop(0)
            for edge in graph.get(current_key, []):
                if edge.get("kind") == "pipe":
                    reaches_pipe = True
                    break
                next_key = edge["to"]
                if next_key not in reachable:
                    reachable.add(next_key)
                    pending.append(next_key)
    else:
        reaches_pipe = True
    if source_connector is not None and not reaches_pipe:
        nearby_pipe_connector = find_nearest_pipe_connector(source_connector, system_elements)
        if nearby_pipe_connector is not None:
            add_edge(connector_key(source_connector), connector_key(nearby_pipe_connector),
                     {"kind": "connection", "length_m": 0.0})
    return graph


def build_connector_path_tree(graph, source_connector):
    source_key = connector_key(source_connector)
    distances = {source_key: 0.0}
    parents = {source_key: None}
    sequence = 0
    pending = [(0.0, sequence, source_key)]
    while pending:
        distance, _, key = heapq.heappop(pending)
        if distance > distances.get(key, float("inf")) + 1e-9:
            continue
        for edge in graph.get(key, []):
            next_key = edge["to"]
            candidate = distance + edge["length_m"]
            if candidate + 1e-9 >= distances.get(next_key, float("inf")):
                continue
            sequence += 1
            distances[next_key] = candidate
            parents[next_key] = (key, edge)
            heapq.heappush(pending, (candidate, sequence, next_key))
    return source_key, distances, parents


def reconstruct_connector_route(path_tree, outlet_connector):
    source_key, distances, parents = path_tree
    target_key = connector_key(outlet_connector)
    if target_key not in distances:
        return None
    route = []
    key = target_key
    while key != source_key:
        parent = parents.get(key)
        if parent is None:
            return None
        previous_key, edge = parent
        route.append(edge)
        key = previous_key
    route.reverse()
    return summarize_connector_route(distances[target_key], route)


def summarize_connector_route(length_m, route):
    pipe_ids = []
    count_90 = 0
    count_45 = 0
    count_t = 0
    counted_fittings = set()
    for edge in route:
        if edge["kind"] == "pipe":
            pipe_ids.append(str(edge["element_id"]))
        elif edge["kind"] == "fitting" and edge["element_id"] not in counted_fittings:
            angle = edge.get("angle")
            label = normalize_text(edge.get("label", ""))
            is_tee = any(token in label for token in ["tee", "te ", "te-", "te ", "derivacao", "tubulacao t"])
            if is_tee:
                # O T so e contado quando o caminho usa o ramal, nao no trecho reto.
                if angle is not None and 75.0 <= angle <= 105.0:
                    count_t += 1
                    counted_fittings.add(edge["element_id"])
            elif "90" in label:
                count_90 += 1
                counted_fittings.add(edge["element_id"])
            elif "45" in label:
                count_45 += 1
                counted_fittings.add(edge["element_id"])
            elif angle is not None and 75.0 <= angle <= 105.0:
                count_90 += 1
                counted_fittings.add(edge["element_id"])
            elif angle is not None and 20.0 <= angle <= 70.0:
                count_45 += 1
                counted_fittings.add(edge["element_id"])
    return {"length_m": length_m, "pipe_ids": pipe_ids,
            "count_90": count_90, "count_45": count_45, "count_t": count_t}


def get_pipe_type_label(source_element):
    label = normalize_text(get_piping_system_label(source_element))
    if "quente" in label or "hot water" in label:
        return "Água quente"
    if "fria" in label or "cold water" in label:
        return "Água fria"
    return "Não identificado"


def get_route_pipe_type_label(source_element, pipe_elements, outlet_element=None):
    """Classifica pela tubulacao do percurso, pois o adaptador pode nao ter sistema."""
    labels = []
    for element in pipe_elements or []:
        try:
            labels.append(get_element_name(element.MEPSystem))
        except Exception:
            pass
        try:
            labels.append(get_element_name(element.PipeType))
        except Exception:
            pass
        try:
            labels.append(get_material(element))
        except Exception:
            pass
        try:
            system_name = element.get_Parameter(DB.BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
            if system_name and system_name.HasValue:
                labels.append(system_name.AsString())
        except Exception:
            pass
    labels.append(get_piping_system_label(source_element))
    for element in [source_element, outlet_element]:
        if element is None:
            continue
        labels.append(get_element_name(element))
        try:
            labels.append(element.Symbol.FamilyName)
            labels.append(get_element_name(element.Symbol))
        except Exception:
            pass
    for value in labels:
        label = normalize_text(value)
        if any(token in label for token in ["agua quente", "aguaquente", "aquecida", "hot water", "hot-water"]):
            return "Água quente"
        if any(token in label for token in ["agua fria", "aguafria", "cold water", "cold-water"]):
            return "Água fria"
    return get_pipe_type_label(source_element)


def get_outlet_label(element):
    try:
        mark = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        value = (mark.AsString() or "").strip() if mark and mark.HasValue else ""
        if value and normalize_text(value) not in ("var.", "var", ""):
            return value
    except Exception:
        pass
    return "Ponto {}".format(element.Id.IntegerValue)


def pipe_resistance(segment):
    length_m = segment["length_m"] + segment["equivalent_length_m"]
    diameter_m = segment["diameter_mm"] / 1000.0
    if length_m <= 0 or diameter_m <= 0 or segment["c_factor"] <= 0:
        return float("inf")
    return 10.67 * length_m / (
        math.pow(segment["c_factor"], 1.852) * math.pow(diameter_m, 4.87)
    )


def flow_from_head_difference(head_difference_m, resistance):
    if resistance == float("inf") or resistance <= 0 or abs(head_difference_m) < 1e-12:
        return 0.0
    magnitude = math.pow(abs(head_difference_m) / resistance, 1.0 / 1.852)
    return magnitude if head_difference_m > 0 else -magnitude


def pipe_head_loss_m(flow_m3s, segment):
    magnitude = abs(flow_m3s)
    loss = pipe_resistance(segment) * math.pow(magnitude, 1.852)
    fitting_k = max(0.0, segment.get("fitting_k", 0.0))
    diameter_m = segment["diameter_mm"] / 1000.0
    if fitting_k > 0.0 and diameter_m > 0.0:
        area_m2 = math.pi * diameter_m * diameter_m / 4.0
        velocity = magnitude / area_m2
        loss += fitting_k * velocity * velocity / (2.0 * 9.80665)
    return loss


def flow_for_pipe_head_difference(head_difference_m, segment):
    resistance = pipe_resistance(segment)
    if resistance == float("inf") or resistance <= 0.0:
        return 0.0
    if segment.get("fitting_k", 0.0) <= 0.0:
        return flow_from_head_difference(head_difference_m, resistance)
    head = abs(head_difference_m)
    if head < 1e-12:
        return 0.0
    low = 0.0
    high = math.pow(head / resistance, 1.0 / 1.852)
    for _ in range(32):
        middle = (low + high) / 2.0
        if pipe_head_loss_m(middle, segment) > head:
            high = middle
        else:
            low = middle
    flow = (low + high) / 2.0
    return flow if head_difference_m > 0.0 else -flow


def solve_hydraulic_network(segments, adjacency, source_node, source_head_m, outlets):
    """Resolve vazões por continuidade e Hazen-Williams com saídas livres (K=1)."""
    nodes = set(adjacency.keys())
    for edges in adjacency.values():
        for segment in edges:
            nodes.add(segment["start"])
            nodes.add(segment["end"])
    heads = dict((node, source_head_m) for node in nodes)
    outlet_by_node = {}
    for outlet in outlets:
        outlet_by_node.setdefault(outlet["node"], []).append(outlet)

    outlet_areas = {}
    for outlet in outlets:
        diameter_m = outlet["diameter_mm"] / 1000.0
        outlet_areas[id(outlet)] = math.pi * diameter_m * diameter_m / 4.0

    unknown_nodes = sorted([node for node in nodes if node != source_node])
    converged = False

    def node_continuity(node, head_m):
        net_outflow = 0.0
        for segment in adjacency.get(node, []):
            if segment["start"] == node:
                neighbor = segment["end"]
            else:
                neighbor = segment["start"]
            net_outflow += flow_for_pipe_head_difference(
                head_m - heads[neighbor], segment
            )
        for outlet in outlet_by_node.get(node, []):
            outlet_head = outlet["connector"].Origin.Z * FT_TO_M
            pressure_head = max(0.0, head_m - outlet_head)
            net_outflow += outlet_areas[id(outlet)] * math.sqrt(
                2.0 * 9.80665 * pressure_head
            )
        return net_outflow

    for iteration in range(800):
        largest_change = 0.0
        for node in unknown_nodes:
            incident = adjacency.get(node, [])
            if not incident:
                continue
            neighbor_heads = []
            for segment in incident:
                neighbor = segment["end"] if segment["start"] == node else segment["start"]
                neighbor_heads.append(heads[neighbor])
            terminal_heads = [item["connector"].Origin.Z * FT_TO_M
                              for item in outlet_by_node.get(node, [])]
            reference_heads = neighbor_heads + terminal_heads + [source_head_m]
            span = max(100.0, max(abs(value) for value in reference_heads) + 10.0)
            lower = min(reference_heads) - span
            upper = max(reference_heads) + span

            for _ in range(60):
                middle = (lower + upper) / 2.0
                if node_continuity(node, middle) > 0.0:
                    upper = middle
                else:
                    lower = middle
            target_head = (lower + upper) / 2.0
            updated_head = 0.75 * target_head + 0.25 * heads[node]
            largest_change = max(largest_change, abs(updated_head - heads[node]))
            heads[node] = updated_head
        largest_residual = max(
            [abs(node_continuity(node, heads[node])) for node in unknown_nodes] or [0.0]
        )
        if largest_change < 1e-7 and largest_residual < 1e-9:
            converged = True
            break

    for segment in segments:
        signed_flow_m3s = flow_for_pipe_head_difference(
            heads[segment["start"]] - heads[segment["end"]], segment
        )
        segment["signed_flow_lps"] = signed_flow_m3s * 1000.0
        segment["flow_lps"] = abs(signed_flow_m3s) * 1000.0

    for outlet in outlets:
        pressure_head = heads[outlet["node"]] - outlet["connector"].Origin.Z * FT_TO_M
        outlet["pressure_kpa"] = pressure_head * GRAVITY_KPA_PER_M
        outlet["flow_lps"] = outlet_areas[id(outlet)] * math.sqrt(
            2.0 * 9.80665 * max(0.0, pressure_head)
        ) * 1000.0
        outlet["flow_is_estimated"] = True

    results = []
    for segment in segments:
        flow = segment["signed_flow_lps"]
        diameter_m = segment["diameter_mm"] / 1000.0
        area = math.pi * diameter_m * diameter_m / 4.0 if diameter_m > 0 else 0.0
        velocity = abs(flow / 1000.0) / area if area > 0 else 0.0
        distributed_m = calculate_loss_m(
            abs(flow), segment["diameter_mm"], segment["length_m"], segment["c_factor"]
        )
        localized_m = calculate_loss_m(
            abs(flow), segment["diameter_mm"], segment["equivalent_length_m"], segment["c_factor"]
        )
        direction = 1.0 if flow >= 0 else -1.0
        elevation_m = direction * (segment["end_elevation"] - segment["start_elevation"])
        results.append({
            "Trecho": segment["name"],
            "Diametro (mm)": round(segment["diameter_mm"], 1),
            "Comprimento (m)": round(segment["length_m"], 2),
            "Vazao (L/s)": round(abs(flow), 3),
            "Velocidade (m/s)": round(velocity, 2),
            "Perda dist. (kPa)": round(distributed_m * GRAVITY_KPA_PER_M, 2),
            "Perda loc. (kPa)": round(localized_m * GRAVITY_KPA_PER_M, 2),
            "Perda elev. (kPa)": round(elevation_m * GRAVITY_KPA_PER_M, 2),
            "Pressao final (kPa)": round(
                (heads[segment["end"]] - segment["end_elevation"]) * GRAVITY_KPA_PER_M, 2
            ),
            "Vazao estimada": "Sim",
        })
    return heads, results, converged

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
    output = script.get_output()
    output.set_title("Calculadora Hidraulica")
    try:
        run_analysis(revit.doc, ReportOutput(output))
    except Exception:
        output.print_md("## Erro na analise hidraulica")
        output.print_md("```\n{}\n```".format(traceback.format_exc()))
        forms.alert("Ocorreu um erro. Veja o detalhe no final do painel de resultados do PyRevit.",
                    title="Calculadora Hidraulica")


def run_analysis(document, output):
    source_element, source_connector, _ = choose_source(document)
    if source_connector is None:
        output.print_md("**Selecao cancelada.** Nenhum ponto de origem foi definido.")
        return
    output.print_md("Adaptador de origem selecionado: elemento Id {}.".format(source_element.Id.IntegerValue))

    network_elements = collect_reachable_elements(source_element, source_connector)
    output.print_md("Elementos conectados encontrados na rede: {}.".format(len(network_elements)))
    segments, adjacency, source_node, outlets, skipped_pipes = build_network(
        document, network_elements, source_connector
    )
    pipe_candidates = [item for item in network_elements if is_pipe_element(item)]
    if skipped_pipes and segments:
        skipped_summary = ", ".join("{} ({} conectores)".format(item_id, count)
                                    for item_id, count in skipped_pipes[:10])
        forms.alert("Tubos encontrados, mas ignorados por nao terem exatamente dois conectores de tubulacao. Total: {}. IDs/conectores: {}".format(
            len(skipped_pipes), skipped_summary), title="Revisao da rede hidraulica", warn_icon=True)
    output.print_md("Tubos encontrados: {}. Saídas azuis reconhecidas: {}.".format(len(segments), len(outlets)))
    if not segments or source_node is None:
        forms.alert("A selecao foi aceita, mas nao foi possivel montar a rede. Elementos encontrados: {}. Curvas de tubo detectadas: {}. Trechos validos: {}. Tubos descartados: {}. Origem identificada: {}.".format(
            len(network_elements), len(pipe_candidates), len(segments), len(skipped_pipes),
            "sim" if source_node else "nao"),
            title="Calculadora Hidraulica")
        return

    path_parents, traversal, path_distances = build_shortest_path_tree(adjacency, source_node)
    segment_paths = {
        id(item): reconstruct_segment_path(source_node, item["node"], path_parents)
        for item in outlets
    }
    segment_by_id = dict((str(item["id"]), item) for item in segments)
    element_parents, element_by_id = build_element_traversal_tree(
        network_elements, source_element, source_connector
    )
    source_element_id = source_element.Id.IntegerValue
    element_paths = {
        id(item): reconstruct_element_pipe_path(
            source_element_id, item["element"].Id.IntegerValue,
            element_parents, element_by_id, segment_by_id
        )
        for item in outlets
    }
    traversed_pipe_ids = set(
        str(element_id) for element_id in element_parents
        if element_id in element_by_id and is_pipe_element(element_by_id[element_id])
    )
    route_graph = build_connector_route_graph(network_elements, source_connector)
    connector_path_tree = build_connector_path_tree(route_graph, source_connector)
    outlet_routes = {}
    for item in outlets:
        outlet_routes[id(item)] = reconstruct_connector_route(
            connector_path_tree, item["terminal_connector"]
        )
    unreachable_outlets = [item for item in outlets
                           if element_paths[id(item)] is None and
                           segment_paths[id(item)] is None and outlet_routes[id(item)] is None]
    cycle_count = max(0, len(segments) - max(0, len(traversal) - 1))
    if cycle_count:
        output.print_md("**Aviso:** a vazão considera todos os caminhos da rede; a metragem exibida é o menor percurso de tubos até cada saída, entre {} caminhos possíveis em anéis.".format(cycle_count))

    source_elevation_m = source_connector.Origin.Z * FT_TO_M
    source_head_m = source_elevation_m + WATER_COLUMN_ABOVE_SOURCE_M
    source_pressure_kpa = WATER_COLUMN_ABOVE_SOURCE_M * GRAVITY_KPA_PER_M
    node_heads, segment_results, solver_converged = solve_hydraulic_network(
        segments, adjacency, source_node, source_head_m, outlets
    )

    named, skipped = name_outlets(outlets)
    point_results = []
    route_results = []
    no_flow_ids = []
    routed_pipe_ids = set()
    for outlet in outlets:
        outlet_elevation_m = outlet["connector"].Origin.Z * FT_TO_M
        route = outlet_routes[id(outlet)]
        segment_path = segment_paths[id(outlet)] or []
        segment_path_ids = [str(segment["id"]) for segment in segment_path]
        element_path = element_paths[id(outlet)] or []
        element_path_ids = [str(segment["id"]) for segment in element_path]
        route_pipe_elements = [element_by_id[int(pipe_id)] for pipe_id in element_path_ids
                               if int(pipe_id) in element_by_id]
        connector_path_ids = route["pipe_ids"] if route else []
        connector_path_segments = [segment_by_id[pipe_id] for pipe_id in connector_path_ids
                                   if pipe_id in segment_by_id]
        connector_path_complete = (route is not None and
                                   len(connector_path_segments) == len(connector_path_ids))
        if element_path:
            path_ids = element_path_ids
            path_segments = element_path
        else:
            path_ids = connector_path_ids if connector_path_complete else segment_path_ids
            path_segments = connector_path_segments if connector_path_complete else segment_path
        routed_pipe_ids.update(path_ids)
        route_length = sum(segment["length_m"] for segment in path_segments)
        segment_route_length = sum(segment["length_m"] for segment in segment_path)
        path_lengths = " > ".join("{:.2f}".format(segment["length_m"]) for segment in path_segments)
        elevation_delta = outlet_elevation_m - source_elevation_m
        no_route = not element_path and not connector_path_complete and not segment_path
        verification_paths = []
        if connector_path_complete:
            verification_paths.append(connector_path_ids)
        if segment_path:
            verification_paths.append(segment_path_ids)
        graphs_diverge = any(ids != path_ids for ids in verification_paths)
        vertical_mismatch = route_length + 0.05 < abs(elevation_delta)
        if no_route:
            route_status = "SEM CAMINHO"
        elif graphs_diverge or (element_path and not connector_path_complete) or (element_path and not segment_path):
            route_status = "GRAFOS DIVERGENTES"
        elif not connector_path_complete:
            route_status = "FALLBACK SEGMENTOS"
        elif vertical_mismatch:
            route_status = "CONFERIR COTA"
        else:
            route_status = "OK"
        flow_lps = outlet.get("flow_lps", 0.0)
        pressure_kpa = outlet.get("pressure_kpa", 0.0)
        if not solver_converged:
            hydraulic_status = "SOLVER NAO CONVERGIU"
        elif pressure_kpa < 0.0:
            hydraulic_status = "PRESSAO NEGATIVA"
        else:
            hydraulic_status = "OK" if flow_lps > 1e-6 else "SEM VAZAO"
        if hydraulic_status == "SEM VAZAO" and not no_route:
            no_flow_ids.append(outlet["element"].Id)
        if vertical_mismatch:
            output.print_md("**Inconsistência no percurso de {}:** o caminho conectado soma {:.2f} m de tubos, mas a diferença vertical é {:.2f} m. Confira se os tubos verticais e conexões estão realmente conectados no modelo.".format(outlet["name"], route_length, abs(elevation_delta)))

        point_results.append({
            "Ponto": outlet["name"],
            "Quantidade de tubos": len(path_ids),
            "Comprimentos por trecho (m)": path_lengths,
            "Metragem de verificacao (m)": round(segment_route_length, 2),
            "Elevacao relativa (m)": round(elevation_delta, 2),
            "Metragem de tubos (m)": round(route_length, 2),
            "Vazao calculada (L/s)": round(flow_lps, 3),
            "Pressao antes da saida (kPa)": round(pressure_kpa, 2),
            "Status percurso": route_status,
            "Status hidraulico": hydraulic_status,
        })
        route_results.append({
            "Ponto": outlet["name"],
            "Tipo de tubulacao": get_route_pipe_type_label(
                source_element, route_pipe_elements, outlet["element"]
            ),
            # As pecas sao contadas pelo grafo de conectores; a rota de tubos
            # principal pode ter outra sequencia sem invalidar essa contagem.
            "90 graus": route["count_90"] if route else "Revisar",
            "45 graus": route["count_45"] if route else "Revisar",
            "T": route["count_t"] if route else "Revisar",
        })

    point_columns = [
        "Ponto", "Quantidade de tubos", "Comprimentos por trecho (m)",
        "Metragem de verificacao (m)", "Elevacao relativa (m)", "Metragem de tubos (m)",
        "Vazao calculada (L/s)", "Pressao antes da saida (kPa)", "Status percurso", "Status hidraulico",
    ]
    route_columns = ["Ponto", "Tipo de tubulacao", "90 graus", "45 graus", "T"]
    network_pipe_ids = set(segment_by_id.keys())
    covered_pipe_count = len(routed_pipe_ids.intersection(network_pipe_ids))
    output.print_html(render_results_html(
        "{} | tubos reconhecidos: {}/{} | tubos conectados: {}/{} | tubos nos percursos: {}/{}".format(
            get_piping_system_label(source_element), len(segments), len(pipe_candidates),
            len(traversed_pipe_ids.intersection(network_pipe_ids)), len(segments),
            covered_pipe_count, len(segments)),
        point_results, route_results,
        point_columns, route_columns
    ))

    output.print_md("# {}".format(get_piping_system_label(source_element)))
    output.print_md("Carga na origem: **{:.2f} mca** (**{:.2f} kPa**). Saídas modeladas como descarga livre à atmosfera, com K=1 e diâmetro do tubo conectado.".format(
        WATER_COLUMN_ABOVE_SOURCE_M, source_pressure_kpa
    ))
    if cycle_count:
        output.print_md("A rede tem caminhos em anel; a metragem exibida corresponde ao menor caminho conectado de tubos para cada ponto.")
    output.print_md("## Pontos de saída")
    if point_results:
        point_columns = [
            "Ponto", "Quantidade de tubos", "Elevacao relativa (m)", "Metragem de tubos (m)",
            "Vazao calculada (L/s)", "Pressao antes da saida (kPa)",
            "Status percurso", "Status hidraulico",
        ]
        output.print_table(to_table_rows(point_results, point_columns), columns=point_columns)
        output.print_md("## Tubulacao e curvas por ponto")
        route_columns = ["Ponto", "Tipo de tubulacao", "90 graus", "45 graus", "T"]
        output.print_table(to_table_rows(route_results, route_columns), columns=route_columns)
    else:
        near_misses = find_near_miss_families(network_elements)
        output.print_md("**Nenhuma saída azul reconhecida na rede conectada.**")
        for label in near_misses:
            output.print_md("- {}".format(label))
    if unreachable_outlets:
        output.print_md("**Atencao:** {} saída(s) não têm percurso conectado ao ponto inicial.".format(len(unreachable_outlets)))
    output.print_md("Pontos nomeados em `Marca`: **{}** | Pontos preservados por já terem Marca: **{}**".format(named, skipped))

    if no_flow_ids:
        revit.uidoc.Selection.SetElementIds(List[DB.ElementId](no_flow_ids))
        forms.alert("{} saída(s) sem vazão calculada. Foram selecionadas para conferência.".format(len(no_flow_ids)),
                    title="Resultado hidráulico", warn_icon=True)
    else:
        forms.alert("Análise concluída com o modelo de descarga livre e carga de origem de 1 mca.",
                    title="Resultado hidráulico", warn_icon=False)

try:
    main()
except Exception as error:
    forms.alert("Erro inesperado ao iniciar a Calculadora Hidraulica:\n{}\n\n{}".format(error, traceback.format_exc()),
                title="Calculadora Hidraulica")
