using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Plumbing;
using RevitHydraulicCalc.Core;
using System;
using System.Collections.Generic;
using System.Linq;

namespace RevitHydraulicCalc.Core
{
    /// <summary>
    /// Responsável por ler o modelo Revit e extrair dados hidráulicos.
    /// </summary>
    public class ModelReader
    {
        private readonly Document _doc;

        public ModelReader(Document doc)
        {
            _doc = doc;
        }

        /// <summary>
        /// Lista todos os sistemas de tubulação do modelo
        /// </summary>
        public List<PipingSystem> GetAllPipingSystems()
        {
            return new FilteredElementCollector(_doc)
                .OfClass(typeof(PipingSystem))
                .Cast<PipingSystem>()
                .Where(ps => ps != null)
                .ToList();
        }

        /// <summary>
        /// Extrai todos os segmentos e nós de um sistema de tubulação
        /// </summary>
        public (List<PipeSegment> segments, List<HydraulicNode> nodes) ExtractSystemNetwork(
            PipingSystem system,
            double defaultFlowLps = 0.5,
            double sourcePressureKPa = 200)
        {
            var segments = new List<PipeSegment>();
            var nodes = new List<HydraulicNode>();
            var nodeMap = new Dictionary<Connector, string>(); // mapeia connector -> nodeId
            var processedConnectors = new HashSet<Connector>();
            int nodeCounter = 0;
            int segCounter = 0;

            // Coleta todos os elementos do sistema
            var systemElements = system.PipingNetwork?.ToList() ?? new List<ElementId>();

            // Primeira passada: identifica todos os conectores e cria nós
            foreach (ElementId elemId in systemElements)
            {
                var element = _doc.GetElement(elemId);
                if (element == null) continue;

                var connectors = GetConnectors(element);
                foreach (var conn in connectors)
                {
                    if (processedConnectors.Contains(conn)) continue;

                    string nodeId = $"NODE_{nodeCounter++}";
                    nodeMap[conn] = nodeId;

                    var node = new HydraulicNode
                    {
                        Id = nodeId,
                        Name = $"{element.Name}_{conn.Id}",
                        ElevationM = conn.Origin.Z * 0.3048, // pés -> metros
                        RevitElementId = element.Id.IntegerValue,
                        SystemName = system.Name,
                        IsOutlet = IsOutletElement(element),
                        MinRequiredPressureKPa = GetMinPressureForElement(element)
                    };

                    nodes.Add(node);
                    processedConnectors.Add(conn);
                }
            }

            // Segunda passada: cria segmentos entre conectores conectados
            processedConnectors.Clear();
            foreach (ElementId elemId in systemElements)
            {
                var element = _doc.GetElement(elemId);
                if (element is not Pipe pipe) continue;

                var pipeConnectors = GetConnectors(pipe).ToList();
                if (pipeConnectors.Count < 2) continue;

                var startConn = pipeConnectors[0];
                var endConn = pipeConnectors[1];

                if (!nodeMap.ContainsKey(startConn) || !nodeMap.ContainsKey(endConn))
                    continue;

                string startNodeId = nodeMap[startConn];
                string endNodeId = nodeMap[endConn];

                // Extrai propriedades do tubo
                double diameterMm = pipe.Diameter * 304.8; // pés internos -> mm
                double lengthM = GetPipeLength(pipe);

                // Tenta obter material do PipeType
                string material = GetPipeMaterial(pipe);
                double cFactor = Engine.HydraulicEngine.GetHazenWilliamsC(material);

                // Tenta obter vazão de parâmetros do Revit
                double flowLps = GetFlowRateFromElement(pipe, defaultFlowLps);

                var segment = new PipeSegment
                {
                    Id = $"SEG_{segCounter++}",
                    Name = pipe.Name ?? $"Tubo_{pipe.Id}",
                    DiameterMm = diameterMm,
                    LengthM = lengthM,
                    Material = material,
                    HazenWilliamsC = cFactor,
                    FlowRateLps = flowLps,
                    StartElevationM = startConn.Origin.Z * 0.3048,
                    EndElevationM = endConn.Origin.Z * 0.3048,
                    StartNodeId = startNodeId,
                    EndNodeId = endNodeId,
                    ElementId = pipe.Id.IntegerValue
                };

                // Adiciona acessórios conectados ao tubo
                segment.Fittings = GetFittingsForPipe(pipe, diameterMm);

                segments.Add(segment);

                // Atualiza conexões dos nós
                var startNode = nodes.First(n => n.Id == startNodeId);
                var endNode = nodes.First(n => n.Id == endNodeId);
                startNode.ConnectedSegmentIds.Add(segment.Id);
                endNode.ConnectedSegmentIds.Add(segment.Id);
            }

            // Identifica nó de origem (barrilete / ponto mais alto com maior pressão)
            // Por padrão, usa o nó com maior elevação como origem (simplificação)
            // Em versão futura, permite usuário selecionar
            var sourceNode = nodes.OrderByDescending(n => n.ElevationM).FirstOrDefault();
            if (sourceNode != null)
            {
                sourceNode.IsSource = true;
                sourceNode.PressureKPa = sourcePressureKPa;
            }

            return (segments, nodes);
        }

        /// <summary>
        /// Obtém todos os conectores de um elemento
        /// </summary>
        private static IEnumerable<Connector> GetConnectors(Element element)
        {
            if (element == null) yield break;

            var conSet = element switch
            {
                MEPCurve mep => mep.ConnectorManager?.Connectors,
                FamilyInstance fi => fi.MEPModel?.ConnectorManager?.Connectors,
                _ => null
            };

            if (conSet == null) yield break;

            foreach (Connector c in conSet)
            {
                if (c != null) yield return c;
            }
        }

        /// <summary>
        /// Calcula comprimento do tubo pela curva
        /// </summary>
        private static double GetPipeLength(Pipe pipe)
        {
            if (pipe.Location is LocationCurve locCurve)
            {
                return locCurve.Curve.Length * 0.3048; // pés -> metros
            }
            return 1.0; // fallback
        }

        /// <summary>
        /// Tenta obter material do tipo de tubo
        /// </summary>
        private static string GetPipeMaterial(Pipe pipe)
        {
            try
            {
                var pipeType = pipe.PipeType;
                if (pipeType != null)
                {
                    var materialParam = pipeType.get_Parameter(BuiltInParameter.RBS_PIPE_MATERIAL_PARAM);
                    if (materialParam != null && materialParam.HasValue)
                    {
                        var materialId = materialParam.AsElementId();
                        if (materialId != null && materialId != ElementId.InvalidElementId)
                        {
                            var material = pipe.Document.GetElement(materialId) as Material;
                            if (material != null)
                                return material.Name;
                        }
                    }
                    return pipeType.Name;
                }
            }
            catch { }
            return "Aço Galvanizado";
        }

        /// <summary>
        /// Tenta obter vazão de parâmetros do elemento
        /// </summary>
        private static double GetFlowRateFromElement(Element element, double defaultValue)
        {
            try
            {
                // Tenta parâmetros comuns de vazão
                var paramNames = new[] { "Flow", "Vazão", "Flow Rate", "Taxa de Vazão", 
                                         "RBS_PIPE_FLOW_PARAM", "RBS_FLOW_PARAM" };

                foreach (var paramName in paramNames)
                {
                    var param = element.LookupParameter(paramName);
                    if (param != null && param.HasValue)
                    {
                        double val = param.AsDouble();
                        // Revit usa pés cúbicos/segundo internamente
                        // 1 ft³/s = 28.3168 L/s
                        return val * 28.3168;
                    }
                }

                // Tenta BuiltInParameter
                var bip = element.get_Parameter(BuiltInParameter.RBS_PIPE_FLOW_PARAM);
                if (bip != null && bip.HasValue)
                {
                    return bip.AsDouble() * 28.3168;
                }
            }
            catch { }

            return defaultValue;
        }

        /// <summary>
        /// Verifica se o elemento é um ponto de saída (torneira, chuveiro, etc.)
        /// </summary>
        private static bool IsOutletElement(Element element)
        {
            if (element is not FamilyInstance fi) return false;

            var category = fi.Category?.Id.IntegerValue;
            var familyName = fi.Symbol?.FamilyName?.ToLower() ?? "";

            // Categorias típicas de pontos de uso
            var outletCategories = new[]
            {
                (int)BuiltInCategory.OST_PlumbingFixtures,
                (int)BuiltInCategory.OST_MechanicalEquipment,
                (int)BuiltInCategory.OST_SpecialityEquipment
            };

            if (outletCategories.Contains(category))
                return true;

            // Nomes comuns de famílias de saída
            var outletNames = new[] { "torneira", "chuveiro", "lavatorio", "vaso", "bidê", 
                                      "pia", "tanque", "ducha", "bica", "saída", "saida" };
            return outletNames.Any(name => familyName.Contains(name));
        }

        /// <summary>
        /// Retorna pressão mínima requerida por tipo de ponto de uso (kPa)
        /// </summary>
        private static double GetMinPressureForElement(Element element)
        {
            if (element is not FamilyInstance fi) return 100;

            var familyName = fi.Symbol?.FamilyName?.ToLower() ?? "";

            if (familyName.Contains("chuveiro") || familyName.Contains("ducha"))
                return 100; // mínimo 10 mca para chuveiro
            if (familyName.Contains("torneira") || familyName.Contains("lavatorio"))
                return 50;  // mínimo 5 mca para torneira
            if (familyName.Contains("vaso") || familyName.Contains("bacia"))
                return 70;  // mínimo 7 mca para vaso

            return 100; // padrão
        }

        /// <summary>
        /// Obtém acessórios conectados a um tubo
        /// </summary>
        private static List<Fitting> GetFittingsForPipe(Pipe pipe, double diameterMm)
        {
            var fittings = new List<Fitting>();

            try
            {
                var connectors = GetConnectors(pipe).ToList();
                foreach (var conn in connectors)
                {
                    if (!conn.IsConnected) continue;

                    foreach (Connector refConn in conn.AllRefs)
                    {
                        if (refConn?.Owner == null || refConn.Owner.Id == pipe.Id)
                            continue;

                        var owner = refConn.Owner;
                        string fittingType = owner.Category?.Name ?? "Acessório";

                        // Tenta identificar tipo de acessório
                        if (owner is FamilyInstance fi)
                        {
                            fittingType = fi.Symbol?.FamilyName ?? fittingType;
                        }
                        else if (owner is PipeFitting pf)
                        {
                            fittingType = pf.Name ?? "Conexão";
                        }

                        double eqLength = Engine.HydraulicEngine.GetEquivalentLength(fittingType, diameterMm);

                        fittings.Add(new Fitting
                        {
                            Type = fittingType,
                            EquivalentLengthM = eqLength,
                            Quantity = 1
                        });
                    }
                }
            }
            catch { }

            return fittings;
        }

        /// <summary>
        /// Encontra o nó de origem com base na seleção do usuário
        /// </summary>
        public static string? FindSourceNodeByElement(
            List<HydraulicNode> nodes, 
            ElementId selectedElementId)
        {
            var node = nodes.FirstOrDefault(n => n.RevitElementId == selectedElementId.IntegerValue);
            return node?.Id;
        }
    }
}
