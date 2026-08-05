# -*- coding: utf-8 -*-
"""IFC Estrutural - Plugin simples e funcional"""

import os
from pyrevit import forms, revit

doc = revit.doc

# Pedir arquivo IFC
ifc_path = forms.pick_file(file_ext="ifc")

if not ifc_path:
    forms.alert("Nenhum arquivo selecionado.", title="Cancelado")
else:
    if not os.path.exists(ifc_path):
        forms.alert("Arquivo não encontrado: {}".format(ifc_path), title="Erro")
    else:
        filename = os.path.basename(ifc_path)
        forms.alert(
            "Arquivo selecionado:\n\n{}".format(filename),
            title="OK - IFC Carregado"
        )
