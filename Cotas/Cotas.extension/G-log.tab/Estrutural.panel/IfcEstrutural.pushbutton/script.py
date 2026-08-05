# -*- coding: utf-8 -*-
"""Extrai solids da geometria importada e classifica"""

from pyrevit import DB, forms, revit

doc = revit.doc

columns = []
beams = []
slabs = []

# Extrair todos os solids de todos os ImportInstance
for imp in DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance):
    try:
        geo = imp.get_Geometry(DB.Options())
        if not geo:
            continue
            
        for item in geo:
            # GeometryInstance - contém os solids
            if isinstance(item, DB.GeometryInstance):
                inst_geo = item.GetInstanceGeometry()
                
                for solid in inst_geo:
                    if isinstance(solid, DB.Solid):
                        try:
                            # Extrair dimensões do bounding box
                            bb = solid.GetBoundingBox()
                            min_pt = bb.Min
                            max_pt = bb.Max
                            
                            width = max_pt.X - min_pt.X
                            depth = max_pt.Y - min_pt.Y
                            height = max_pt.Z - min_pt.Z
                            
                            center_z = (min_pt.Z + max_pt.Z) / 2
                            
                            # Classificar por proporções
                            min_dim = min(width, depth)
                            
                            if height > depth and height > width:
                                # Pilar (alto e fino)
                                if min_dim < 2:  # Menos de 2 pés (~60cm)
                                    columns.append({
                                        'width': width,
                                        'depth': depth,
                                        'height': height,
                                        'center_z': center_z,
                                        'solid': solid
                                    })
                            elif height < 1 and (width > 3 or depth > 3):
                                # Laje (baixa e comprida)
                                slabs.append(solid)
                            else:
                                # Viga (média altura, comprida)
                                beams.append({
                                    'width': width,
                                    'depth': depth,
                                    'height': height,
                                    'center_z': center_z,
                                    'solid': solid
                                })
                        except:
                            pass
            
            # Direct Solid
            elif isinstance(item, DB.Solid):
                try:
                    bb = item.GetBoundingBox()
                    min_pt = bb.Min
                    max_pt = bb.Max
                    
                    height = max_pt.Z - min_pt.Z
                    width = max_pt.X - min_pt.X
                    depth = max_pt.Y - min_pt.Y
                    
                    if height > depth and height > width:
                        columns.append({'width': width, 'depth': depth, 'height': height})
                    else:
                        beams.append({'width': width, 'depth': depth, 'height': height})
                except:
                    pass
    except:
        pass

# Resultado
msg = "ESTRUTURA EXTRAÍDA:\n\n"
msg += "Pilares encontrados: {}\n".format(len(columns))
msg += "Vigas encontradas: {}\n".format(len(beams))
msg += "Lajes encontradas: {}\n\n".format(len(slabs))

if columns:
    msg += "PILARES:\n"
    for i, col in enumerate(columns[:5]):
        msg += "  [{}] {:.2f}x{:.2f}x{:.2f}m\n".format(
            i, col['width'], col['depth'], col['height']
        )
    if len(columns) > 5:
        msg += "  ... e mais {} pilares\n".format(len(columns) - 5)

if beams:
    msg += "\nVIGAS:\n"
    for i, beam in enumerate(beams[:5]):
        msg += "  [{}] {:.2f}x{:.2f}x{:.2f}m\n".format(
            i, beam['width'], beam['depth'], beam['height']
        )
    if len(beams) > 5:
        msg += "  ... e mais {} vigas\n".format(len(beams) - 5)

forms.alert(msg, title="Estrutura Detectada")
