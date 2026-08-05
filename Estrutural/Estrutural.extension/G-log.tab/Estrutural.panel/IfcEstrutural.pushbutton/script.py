# -*- coding: utf-8 -*-
"""IFC Estrutural - Detecta links IFC no Revit"""

import os
from pyrevit import DB, forms, revit

doc = revit.doc

# Encontrar arquivos IFC vinculados
ifc_links = []

try:
    collector = DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance)
    
    for link in collector:
        try:
            link_doc = link.GetLinkDocument()
            if link_doc:
                path = link_doc.PathName
                if path and path.lower().endswith('.ifc'):
                    ifc_links.append({
                        'name': os.path.basename(path),
                        'path': path,
                        'link': link
                    })
        except:
            pass
except:
    pass

# Resultado
if not ifc_links:
    forms.alert(
        "Nenhum arquivo IFC vinculado encontrado no modelo.\n\n"
        "Você precisa vincular um arquivo .ifc no Revit.",
        title="Sem links IFC"
    )
else:
    if len(ifc_links) == 1:
        ifc = ifc_links[0]
        msg = "IFC encontrado:\n\n{}".format(ifc['name'])
    else:
        # Múltiplos IFC - deixar escolher
        names = [ifc['name'] for ifc in ifc_links]
        idx = forms.SelectFromList.show(names, title="Selecione o IFC")
        
        if idx is None or idx < 0:
            forms.alert("Cancelado.", title="Info")
        else:
            ifc = ifc_links[idx]
            msg = "IFC selecionado:\n\n{}".format(ifc['name'])
    
    forms.alert(msg, title="OK - Pronto para processar")
