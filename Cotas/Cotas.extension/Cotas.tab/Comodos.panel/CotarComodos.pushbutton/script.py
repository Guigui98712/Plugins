# -*- coding: utf-8 -*-

import traceback

from pyrevit import DB, forms, revit
from Autodesk.Revit.DB import FailureProcessingResult, FailureSeverity, IFailuresPreprocessor, Transaction


doc = revit.doc
view = doc.ActiveView

STYLE_NAME = "SL_PRETO_1,5"
OFFSET_MM = 100.0
TOLERANCE = 1e-6
DOOR_AVOID_BAND_MM = 350.0
MIN_INNER_OFFSET_MM = 80.0
SMALL_ROOM_THRESHOLD_MM = 2200.0


class RoomCandidate(object):
    def __init__(self, room, loop_segments, axis_u, axis_v):
        self.room = room
        self.loop_segments = loop_segments
        self.axis_u = axis_u
        self.axis_v = axis_v


class RoomDimensionFailurePreprocessor(IFailuresPreprocessor):
    def PreprocessFailures(self, failures_accessor):
        failure_messages = failures_accessor.GetFailureMessages()
        has_error = False

        for failure in failure_messages:
            severity = failure.GetSeverity()
            if severity == FailureSeverity.Warning:
                failures_accessor.DeleteWarning(failure)
            else:
                has_error = True

        if has_error:
            return FailureProcessingResult.ProceedWithRollBack

        return FailureProcessingResult.Continue


def is_plan_view(active_view):
    return isinstance(active_view, DB.ViewPlan) and not active_view.IsTemplate


def get_dimension_type_by_name(document, type_name):
    collector = DB.FilteredElementCollector(document).OfClass(DB.DimensionType)
    for dim_type in collector:
        current_name = get_element_name(dim_type)
        if current_name == type_name:
            return dim_type
    return None


def get_rooms_in_view(document, active_view):
    collector = DB.FilteredElementCollector(document, active_view.Id)
    collector = collector.OfCategory(DB.BuiltInCategory.OST_Rooms)
    collector = collector.WhereElementIsNotElementType()
    return [room for room in collector if room.Area > 0]


def get_room_boundary_segments(room):
    options = DB.SpatialElementBoundaryOptions()
    boundaries = room.GetBoundarySegments(options)
    if not boundaries:
        return None
    if len(boundaries) != 1:
        return None
    loop = boundaries[0]
    segments = []
    line_dirs = []
    for boundary_segment in loop:
        curve = boundary_segment.GetCurve()
        if not isinstance(curve, DB.Line):
            return None
        element = doc.GetElement(boundary_segment.ElementId)
        if not isinstance(element, DB.Wall):
            return None
        segments.append((boundary_segment, curve, element))

        direction = get_line_direction(curve)
        direction_xy = DB.XYZ(direction.X, direction.Y, 0.0)
        if direction_xy.GetLength() > TOLERANCE:
            line_dirs.append(direction_xy.Normalize())

    if len(segments) < 4:
        return None

    axis_u, axis_v = resolve_room_axes(line_dirs)
    if axis_u is None or axis_v is None:
        return None

    return RoomCandidate(room, segments, axis_u, axis_v)


def get_line_direction(curve):
    direction = (curve.GetEndPoint(1) - curve.GetEndPoint(0)).Normalize()
    return direction


def are_parallel(vec_a, vec_b, tolerance=0.95):
    try:
        dot = abs(vec_a.Normalize().DotProduct(vec_b.Normalize()))
        return dot >= tolerance
    except Exception:
        return False


def resolve_room_axes(line_dirs):
    if not line_dirs:
        return None, None

    axis_u = line_dirs[0]
    axis_v = None
    for line_dir in line_dirs[1:]:
        dot = abs(axis_u.DotProduct(line_dir))
        if dot < 0.2:
            axis_v = line_dir
            break

    if axis_v is None:
        return None, None

    axis_u = DB.XYZ(axis_u.X, axis_u.Y, 0.0).Normalize()
    axis_v = DB.XYZ(axis_v.X, axis_v.Y, 0.0).Normalize()

    for line_dir in line_dirs:
        if not are_parallel(line_dir, axis_u) and not are_parallel(line_dir, axis_v):
            return None, None

    return axis_u, axis_v


def is_orthogonal_rectangle(candidate):
    for _, curve, _ in candidate.loop_segments:
        direction = get_line_direction(curve)
        direction_xy = DB.XYZ(direction.X, direction.Y, 0.0)
        if direction_xy.GetLength() <= TOLERANCE:
            return False
        direction_xy = direction_xy.Normalize()
        if not are_parallel(direction_xy, candidate.axis_u) and not are_parallel(direction_xy, candidate.axis_v):
            return False

    return True


def get_room_bbox(room, active_view):
    bbox = room.get_BoundingBox(active_view)
    if bbox is None:
        bbox = room.get_BoundingBox(None)
    return bbox


def get_room_point(room, bbox):
    location = room.Location
    if isinstance(location, DB.LocationPoint):
        return location.Point

    if bbox is not None:
        return DB.XYZ(
            (bbox.Min.X + bbox.Max.X) * 0.5,
            (bbox.Min.Y + bbox.Max.Y) * 0.5,
            (bbox.Min.Z + bbox.Max.Z) * 0.5,
        )

    return None


def project_point_to_axis(point, origin, axis):
    vector = point - origin
    return vector.DotProduct(axis)


def get_candidate_extents(candidate, room_point):
    min_u = None
    max_u = None
    min_v = None
    max_v = None

    for _, curve, _ in candidate.loop_segments:
        for point in [curve.GetEndPoint(0), curve.GetEndPoint(1)]:
            pu = project_point_to_axis(point, room_point, candidate.axis_u)
            pv = project_point_to_axis(point, room_point, candidate.axis_v)

            min_u = pu if min_u is None else min(min_u, pu)
            max_u = pu if max_u is None else max(max_u, pu)
            min_v = pv if min_v is None else min(min_v, pv)
            max_v = pv if max_v is None else max(max_v, pv)

    if None in (min_u, max_u, min_v, max_v):
        return None

    return (min_u, max_u, min_v, max_v)


def make_point_from_local(origin, axis_u, axis_v, pu, pv, z_value):
    x = origin.X + axis_u.X * pu + axis_v.X * pv
    y = origin.Y + axis_u.Y * pu + axis_v.Y * pv
    return DB.XYZ(x, y, z_value)


def to_internal_mm(mm_value):
    return DB.UnitUtils.ConvertToInternalUnits(mm_value, DB.UnitTypeId.Millimeters)


def clamp_inner_offset(span_length, desired_offset):
    min_offset = to_internal_mm(MIN_INNER_OFFSET_MM)
    max_offset = span_length * 0.35
    if max_offset <= min_offset:
        return max(min_offset * 0.5, span_length * 0.2)
    return min(desired_offset, max_offset)


def get_element_center_point(element, active_view):
    location = getattr(element, "Location", None)
    if isinstance(location, DB.LocationPoint):
        return location.Point

    bbox = element.get_BoundingBox(active_view)
    if bbox is None:
        bbox = element.get_BoundingBox(None)
    if bbox is None:
        return None

    return DB.XYZ(
        (bbox.Min.X + bbox.Max.X) * 0.5,
        (bbox.Min.Y + bbox.Max.Y) * 0.5,
        (bbox.Min.Z + bbox.Max.Z) * 0.5,
    )


def get_door_points_local(candidate, room_point, active_view):
    points = []
    wall_ids = set([seg[2].Id.IntegerValue for seg in candidate.loop_segments])
    if not wall_ids:
        return points

    doors = DB.FilteredElementCollector(doc, active_view.Id)
    doors = doors.OfCategory(DB.BuiltInCategory.OST_Doors).WhereElementIsNotElementType()

    for door in doors:
        host = getattr(door, "Host", None)
        if host is None:
            continue
        if host.Id.IntegerValue not in wall_ids:
            continue

        center = get_element_center_point(door, active_view)
        if center is None:
            continue

        pu = project_point_to_axis(center, room_point, candidate.axis_u)
        pv = project_point_to_axis(center, room_point, candidate.axis_v)
        points.append((pu, pv))

    return points


def side_score_by_doors(door_points_local, coord_value, is_horizontal):
    if not door_points_local:
        return 1e9

    band = to_internal_mm(DOOR_AVOID_BAND_MM)
    collisions = 0
    min_dist = None

    for pu, pv in door_points_local:
        distance = abs((pv if is_horizontal else pu) - coord_value)
        if distance < band:
            collisions += 1
        if min_dist is None or distance < min_dist:
            min_dist = distance

    if min_dist is None:
        min_dist = 0.0

    return (min_dist * 10.0) - (collisions * 1000.0)


def pick_horizontal_side(min_v, max_v, inset_v, door_points_local):
    top_v = max_v - inset_v
    bottom_v = min_v + inset_v

    top_score = side_score_by_doors(door_points_local, top_v, True)
    bottom_score = side_score_by_doors(door_points_local, bottom_v, True)

    if bottom_score > top_score:
        return "bottom", bottom_v
    return "top", top_v


def pick_vertical_side(min_u, max_u, inset_u, door_points_local):
    right_u = max_u - inset_u
    left_u = min_u + inset_u

    right_score = side_score_by_doors(door_points_local, right_u, False)
    left_score = side_score_by_doors(door_points_local, left_u, False)

    if left_score > right_score:
        return "left", left_u
    return "right", right_u


def opposite_horizontal(side_name):
    return "bottom" if side_name == "top" else "top"


def opposite_vertical(side_name):
    return "left" if side_name == "right" else "right"


def get_horizontal_coord(side_name, min_v, max_v, inset_v):
    return (max_v - inset_v) if side_name == "top" else (min_v + inset_v)


def get_vertical_coord(side_name, min_u, max_u, inset_u):
    return (max_u - inset_u) if side_name == "right" else (min_u + inset_u)


def set_dimension_text_center(dimension, line_start, line_end):
    try:
        midpoint = (line_start + line_end) * 0.5
        if hasattr(dimension, "TextPosition"):
            dimension.TextPosition = midpoint
    except Exception:
        pass


def get_face_midpoint(face):
    bb = face.GetBoundingBox()
    if bb is None:
        return None
    uv = DB.UV((bb.Min.U + bb.Max.U) * 0.5, (bb.Min.V + bb.Max.V) * 0.5)
    try:
        return face.Evaluate(uv)
    except Exception:
        return None


def normalize_xy(vector):
    if vector is None:
        return None
    vec = DB.XYZ(vector.X, vector.Y, 0.0)
    if vec.GetLength() <= TOLERANCE:
        return None
    return vec.Normalize()


def pick_best_planar_face_reference(wall, references, axis, room_point):
    best_ref = None
    best_score = None
    best_normal = None

    axis_xy = normalize_xy(axis)
    if axis_xy is None:
        return None, None

    for ref in references:
        try:
            face = wall.GetGeometryObjectFromReference(ref)
        except Exception:
            face = None
        if not isinstance(face, DB.PlanarFace):
            continue

        normal_xy = normalize_xy(face.FaceNormal)
        if normal_xy is None:
            continue

        alignment = abs(normal_xy.DotProduct(axis_xy))
        if alignment < 0.95:
            continue

        midpoint = get_face_midpoint(face)
        if midpoint is None or room_point is None:
            distance = 0.0
        else:
            distance = midpoint.DistanceTo(room_point)

        score = distance
        if best_score is None or score < best_score:
            best_score = score
            best_ref = ref
            best_normal = normal_xy

    return best_ref, best_normal


def get_reference_from_geometry_faces(wall, axis, room_point):
    options = DB.Options()
    options.ComputeReferences = True
    options.IncludeNonVisibleObjects = True

    best_ref = None
    best_score = None
    best_normal = None

    axis_xy = normalize_xy(axis)
    if axis_xy is None:
        return None, None

    try:
        geometry = wall.get_Geometry(options)
    except Exception:
        geometry = None

    if geometry is None:
        return None

    for geom_obj in geometry:
        solid = geom_obj if isinstance(geom_obj, DB.Solid) else None
        if solid is None or solid.Faces is None or solid.Faces.Size == 0:
            continue

        for face in solid.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            normal_xy = normalize_xy(face.FaceNormal)
            if normal_xy is None:
                continue

            alignment = abs(normal_xy.DotProduct(axis_xy))
            if alignment < 0.95:
                continue

            if face.Reference is None:
                continue

            midpoint = get_face_midpoint(face)
            if midpoint is None or room_point is None:
                distance = 0.0
            else:
                distance = midpoint.DistanceTo(room_point)

            score = distance
            if best_score is None or score < best_score:
                best_score = score
                best_ref = face.Reference
                best_normal = normal_xy

    return best_ref, best_normal


def get_wall_face_reference_and_normal(wall, axis, room_point):
    refs = []
    try:
        interior_refs = DB.HostObjectUtils.GetSideFaces(wall, DB.ShellLayerType.Interior)
        if interior_refs:
            refs.extend([r for r in interior_refs])
    except Exception:
        pass

    try:
        exterior_refs = DB.HostObjectUtils.GetSideFaces(wall, DB.ShellLayerType.Exterior)
        if exterior_refs:
            refs.extend([r for r in exterior_refs])
    except Exception:
        pass

    if refs:
        picked_ref, picked_normal = pick_best_planar_face_reference(wall, refs, axis, room_point)
        if picked_ref is not None:
            return picked_ref, picked_normal

    return get_reference_from_geometry_faces(wall, axis, room_point)


def get_wall_centerline_reference_and_direction(wall):
    try:
        location = wall.Location
        location_curve = getattr(location, "Curve", None)
        if not isinstance(location_curve, DB.Line):
            return None, None

        reference = location_curve.Reference
        if reference is None:
            return None, None

        direction = get_line_direction(location_curve)
        direction_xy = DB.XYZ(direction.X, direction.Y, 0.0)
        if direction_xy.GetLength() <= TOLERANCE:
            return None, None

        return reference, direction_xy.Normalize()
    except Exception:
        return None, None


def get_wall_reference_pair(wall_a, wall_b, axis, room_point):
    # Prefer face references for better geometric fidelity; fallback to centerline references.
    a_face_ref, a_face_normal = get_wall_face_reference_and_normal(wall_a, axis, room_point)
    b_face_ref, b_face_normal = get_wall_face_reference_and_normal(wall_b, axis, room_point)

    if a_face_ref is not None and b_face_ref is not None and a_face_normal is not None and b_face_normal is not None:
        if are_parallel(a_face_normal, b_face_normal, 0.995):
            return a_face_ref, b_face_ref, "face"

    a_line_ref, a_line_dir = get_wall_centerline_reference_and_direction(wall_a)
    b_line_ref, b_line_dir = get_wall_centerline_reference_and_direction(wall_b)
    if a_line_ref is not None and b_line_ref is not None and a_line_dir is not None and b_line_dir is not None:
        if are_parallel(a_line_dir, b_line_dir, 0.995):
            return a_line_ref, b_line_ref, "line"

    return None, None, None


def get_room_z(room, active_view):
    if room.Level is not None:
        return room.Level.Elevation
    if hasattr(active_view, "GenLevel") and active_view.GenLevel is not None:
        return active_view.GenLevel.Elevation
    return None


def make_reference_array(references):
    ref_array = DB.ReferenceArray()
    for ref in references:
        if ref is not None:
            ref_array.Append(ref)
    return ref_array


def choose_walls(candidate):
    left_side = None
    right_side = None
    bottom_side = None
    top_side = None

    room_center = get_room_point(candidate.room, None)
    if room_center is None:
        return None

    for _, curve, wall in candidate.loop_segments:
        start = curve.GetEndPoint(0)
        end = curve.GetEndPoint(1)
        midpoint = (start + end) * 0.5
        direction = get_line_direction(curve)
        direction_xy = DB.XYZ(direction.X, direction.Y, 0.0)
        if direction_xy.GetLength() <= TOLERANCE:
            continue
        direction_xy = direction_xy.Normalize()

        if are_parallel(direction_xy, candidate.axis_v):
            pos_u = project_point_to_axis(midpoint, room_center, candidate.axis_u)
            if left_side is None or pos_u < left_side[0]:
                left_side = (pos_u, wall)
            if right_side is None or pos_u > right_side[0]:
                right_side = (pos_u, wall)
        elif are_parallel(direction_xy, candidate.axis_u):
            pos_v = project_point_to_axis(midpoint, room_center, candidate.axis_v)
            if bottom_side is None or pos_v < bottom_side[0]:
                bottom_side = (pos_v, wall)
            if top_side is None or pos_v > top_side[0]:
                top_side = (pos_v, wall)

    if None in (left_side, right_side, bottom_side, top_side):
        return None

    return {
        "left": left_side[1],
        "right": right_side[1],
        "bottom": bottom_side[1],
        "top": top_side[1],
    }


def create_dimension(document, active_view, dim_type, line, references):
    ref_array = make_reference_array(references)
    if ref_array.Size < 2:
        return None

    dimension = document.Create.NewDimension(active_view, line, ref_array)
    if dimension is None:
        return None

    if dim_type is not None:
        try:
            dimension.ChangeTypeId(dim_type.Id)
        except Exception:
            pass

    return dimension


def room_label(room):
    number = safe_get_room_number(room)
    name = safe_get_room_name(room)
    return (number + " " + name).strip()


def append_skip(skipped_list, room, reason):
    skipped_list.append((room_label(room), reason))


def get_element_name(element):
    """Read Revit element name safely across API wrappers."""
    if element is None:
        return ""

    try:
        return DB.Element.Name.__get__(element)
    except Exception:
        pass

    try:
        return element.Name
    except Exception:
        return ""


def safe_get_room_name(room):
    try:
        value = room.Name
        if value:
            return value
    except Exception:
        pass

    try:
        param = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
        if param is not None:
            value = param.AsString()
            if value:
                return value
    except Exception:
        pass

    return "Sem nome"


def safe_get_room_number(room):
    try:
        value = room.Number
        if value:
            return value
    except Exception:
        pass

    try:
        param = room.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER)
        if param is not None:
            value = param.AsString()
            if value:
                return value
    except Exception:
        pass

    return ""


def main():
    if not is_plan_view(view):
        forms.alert("Execute este comando em uma vista de planta ativa.", title="Cotas de comodos")
        return

    dim_type = get_dimension_type_by_name(doc, STYLE_NAME)
    if dim_type is None:
        forms.alert(
            "Nao encontrei o estilo de cota '{}'. Verifique o nome no template.".format(STYLE_NAME),
            title="Cotas de comodos",
        )
        return

    rooms = get_rooms_in_view(doc, view)
    if not rooms:
        forms.alert("Nao encontrei rooms com area na vista ativa.", title="Cotas de comodos")
        return

    eligible_rooms = []
    skipped_rooms = []
    for room in rooms:
        candidate = get_room_boundary_segments(room)
        if candidate is None:
            append_skip(skipped_rooms, room, "contorno nao fechado, nao ortogonal ou sem paredes")
            continue
        if not is_orthogonal_rectangle(candidate):
            append_skip(skipped_rooms, room, "contorno fora do padrao retangular ortogonal")
            continue
        eligible_rooms.append(candidate)

    if not eligible_rooms:
        forms.alert(
            "Nenhum comodo se enquadrou no padrao atual.\n\nOs rooms precisam ser retangulares, fechados e com paredes como limite.",
            title="Cotas de comodos",
        )
        return

    offset = DB.UnitUtils.ConvertToInternalUnits(OFFSET_MM, DB.UnitTypeId.Millimeters)
    created_count = 0
    processed_count = 0

    for candidate in eligible_rooms:
        processed_count += 1
        room = candidate.room

        room_transaction = Transaction(doc, "Cotar comodo {}".format(room_label(room)))
        failure_options = room_transaction.GetFailureHandlingOptions()
        failure_options.SetFailuresPreprocessor(RoomDimensionFailurePreprocessor())
        failure_options.SetClearAfterRollback(True)
        room_transaction.SetFailureHandlingOptions(failure_options)

        room_transaction.Start()
        try:
            bbox = get_room_bbox(room, view)
            if bbox is None:
                append_skip(skipped_rooms, room, "sem bounding box na vista")
                room_transaction.RollBack()
                continue

            center_z = get_room_z(room, view)
            if center_z is None:
                append_skip(skipped_rooms, room, "nao foi possivel determinar o nivel da vista")
                room_transaction.RollBack()
                continue

            room_point = get_room_point(room, bbox)
            if room_point is None:
                append_skip(skipped_rooms, room, "nao foi possivel determinar o centro do comodo")
                room_transaction.RollBack()
                continue

            walls = choose_walls(candidate)
            if walls is None:
                append_skip(skipped_rooms, room, "nao foi possivel identificar as paredes principais")
                room_transaction.RollBack()
                continue

            if walls["left"].Id == walls["right"].Id or walls["bottom"].Id == walls["top"].Id:
                append_skip(skipped_rooms, room, "lados opostos apontaram para a mesma parede")
                room_transaction.RollBack()
                continue

            extents = get_candidate_extents(candidate, room_point)
            if extents is None:
                append_skip(skipped_rooms, room, "nao foi possivel calcular os limites do comodo")
                room_transaction.RollBack()
                continue

            min_u, max_u, min_v, max_v = extents
            width = max_u - min_u
            height = max_v - min_v

            inset_u = clamp_inner_offset(width, offset)
            inset_v = clamp_inner_offset(height, offset)

            door_points_local = get_door_points_local(candidate, room_point, view)

            horizontal_side, horizontal_pv = pick_horizontal_side(min_v, max_v, inset_v, door_points_local)
            vertical_side, vertical_pu = pick_vertical_side(min_u, max_u, inset_u, door_points_local)

            small_room_threshold = to_internal_mm(SMALL_ROOM_THRESHOLD_MM)
            if min(width, height) < small_room_threshold:
                # In small rooms, try opposite side for vertical dim to reduce corner crowding.
                vertical_side = opposite_vertical(vertical_side)
                vertical_pu = get_vertical_coord(vertical_side, min_u, max_u, inset_u)

            left_ref, right_ref, _ = get_wall_reference_pair(
                walls["left"], walls["right"], candidate.axis_u, room_point
            )
            bottom_ref, top_ref, _ = get_wall_reference_pair(
                walls["bottom"], walls["top"], candidate.axis_v, room_point
            )

            if None in (left_ref, right_ref, bottom_ref, top_ref):
                append_skip(skipped_rooms, room, "nao foi possivel obter pares de referencias paralelas")
                room_transaction.RollBack()
                continue

            horizontal_start = make_point_from_local(
                room_point, candidate.axis_u, candidate.axis_v, min_u + inset_u, horizontal_pv, center_z
            )
            horizontal_end = make_point_from_local(
                room_point, candidate.axis_u, candidate.axis_v, max_u - inset_u, horizontal_pv, center_z
            )
            horizontal_line = DB.Line.CreateBound(horizontal_start, horizontal_end)
            horizontal_dimension = create_dimension(doc, view, dim_type, horizontal_line, [left_ref, right_ref])

            if horizontal_dimension is None:
                horizontal_side = opposite_horizontal(horizontal_side)
                horizontal_pv = get_horizontal_coord(horizontal_side, min_v, max_v, inset_v)
                horizontal_start = make_point_from_local(
                    room_point, candidate.axis_u, candidate.axis_v, min_u + inset_u, horizontal_pv, center_z
                )
                horizontal_end = make_point_from_local(
                    room_point, candidate.axis_u, candidate.axis_v, max_u - inset_u, horizontal_pv, center_z
                )
                horizontal_line = DB.Line.CreateBound(horizontal_start, horizontal_end)
                horizontal_dimension = create_dimension(doc, view, dim_type, horizontal_line, [left_ref, right_ref])

            vertical_start = make_point_from_local(
                room_point, candidate.axis_u, candidate.axis_v, vertical_pu, min_v + inset_v, center_z
            )
            vertical_end = make_point_from_local(
                room_point, candidate.axis_u, candidate.axis_v, vertical_pu, max_v - inset_v, center_z
            )
            vertical_line = DB.Line.CreateBound(vertical_start, vertical_end)
            vertical_dimension = create_dimension(doc, view, dim_type, vertical_line, [bottom_ref, top_ref])

            if vertical_dimension is None:
                vertical_side = opposite_vertical(vertical_side)
                vertical_pu = get_vertical_coord(vertical_side, min_u, max_u, inset_u)
                vertical_start = make_point_from_local(
                    room_point, candidate.axis_u, candidate.axis_v, vertical_pu, min_v + inset_v, center_z
                )
                vertical_end = make_point_from_local(
                    room_point, candidate.axis_u, candidate.axis_v, vertical_pu, max_v - inset_v, center_z
                )
                vertical_line = DB.Line.CreateBound(vertical_start, vertical_end)
                vertical_dimension = create_dimension(doc, view, dim_type, vertical_line, [bottom_ref, top_ref])

            if horizontal_dimension is None or vertical_dimension is None:
                append_skip(skipped_rooms, room, "falha ao criar uma ou mais cotas")
                room_transaction.RollBack()
                continue

            set_dimension_text_center(horizontal_dimension, horizontal_start, horizontal_end)
            set_dimension_text_center(vertical_dimension, vertical_start, vertical_end)

            status = room_transaction.Commit()
            if status == DB.TransactionStatus.Committed:
                created_count += 2
            else:
                append_skip(skipped_rooms, room, "cotas revertidas por falha de geometria")
        except Exception as room_exc:
            try:
                room_transaction.RollBack()
            except Exception:
                pass
            append_skip(skipped_rooms, room, "erro: {}".format(str(room_exc)))

    summary_lines = [
        "Processados: {}".format(processed_count),
        "Cotas criadas: {}".format(created_count),
        "Rooms ignorados: {}".format(len(skipped_rooms)),
    ]

    if skipped_rooms:
        summary_lines.append("")
        summary_lines.append("Primeiros rooms ignorados:")
        for room_name, reason in skipped_rooms[:12]:
            summary_lines.append("- {} | {}".format(room_name, reason))

    forms.alert("\n".join(summary_lines), title="Cotas de comodos", warn_icon=False)


try:
    main()
except Exception as fatal_exc:
    forms.alert(
        "Erro inesperado na execucao:\n{}\n\nDetalhe tecnico:\n{}".format(str(fatal_exc), traceback.format_exc()),
        title="Cotas de comodos",
    )
