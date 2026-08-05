# -*- coding: utf-8 -*-
"""IFC Estrutural - Trabalha com importação (CAD/IFC importado)"""

from pyrevit import DB, forms, revit

doc = revit.doc

# Buscar todos os ImportInstance (geometria importada)
collector = DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance)
imports = list(collector)

if not imports:
    forms.alert("Nenhuma geometria importada encontrada.", title="Aviso")
else:
    msg = "GEOMETRIA IMPORTADA ENCONTRADA:\n\n"
    msg += "Total: {} elementos\n\n".format(len(imports))
    msg += "Primeiros elementos:\n"
    
    for i, imp in enumerate(imports[:5]):
        try:
            name = imp.Name
            elem_id = imp.Id.IntegerValue
            msg += "- {} (ID: {})\n".format(name, elem_id)
        except:
            pass
    
    msg += "\n--- PRONTO PARA PROCESSAR ---"
    
    forms.alert(msg, title="OK - Estrutura Detectada")
