# -*- coding: utf-8 -*-

import traceback

from pyrevit import DB, forms, revit


doc = revit.doc
FT_TO_M = 0.3048


def get_geometry_options():
    opt = DB.Options()
    opt.IncludeNonVisibleObjects = True
    try:
        opt.DetailLevel = DB.ViewDetailLevel.Fine
    except Exception:
        pass
    return opt


def collect_ifc_link_instances(document):
    links = []
    for link in DB.FilteredElementCollector(document).OfClass(DB.RevitLinkInstance):
        try:
            link_doc = link.GetLinkDocument()
            title = ""
            if link_doc is not None:
                title = (link_doc.Title or "").lower()
            name = (link.Name or "").lower()
            if ".ifc" in title or ".ifc" in name or "ifc" in name:
                links.append(link)
        except Exception:
            continue
    return links


def build_sources(document):
    sources = []

    imports = list(DB.FilteredElementCollector(document).OfClass(DB.ImportInstance))
    if imports:
        sources.append(
            {
                "label": "Todos os ImportInstance ({})".format(len(imports)),
                "kind": "import_all",
                "elements": imports,
                "root_transform": DB.Transform.Identity,
            }
        )

    direct_shapes = list(DB.FilteredElementCollector(document).OfClass(DB.DirectShape))
    if direct_shapes:
        sources.append(
            {
                "label": "Todos os DirectShape ({})".format(len(direct_shapes)),
                "kind": "directshape_all",
                "elements": direct_shapes,
                "root_transform": DB.Transform.Identity,
            }
        )

    ifc_links = collect_ifc_link_instances(document)
    for link in ifc_links:
        link_doc = None
        try:
            link_doc = link.GetLinkDocument()
        except Exception:
            link_doc = None
        if link_doc is None:
            continue

        try:
            root_t = link.GetTotalTransform()
        except Exception:
            root_t = link.GetTransform()

        elems = list(DB.FilteredElementCollector(link_doc).WhereElementIsNotElementType())
        sources.append(
            {
                "label": "RevitLink IFC Id {} ({})".format(link.Id.IntegerValue, link.Name or "SemNome"),
                "kind": "ifc_link",
                "elements": elems,
                "root_transform": root_t,
            }
        )

    return sources


def choose_source(sources):
    if not sources:
        return None
    if len(sources) == 1:
        return sources[0]

    labels = [s["label"] for s in sources]
    selected = forms.SelectFromList.show(
        labels,
        title="IFC Estrutural - Escolha o IFC para gerar",
        multiselect=False,
        button_name="Gerar",
    )
    if not selected:
        return None
    for src in sources:
        if src["label"] == selected:
            return src
    return None


def transform_multiply(t1, t2):
    if t1 is None:
        return t2
    if t2 is None:
        return t1
    try:
        return t1.Multiply(t2)
    except Exception:
        return t1


def transform_solid(solid, transform):
    if solid is None:
        return None
    try:
        if transform is None or transform.IsIdentity:
            return solid
    except Exception:
        pass
    try:
        return DB.SolidUtils.CreateTransformed(solid, transform)
    except Exception:
        return solid


def bbox_from_solid(solid):
    if solid is None:
        return None
    try:
        if solid.Volume <= 1e-9:
            return None
    except Exception:
        return None
    bb = solid.GetBoundingBox()
    if bb is None:
        return None
    return bb.Min, bb.Max


def dims_m(min_pt, max_pt):
    return (
        abs(max_pt.X - min_pt.X) * FT_TO_M,
        abs(max_pt.Y - min_pt.Y) * FT_TO_M,
        abs(max_pt.Z - min_pt.Z) * FT_TO_M,
    )


def classify(dx, dy, dz):
    if dx < 0.05 or dy < 0.05 or dz < 0.05:
        return "ignore"

    hmax = max(dx, dy)
    hmin = min(dx, dy)

    if dz >= (hmax * 1.6) and hmax <= 1.2:
        return "column"
    if hmax >= (dz * 1.6) and dz <= 1.2 and hmin >= 0.08:
        return "beam"
    if dz <= 0.45 and hmax >= 1.2 and hmin >= 0.6:
        return "slab"
    return "other"


def parse_ifc_kind(element):
    texts = []
    try:
        texts.append((element.Name or "").lower())
    except Exception:
        pass
    try:
        if element.Category is not None:
            texts.append((element.Category.Name or "").lower())
    except Exception:
        pass

    joined = " | ".join(texts)
    if "ifccolumn" in joined or "column" in joined or "pilar" in joined:
        return "column"
    if "ifcbeam" in joined or "beam" in joined or "viga" in joined:
        return "beam"
    if "ifcslab" in joined or "slab" in joined or "laje" in joined:
        return "slab"
    return None


def collect_from_geom_obj(geom_obj, current_t, data, elem_kind):
    if isinstance(geom_obj, DB.Solid):
        ts = transform_solid(geom_obj, current_t)
        bb = bbox_from_solid(ts)
        if bb is None:
            return
        min_pt, max_pt = bb
        dx, dy, dz = dims_m(min_pt, max_pt)
        kind = elem_kind if elem_kind in ("column", "beam", "slab") else classify(dx, dy, dz)

        data["solid"] += 1
        data[kind] += 1
        if kind in ("column", "beam", "slab"):
            data["items"].append(
                {
                    "kind": kind,
                    "shape": ts,
                    "min_pt": min_pt,
                    "max_pt": max_pt,
                    "dx": dx,
                    "dy": dy,
                    "dz": dz,
                }
            )
        return

    if isinstance(geom_obj, DB.Mesh):
        data["mesh"] += 1
        return

    if isinstance(geom_obj, DB.GeometryInstance):
        inst_t = None
        try:
            inst_t = geom_obj.Transform
        except Exception:
            inst_t = None
        next_t = transform_multiply(current_t, inst_t)

        sub_geo = None
        try:
            sub_geo = geom_obj.GetSymbolGeometry()
        except Exception:
            try:
                sub_geo = geom_obj.GetInstanceGeometry()
            except Exception:
                sub_geo = None
        if sub_geo is None:
            return

        for sub_item in sub_geo:
            collect_from_geom_obj(sub_item, next_t, data, elem_kind)
        return

    if isinstance(geom_obj, DB.GeometryElement):
        for sub_item in geom_obj:
            collect_from_geom_obj(sub_item, current_t, data, elem_kind)


def dedup_items(items):
    # Mantem todos para evitar perda de elementos validos em IFCs que segmentam
    # vigas/pilares em multiplos solids.
    return list(items)


def find_previous_generated_directshapes(document):
    ids = []
    ds_collector = DB.FilteredElementCollector(document).OfClass(DB.DirectShape)
    for ds in ds_collector:
        try:
            comment_param = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if comment_param is not None:
                txt = comment_param.AsString() or ""
                if txt == "IfcEstruturalAutoGeneric":
                    ids.append(ds.Id)
                    continue
        except Exception:
            pass

        try:
            app_id = ds.ApplicationId or ""
            if app_id == "IfcEstruturalGeneric":
                ids.append(ds.Id)
        except Exception:
            pass
    return ids


def category_for_kind(kind):
    if kind == "column":
        return DB.BuiltInCategory.OST_StructuralColumns
    if kind == "beam":
        return DB.BuiltInCategory.OST_StructuralFraming
    if kind == "slab":
        return DB.BuiltInCategory.OST_Floors
    return None


def create_generic_elements(document, items):
    created = {"column": 0, "beam": 0, "slab": 0, "removed": 0}
    if not items:
        return created

    with revit.Transaction("IFC Estrutural - Gerar Genericos"):
        old_ids = find_previous_generated_directshapes(document)
        if old_ids:
            created["removed"] = len(old_ids)
            for eid in old_ids:
                try:
                    document.Delete(eid)
                except Exception:
                    continue

        for it in items:
            try:
                bic = category_for_kind(it["kind"])
                if bic is None:
                    continue
                shape = it.get("shape")
                if shape is None:
                    continue

                ds = DB.DirectShape.CreateElement(document, DB.ElementId(bic))
                try:
                    ds.ApplicationId = "IfcEstruturalGeneric"
                    ds.ApplicationDataId = "{}_{:.3f}_{:.3f}_{:.3f}".format(
                        it["kind"], it["dx"], it["dy"], it["dz"]
                    )
                except Exception:
                    pass

                try:
                    cmt = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                    if cmt is not None and not cmt.IsReadOnly:
                        cmt.Set("IfcEstruturalAutoGeneric")
                except Exception:
                    pass

                ds.SetShape([shape])
                created[it["kind"]] += 1
            except Exception:
                continue

    return created


def analyze_and_generate(source):
    data = {
        "column": 0,
        "beam": 0,
        "slab": 0,
        "other": 0,
        "ignore": 0,
        "solid": 0,
        "mesh": 0,
        "items": [],
    }
    opt = get_geometry_options()
    root_t = source.get("root_transform", DB.Transform.Identity)

    for elem in source["elements"]:
        elem_kind = parse_ifc_kind(elem)
        try:
            geo = elem.get_Geometry(opt)
        except Exception:
            geo = None
        if geo is None:
            continue
        for g in geo:
            collect_from_geom_obj(g, root_t, data, elem_kind)

    items = [i for i in data["items"] if i["kind"] in ("column", "beam", "slab")]
    items = dedup_items(items)
    created = create_generic_elements(doc, items)

    msg = []
    msg.append("Modelagem generica concluida.")
    msg.append("")
    msg.append("Fonte IFC: {}".format(source["label"]))
    msg.append("Solids lidos: {}".format(data["solid"]))
    msg.append("Meshes lidos: {}".format(data["mesh"]))
    msg.append("")
    msg.append("Detectados: Pilares {} | Vigas {} | Lajes {}".format(data["column"], data["beam"], data["slab"]))
    msg.append("Gerados:   Pilares {} | Vigas {} | Lajes {}".format(created["column"], created["beam"], created["slab"]))
    msg.append("Elementos anteriores removidos: {}".format(created["removed"]))
    forms.alert("\n".join(msg), title="IFC Estrutural", warn_icon=False)


def main():
    sources = build_sources(doc)
    if not sources:
        forms.alert("Nao encontrei fonte IFC para gerar.", title="IFC Estrutural")
        return

    selected = choose_source(sources)
    if selected is None:
        forms.alert("Operacao cancelada.", title="IFC Estrutural")
        return

    analyze_and_generate(selected)


try:
    main()
except Exception as ex:
    forms.alert(
        "Erro inesperado no IFC Estrutural:\n{}\n\nDetalhe tecnico:\n{}".format(
            str(ex), traceback.format_exc()
        ),
        title="IFC Estrutural",
    )
